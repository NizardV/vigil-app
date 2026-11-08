from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from core.notifications import NotificationError

from . import notifications
from .models import CheckResult, Finding
from .outcomes import OK
from .services import record_outcome
from .tests import outcome

HOOK = "https://discord.com/api/webhooks/1/token"


class Sender:
    """Faux envoi Discord : mémorise les messages, ou échoue sur demande."""

    def __init__(self, fail=False):
        self.fail, self.sent = fail, []

    def __call__(self, url, payload):
        if self.fail:
            raise NotificationError("Discord a répondu 500")
        self.sent.append((url, payload))


def make_finding(sev=Finding.Severity.HIGH, key="1", status=Finding.Status.OPEN, title="faille", **extra):
    return Finding.objects.create(
        fingerprint=f"fp-{key}",
        source="github",
        target="NizardV/demo",
        check_name="dependabot",
        severity=sev,
        title=title,
        url="https://github.com/x",
        status=status,
        **extra,
    )


@override_settings(DISCORD_WEBHOOKS={"security-alerts": HOOK, "security-digest": HOOK})
class AlertTests(TestCase):
    def test_alerte_une_seule_fois(self):
        make_finding()
        sender = Sender()
        self.assertEqual(notifications.notify_new_findings(send=sender)["status"], "sent")
        self.assertEqual(notifications.notify_new_findings(send=sender)["status"], "nothing")
        self.assertEqual(len(sender.sent), 1)
        self.assertIsNotNone(Finding.objects.get().notified_at)

    def test_seuls_critique_et_haut_ouverts_sont_notifies(self):
        make_finding(Finding.Severity.CRITICAL, "a")
        make_finding(Finding.Severity.MEDIUM, "b")
        make_finding(Finding.Severity.HIGH, "c", status=Finding.Status.RESOLVED)
        make_finding(Finding.Severity.HIGH, "d", status=Finding.Status.IGNORED)
        self.assertEqual(notifications.notify_new_findings(send=Sender())["count"], 1)

    def test_echec_d_envoi_ne_marque_rien_et_reessaie_ensuite(self):
        make_finding()
        result = notifications.notify_new_findings(send=Sender(fail=True))
        self.assertEqual(result["status"], "error")
        self.assertIsNone(Finding.objects.get().notified_at)  # sinon l'alerte serait perdue
        self.assertEqual(notifications.notify_new_findings(send=Sender())["status"], "sent")

    @override_settings(DISCORD_WEBHOOKS={})
    def test_webhook_absent_ne_marque_rien(self):
        make_finding()
        sender = Sender()
        self.assertEqual(notifications.notify_new_findings(send=sender)["status"], "not_configured")
        self.assertEqual((sender.sent, Finding.objects.get().notified_at), ([], None))

    def test_dry_run_n_envoie_ni_ne_marque(self):
        make_finding()
        sender = Sender()
        result = notifications.notify_new_findings(send=sender, dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual((sender.sent, Finding.objects.get().notified_at), ([], None))

    def test_constat_rouvert_est_renotifie(self):
        record_outcome(outcome(severity=3))
        notifications.notify_new_findings(send=Sender())
        record_outcome(outcome(status=OK))  # résolu
        record_outcome(outcome(severity=3))  # revient
        self.assertEqual(notifications.notify_new_findings(send=Sender())["status"], "sent")

    def test_constat_aggrave_est_renotifie_mais_pas_un_constat_stable(self):
        record_outcome(outcome(severity=3))
        notifications.notify_new_findings(send=Sender())
        record_outcome(outcome(severity=3))  # inchangé
        self.assertEqual(notifications.notify_new_findings(send=Sender())["status"], "nothing")
        record_outcome(outcome(severity=4))  # haut -> critique
        self.assertEqual(notifications.notify_new_findings(send=Sender())["status"], "sent")

    def test_beaucoup_de_constats_un_seul_message_et_tous_marques(self):
        for i in range(40):
            make_finding(key=str(i), title=f"faille {i}")
        sender = Sender()
        result = notifications.notify_new_findings(send=sender)
        self.assertEqual((result["count"], len(sender.sent)), (40, 1))  # pas de déluge de messages
        description = sender.sent[0][1]["embeds"][0]["description"]
        self.assertIn("30", description)  # « … et 30 autre(s) »
        self.assertLessEqual(len(description), 4096)
        self.assertEqual(Finding.objects.filter(notified_at__isnull=True).count(), 0)  # pas de répétition demain

    def test_contenu_externe_neutralise(self):
        make_finding(title="@everyone **urgent** [clique](http://evil.example)")
        sender = Sender()
        notifications.notify_new_findings(send=sender)
        description = sender.sent[0][1]["embeds"][0]["description"]
        self.assertNotIn("@everyone", description)
        self.assertNotIn("](http://evil", description)


@override_settings(DISCORD_WEBHOOKS={"security-alerts": HOOK, "security-digest": HOOK})
class DigestTests(TestCase):
    def test_resume_signale_un_audit_perime(self):
        payload = notifications.digest_payload()  # aucun audit enregistré
        self.assertIn("n'a pas tourné", payload["embeds"][0]["description"])

    def test_resume_normal_avec_audit_recent(self):
        record_outcome(outcome())
        make_finding(Finding.Severity.CRITICAL, "z", title="RCE")
        description = notifications.digest_payload()["embeds"][0]["description"]
        self.assertNotIn("n'a pas tourné", description)
        self.assertIn("Dernier audit", description)
        self.assertIn("RCE", description)

    def test_audit_ancien_est_signale(self):
        record_outcome(outcome())
        CheckResult.objects.update(checked_at=timezone.now() - notifications.STALE_AUDIT * 2)
        self.assertIn("n'a pas tourné", notifications.digest_payload()["embeds"][0]["description"])

    def test_resume_liste_les_pr_qui_traînent(self):
        for n in range(3):
            Finding.objects.create(
                fingerprint=f"s{n}",
                source="github",
                target="NizardV/cyna-web",
                check_name="stale_dependabot_prs",
                severity=Finding.Severity.MEDIUM,
                title="PR",
                status=Finding.Status.OPEN,
            )
        record_outcome(outcome())
        self.assertIn("3 PR en attente", notifications.digest_payload()["embeds"][0]["description"])

    def test_envoi_du_resume_et_erreur(self):
        self.assertEqual(notifications.send_digest(send=Sender())["status"], "sent")
        self.assertEqual(notifications.send_digest(send=Sender(fail=True))["status"], "error")


@override_settings(DISCORD_WEBHOOKS={"security-alerts": HOOK, "security-digest": HOOK})
class CommandTests(TestCase):
    def run_command(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("notify_security", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_affichable_sur_une_console_windows(self):
        """Régression : le message contient des emojis, la console cp1252 plantait à l'affichage."""
        make_finding()
        output = self.run_command("--dry-run")
        output.encode("cp1252")  # ne doit pas lever UnicodeEncodeError
        self.assertEqual(Finding.objects.get().notified_at, None)  # et ne marque rien

    def test_dry_run_du_resume(self):
        self.run_command("--digest", "--dry-run").encode("cp1252")

    def test_rien_a_notifier(self):
        self.assertIn("Aucun nouveau constat", self.run_command())

    def test_echec_d_envoi_fait_echouer_la_commande(self):
        from django.core.management.base import CommandError

        make_finding()
        failed = {"status": "error", "count": 1, "error": "Discord a répondu 500"}
        with mock.patch("modules.security.notifications.notify_new_findings", return_value=failed):
            with self.assertRaises(CommandError):
                self.run_command()


class NotificationTaskTests(TestCase):
    def test_taches_enregistrees(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        self.assertIn("security.audit_github", app.tasks)
        self.assertIn("security.weekly_digest", app.tasks)

    def test_alerte_non_envoyee_fait_echouer_la_tache_pour_qu_elle_soit_reessayee(self):
        from . import tasks

        failed = {"status": "error", "error": "x"}
        with (
            mock.patch("modules.security.tasks.services.run_github_audit", return_value={}),
            mock.patch("modules.security.tasks.notifications.notify_new_findings", return_value=failed),
            self.assertRaises(RuntimeError),
        ):
            tasks.audit_github(timestamp=0)


@override_settings(DISCORD_WEBHOOKS={"security-alerts": HOOK, "security-digest": HOOK})
class GroupingTests(TestCase):
    def test_doublons_regroupes_avec_un_multiplicateur(self):
        for key in ("1", "2"):  # même faille dans deux fichiers de dépendances
            make_finding(Finding.Severity.CRITICAL, key, title="proxy-addr : usurpation d'IP")
        make_finding(Finding.Severity.CRITICAL, "3", title="autre faille")
        sender = Sender()
        notifications.notify_new_findings(send=sender)
        description = sender.sent[0][1]["embeds"][0]["description"]
        self.assertIn("proxy-addr : usurpation d'IP", description)
        self.assertIn("×2", description)
        self.assertEqual(description.count("proxy-addr"), 1)  # une seule ligne pour les deux
        self.assertEqual(description.count("×"), 1)  # « autre faille » n'a pas de multiplicateur

    def test_cibles_ou_gravites_differentes_ne_sont_pas_regroupees(self):
        make_finding(Finding.Severity.HIGH, "1", title="même titre")
        Finding.objects.create(
            fingerprint="fp-b", source="github", target="NizardV/autre", check_name="dependabot",
            severity=Finding.Severity.HIGH, title="même titre", status=Finding.Status.OPEN,
        )  # fmt: skip
        make_finding(Finding.Severity.CRITICAL, "3", title="même titre")
        sender = Sender()
        notifications.notify_new_findings(send=sender)
        self.assertNotIn("×", sender.sent[0][1]["embeds"][0]["description"])

    def test_les_constats_caches_sont_comptes_en_constats_reels(self):
        """12 titres distincts dont un en ×3 : 13 constats, 10 lignes affichées => « 4 autres »."""
        for i in range(11):
            make_finding(key=f"t{i}", title=f"titre {i}")
        for j in range(3):
            make_finding(key=f"d{j}", title="titre dupliqué")  # 1 ligne, 3 constats (ordre alphabétique : en queue)
        sender = Sender()
        notifications.notify_new_findings(send=sender)
        description = sender.sent[0][1]["embeds"][0]["description"]
        self.assertIn("**4** autre(s) constat(s)", description)  # 14 constats - 10 lignes affichées (10 constats)

    def test_marquage_notifie_concerne_tous_les_doublons(self):
        for key in ("1", "2", "3"):
            make_finding(key=key, title="même faille")
        notifications.notify_new_findings(send=Sender())
        self.assertEqual(Finding.objects.filter(notified_at__isnull=True).count(), 0)

    def test_resume_hebdo_regroupe_aussi(self):
        record_outcome(outcome())  # un audit récent ; fait AVANT car il résout les constats qu'il ne revoit pas
        for key in ("1", "2"):
            make_finding(Finding.Severity.CRITICAL, key, title="faille répétée")
        description = notifications.digest_payload()["embeds"][0]["description"]
        self.assertIn("faille répétée", description)
        self.assertIn("×2", description)

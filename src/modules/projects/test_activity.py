from datetime import UTC, datetime, timedelta

from django.test import TestCase, override_settings

from core.github import GitHubError

from . import notifications
from .activity import ActivityReport, RepoActivity, collect_activity, is_noise, repo_activity
from .models import TrackedRepo

NOW = datetime.now(UTC)
SINCE = NOW - timedelta(days=7)
HOOK = "https://discord.com/api/webhooks/1/token"


def commit(message="fix: bug", login="nizard", kind="User", parents=1):
    return {
        "sha": "abc",
        "commit": {"message": message},
        "author": {"login": login, "type": kind} if login else None,
        "parents": [{}] * parents,
    }


def pr(title, merged_days_ago=1, login="nizard"):
    merged = None if merged_days_ago is None else (NOW - timedelta(days=merged_days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"title": title, "merged_at": merged, "user": {"login": login}}


class FakeClient:
    """Client GitHub factice : `data[repo]` = (commits, pulls) ou une exception."""

    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, path, params=None):
        self.calls.append((path, params))
        repo = path.split("/commits")[0].split("/pulls")[0].removeprefix("/repos/")
        answer = self.data[repo]
        if isinstance(answer, Exception):
            raise answer
        return answer[0] if path.endswith("/commits") else answer[1]


class NoiseTests(TestCase):
    def test_activite_humaine_conservee(self):
        self.assertFalse(is_noise(commit("feat: ajoute le filtre")))

    def test_bots_ecartes(self):
        self.assertTrue(is_noise(commit(login="github-actions[bot]", kind="Bot")))
        self.assertTrue(is_noise(commit(login="dependabot[bot]", kind="User")))  # repérés par le login aussi

    def test_deploiement_automatique_ecarte_sans_tenir_compte_de_la_casse(self):
        self.assertTrue(is_noise(commit("Deploy to GitHub pages")))
        self.assertTrue(is_noise(commit("deploy TO github PAGES")))

    def test_commit_de_fusion_ecarte(self):
        self.assertTrue(is_noise(commit("Merge pull request #4", parents=2)))

    def test_auteur_inconnu_n_est_pas_un_bot(self):
        self.assertFalse(is_noise(commit(login=None)))  # compte supprimé : `author` est null


class RepoActivityTests(TestCase):
    def test_depot_de_profil_avec_seulement_des_deploiements_n_est_pas_actif(self):
        """Le cas réel : « 1 commit(s) sur output — Deploy to GitHub pages », chaque jour."""
        client = FakeClient({"NizardV/NizardV": ([commit("Deploy to GitHub pages", "github-actions[bot]", "Bot")], [])})
        self.assertFalse(repo_activity(client, "NizardV/NizardV", SINCE).is_active)

    def test_seule_la_branche_par_defaut_est_lue(self):
        client = FakeClient({"a/b": ([], [])})
        repo_activity(client, "a/b", SINCE)
        commits_call = next(p for path, p in client.calls if path.endswith("/commits"))
        self.assertNotIn("sha", commits_call)  # sans `sha`, GitHub ne renvoie que la branche par défaut

    def test_pr_fusionnees_et_mises_a_jour_de_dependances_separees(self):
        pulls = [
            pr("Ajoute l'export"),
            pr("Ancienne PR", merged_days_ago=30),  # hors période
            pr("Fermée sans fusion", merged_days_ago=None),
            pr("bump x", login="dependabot[bot]"),
            pr("bump y", login="dependabot[bot]"),
        ]
        activity = repo_activity(FakeClient({"a/b": ([commit("feat: a"), commit("fix: b")], pulls)}), "a/b", SINCE)
        self.assertEqual(activity.commits, ["feat: a", "fix: b"])
        self.assertEqual(activity.merged_prs, ["Ajoute l'export"])
        self.assertEqual(activity.dependency_updates, 2)

    def test_seule_la_premiere_ligne_du_message_est_gardee(self):
        activity = repo_activity(
            FakeClient({"a/b": ([commit("feat: titre\n\ncorps long\nsecret=1")], [])}), "a/b", SINCE
        )
        self.assertEqual(activity.commits, ["feat: titre"])


class CollectTests(TestCase):
    def test_depot_illisible_est_non_verifie_pas_inactif(self):
        client = FakeClient({"a/ok": ([commit()], []), "a/ko": GitHubError(403, "denied"), "a/vide": ([], [])})
        report = collect_activity(client, ["a/ok", "a/ko", "a/vide"], SINCE)
        self.assertEqual(([e.repo for e in report.entries], report.inactive, report.unchecked), (["a/ok"], 1, ["a/ko"]))

    def test_depot_vide_est_inactif_pas_non_verifie(self):
        """Régression : un dépôt vide (409) n'est pas une erreur, sinon l'avertissement serait permanent."""
        client = FakeClient({"a/vide": GitHubError(409, "Git Repository is empty.")})
        report = collect_activity(client, ["a/vide"], SINCE)
        self.assertEqual((report.inactive, report.unchecked), (1, []))

    def test_autre_conflit_409_reste_non_verifie(self):
        client = FakeClient({"a/x": GitHubError(409, "Conflict")})
        self.assertEqual(collect_activity(client, ["a/x"], SINCE).unchecked, ["a/x"])

    def test_quota_epuise_marque_le_reste_comme_non_verifie(self):
        limited = GitHubError(403, "API rate limit exceeded", rate_limited=True)
        client = FakeClient({"a/1": limited, "a/2": ([commit()], []), "a/3": ([commit()], [])})
        report = collect_activity(client, ["a/1", "a/2", "a/3"], SINCE)
        self.assertEqual((report.entries, report.unchecked), ([], ["a/1", "a/2", "a/3"]))
        self.assertEqual(len(client.calls), 1)  # on n'insiste pas

    def test_noms_suspects_ignores(self):
        client = FakeClient({"a/b": ([commit()], [])})
        report = collect_activity(client, ["a/../etc", "a/b"], SINCE)
        self.assertEqual([e.repo for e in report.entries], ["a/b"])

    def test_tri_par_activite_decroissante(self):
        client = FakeClient({"a/peu": ([commit()], []), "a/beaucoup": ([commit(), commit(), commit()], [])})
        report = collect_activity(client, ["a/peu", "a/beaucoup"], SINCE)
        self.assertEqual([e.repo for e in report.entries], ["a/beaucoup", "a/peu"])


class DigestPayloadTests(TestCase):
    def test_contenu_et_pluriels(self):
        report = ActivityReport(
            entries=[RepoActivity("NizardV/cyna-api", ["feat: a", "fix: b"], ["Ajoute X"], 1)], inactive=12
        )
        text = notifications.digest_payload(report, SINCE, NOW)["embeds"][0]["description"]
        self.assertIn("2 commits · 1 PR fusionnée · 1 mise à jour de dépendance", text)
        self.assertIn("12 projets sans activité", text)

    def test_contenu_externe_neutralise(self):
        report = ActivityReport(entries=[RepoActivity("a/b", ["@everyone **gras** [x](http://evil.example)"])])
        text = notifications.digest_payload(report, SINCE, NOW)["embeds"][0]["description"]
        self.assertNotIn("@everyone", text)
        self.assertNotIn("](http://evil", text)

    def test_non_verifies_signales_et_jamais_confondus_avec_inactifs(self):
        report = ActivityReport(unchecked=["a/ko"])
        embed = notifications.digest_payload(report, SINCE, NOW)["embeds"][0]
        self.assertIn("non vérifié", embed["description"])
        self.assertNotIn("sans activité", embed["description"])
        self.assertEqual(embed["color"], 0xEA580C)

    def test_semaine_calme(self):
        text = notifications.digest_payload(ActivityReport(inactive=5), SINCE, NOW)["embeds"][0]["description"]
        self.assertIn("Aucune activité", text)

    def test_taille_bornee_meme_avec_beaucoup_de_projets(self):
        entries = [RepoActivity(f"a/p{i}", [f"commit {i} " + "x" * 200] * 5) for i in range(60)]
        description = notifications.digest_payload(ActivityReport(entries=entries), SINCE, NOW)["embeds"][0][
            "description"
        ]
        self.assertLessEqual(len(description), 4096)
        self.assertIn("50", description)  # « … et 50 autre(s) projet(s) »


@override_settings(DISCORD_WEBHOOKS={"projects-digest": HOOK})
class SendTests(TestCase):
    def setUp(self):
        TrackedRepo.objects.create(full_name="NizardV/actif")
        TrackedRepo.objects.create(full_name="NizardV/archive", is_archived=True)
        self.client_ = FakeClient({"NizardV/actif": ([commit()], []), "NizardV/archive": ([commit()], [])})
        self.sent = []

    def send(self, url, payload):
        self.sent.append((url, payload))

    def test_envoi_et_depots_archives_exclus(self):
        result = notifications.send_weekly_digest(client=self.client_, send=self.send)
        self.assertEqual((result["status"], result["active"]), ("sent", 1))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(all("archive" not in path for path, _ in self.client_.calls))

    def test_dry_run_n_envoie_rien(self):
        result = notifications.send_weekly_digest(client=self.client_, send=self.send, dry_run=True)
        self.assertEqual((result["status"], self.sent), ("dry_run", []))

    @override_settings(DISCORD_WEBHOOKS={})
    def test_webhook_absent_ne_consomme_pas_le_quota_github(self):
        result = notifications.send_weekly_digest(client=self.client_, send=self.send)
        self.assertEqual((result["status"], self.client_.calls, self.sent), ("not_configured", [], []))

    def test_echec_d_envoi_est_remonte(self):
        from core.notifications import NotificationError

        def failing(url, payload):
            raise NotificationError("Discord a répondu 500")

        self.assertEqual(notifications.send_weekly_digest(client=self.client_, send=failing)["status"], "error")

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from procrastinate.contrib.django import app

from core.notifications import NotificationError

from . import services, tasks
from .models import SentAlert

HOOK = "https://discord.com/api/webhooks/1/token"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)  # un lundi, 12:00 UTC


def make_job(task, status, attempts=1, events=()):
    """Crée une tâche Procrastinate par SQL direct (ses modèles Django sont en lecture seule).

    Un déclencheur crée automatiquement un événement : on le remplace par des événements aux dates voulues.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, status, attempts) "
            "VALUES ('default', %s, %s::procrastinate_job_status, %s) RETURNING id",
            [task, status, attempts],
        )
        job_id = cursor.fetchone()[0]
        cursor.execute("DELETE FROM procrastinate_events WHERE job_id = %s", [job_id])
        for event_type, at in events:
            cursor.execute(
                "INSERT INTO procrastinate_events (job_id, type, at) VALUES (%s, %s::procrastinate_job_event_type, %s)",
                [job_id, event_type, at],
            )
    return job_id


def hours_ago(n):
    return NOW - timedelta(hours=n)


class FailedJobsTests(TestCase):
    def test_une_tache_qui_echoue_toutes_les_20_minutes_ne_donne_qu_une_alerte_par_jour(self):
        """API du modèle en panne : 3 échecs le même jour = 1 seul problème, pas 3 (ni 72)."""
        for minutes in (10, 30, 50):
            make_job(
                "watch.analyze_pending",
                "failed",
                events=[("failed", NOW - timedelta(hours=2) + timedelta(minutes=minutes))],
            )
        (problem,) = services.failed_jobs(NOW)
        self.assertIn("3 échecs", problem.detail)
        self.assertEqual(problem.key, "failed:watch.analyze_pending:20261109")

    def test_le_lendemain_c_est_un_nouveau_probleme(self):
        make_job("watch.analyze_pending", "failed", events=[("failed", NOW - timedelta(hours=2))])
        make_job("watch.analyze_pending", "failed", events=[("failed", NOW - timedelta(days=1, hours=2))])
        self.assertEqual(len(services.failed_jobs(NOW)), 2)  # deux jours différents : deux alertes

    def test_taches_differentes_restent_distinctes(self):
        make_job("a.task", "failed", events=[("failed", hours_ago(1))])
        make_job("b.task", "failed", events=[("failed", hours_ago(1))])
        self.assertEqual({p.task for p in services.failed_jobs(NOW)}, {"a.task", "b.task"})

    def test_echec_recent_signale(self):
        make_job("security.audit_github", "failed", attempts=2, events=[("failed", hours_ago(3))])
        (problem,) = services.failed_jobs(NOW)
        self.assertEqual((problem.kind, problem.task), ("failed", "security.audit_github"))
        self.assertIn("2 tentatives", problem.detail)

    def test_echec_ancien_ignore(self):
        make_job("t.old", "failed", events=[("failed", NOW - services.FAILED_WINDOW - timedelta(days=1))])
        self.assertEqual(services.failed_jobs(NOW), [])

    def test_tache_reussie_ignoree(self):
        make_job("t.ok", "succeeded", events=[("succeeded", hours_ago(1))])
        self.assertEqual(services.failed_jobs(NOW), [])


class StuckJobsTests(TestCase):
    def test_tache_bloquee(self):
        make_job("t.slow", "doing", events=[("started", hours_ago(3))])
        (problem,) = services.stuck_jobs(NOW)
        self.assertEqual(problem.kind, "stuck")
        self.assertIn("180 min", problem.detail)

    def test_tache_en_cours_normale_ignoree(self):
        make_job("t.fast", "doing", events=[("started", NOW - timedelta(minutes=10))])
        self.assertEqual(services.stuck_jobs(NOW), [])


class OverduePeriodicTests(TestCase):
    """Cron interprété en UTC, comme Procrastinate. Le registre réel est remplacé par une tâche factice."""

    def setUp(self):
        self.registry = {("t.daily", ""): SimpleNamespace(cron="0 5 * * *")}  # prévue chaque jour à 05:00 UTC
        patcher = mock.patch.object(app.periodic_registry, "periodic_tasks", self.registry)
        patcher.start()
        self.addCleanup(patcher.stop)

    def history(self):
        """Un système actif depuis plusieurs jours (première activité avant l'heure prévue du jour)."""
        make_job("t.other", "succeeded", events=[("succeeded", NOW - timedelta(days=5))])

    def test_execution_manquee_signalee(self):
        self.history()  # aucune réussite de t.daily depuis 05:00
        (problem,) = services.overdue_periodic(NOW)
        self.assertEqual(problem.kind, "overdue")
        self.assertEqual(problem.key, "overdue:t.daily:202611090500")

    def test_execution_reussie_n_est_pas_signalee(self):
        self.history()
        make_job("t.daily", "succeeded", events=[("succeeded", datetime(2026, 11, 9, 5, 2, tzinfo=UTC))])
        self.assertEqual(services.overdue_periodic(NOW), [])

    def test_reussite_de_la_veille_ne_compte_pas(self):
        self.history()
        make_job("t.daily", "succeeded", events=[("succeeded", datetime(2026, 11, 8, 5, 2, tzinfo=UTC))])
        self.assertEqual(len(services.overdue_periodic(NOW)), 1)

    def test_dans_la_marge_pas_encore_en_retard(self):
        self.history()
        just_after = datetime(2026, 11, 9, 5, 30, tzinfo=UTC)  # 30 min après l'heure prévue, marge de 2 h
        self.assertEqual(services.overdue_periodic(just_after), [])

    def test_horaire_prevu_avant_l_installation_ignore(self):
        """Évite une fausse alerte juste après la mise en route (la tâche n'existait pas à l'heure prévue)."""
        make_job("t.other", "succeeded", events=[("succeeded", datetime(2026, 11, 9, 10, 0, tzinfo=UTC))])
        self.assertEqual(services.overdue_periodic(NOW), [])

    def test_aucune_activite_aucune_alerte(self):
        self.assertEqual(services.overdue_periodic(NOW), [])

    def test_le_cron_est_lu_en_utc(self):
        """Cron en UTC (pas à l'heure de Paris) : à 06:59 on est dans la marge, à 07:30 en retard."""
        self.history()
        self.assertEqual(services.overdue_periodic(datetime(2026, 11, 9, 6, 59, tzinfo=UTC)), [])
        self.assertEqual(len(services.overdue_periodic(datetime(2026, 11, 9, 7, 30, tzinfo=UTC))), 1)


class Sender:
    def __init__(self, fail=False):
        self.fail, self.sent = fail, []

    def __call__(self, url, payload):
        if self.fail:
            raise NotificationError("Discord a répondu 500")
        self.sent.append((url, payload))


@override_settings(DISCORD_WEBHOOKS={"system": HOOK})
class NotifyTests(TestCase):
    def failure(self, name="security.audit_github"):
        return make_job(name, "failed", events=[("failed", hours_ago(1))])

    def check(self, **kwargs):
        return services.check_and_notify(now=NOW, **kwargs)

    def test_signale_une_seule_fois(self):
        self.failure()
        sender = Sender()
        self.assertEqual(self.check(send=sender)["status"], "sent")
        self.assertEqual(self.check(send=sender)["status"], "nothing")
        self.assertEqual(len(sender.sent), 1)
        self.assertEqual(SentAlert.objects.count(), 1)

    def test_nouveau_probleme_apres_un_premier_signalement(self):
        self.failure("a.task")
        sender = Sender()
        self.check(send=sender)
        self.failure("b.task")
        result = self.check(send=sender)
        self.assertEqual((result["status"], result["new"], result["problems"]), ("sent", 1, 2))

    def test_echec_d_envoi_ne_marque_rien_et_reessaie(self):
        self.failure()
        self.assertEqual(self.check(send=Sender(fail=True))["status"], "error")
        self.assertEqual(SentAlert.objects.count(), 0)  # sinon le problème serait perdu
        self.assertEqual(self.check(send=Sender())["status"], "sent")

    @override_settings(DISCORD_WEBHOOKS={})
    def test_webhook_absent_ne_marque_rien(self):
        self.failure()
        sender = Sender()
        self.assertEqual(self.check(send=sender)["status"], "not_configured")
        self.assertEqual((sender.sent, SentAlert.objects.count()), ([], 0))

    def test_dry_run_n_envoie_ni_ne_marque(self):
        self.failure()
        sender = Sender()
        self.assertEqual(self.check(send=sender, dry_run=True)["status"], "dry_run")
        self.assertEqual((sender.sent, SentAlert.objects.count()), ([], 0))

    def test_rien_a_signaler(self):
        self.assertEqual(self.check(send=Sender())["status"], "nothing")

    def test_message_borne_et_neutralise(self):
        for i in range(15):
            self.failure(f"task.{i}")
        make_job("@everyone **x**", "failed", events=[("failed", hours_ago(1))])
        sender = Sender()
        self.check(send=sender)
        embed = sender.sent[0][1]["embeds"][0]
        self.assertLessEqual(len(embed["description"]), 4096)
        self.assertNotIn("@everyone", embed["description"])
        self.assertIn("autre(s)", embed["description"])


@override_settings(VIGIL_HEARTBEAT_URL="https://hc-ping.example/uuid-secret")
class HeartbeatTests(TestCase):
    def test_envoye(self):
        ping = mock.Mock()
        self.assertEqual(services.send_heartbeat(ping_fn=ping), "sent")
        ping.assert_called_once_with("https://hc-ping.example/uuid-secret")

    @override_settings(VIGIL_HEARTBEAT_URL="")
    def test_non_configure(self):
        ping = mock.Mock()
        self.assertEqual(services.send_heartbeat(ping_fn=ping), "not_configured")
        ping.assert_not_called()

    def test_echec_du_signal_de_vie_ne_fait_pas_planter_la_verification(self):
        self.assertEqual(services.send_heartbeat(ping_fn=mock.Mock(side_effect=NotificationError("x"))), "error")


class HealthCheckTaskTests(TestCase):
    def test_tache_enregistree_toutes_les_15_minutes(self):
        app.perform_import_paths()
        self.assertIn("system.health_check", app.tasks)
        periodic = {name: p.cron for (name, _), p in app.periodic_registry.periodic_tasks.items()}
        self.assertEqual(periodic["system.health_check"], "*/15 * * * *")

    def test_signal_de_vie_apres_une_verification_reussie(self):
        with (
            mock.patch.object(services, "check_and_notify", return_value={"status": "nothing"}),
            mock.patch.object(services, "send_heartbeat", return_value="sent") as heartbeat,
        ):
            result = tasks.health_check(timestamp=0)
        heartbeat.assert_called_once()
        self.assertEqual(result["heartbeat"], "sent")

    def test_pas_de_signal_de_vie_si_la_verification_echoue(self):
        """Le silence doit être détecté par le service externe : un faux signal de vie masquerait la panne."""
        with (
            mock.patch.object(services, "check_and_notify", return_value={"status": "error", "error": "x"}),
            mock.patch.object(services, "send_heartbeat") as heartbeat,
            self.assertRaises(RuntimeError),
        ):
            tasks.health_check(timestamp=0)
        heartbeat.assert_not_called()


class CommandTests(TestCase):
    def test_dry_run_affichable_sur_une_console_windows(self):
        from io import StringIO

        from django.core.management import call_command

        make_job("security.audit_github", "failed", events=[("failed", timezone_now_minus_hours(1))])
        out = StringIO()
        call_command("system_check", "--dry-run", stdout=out)
        out.getvalue().encode("cp1252")  # ne doit pas lever UnicodeEncodeError (emojis)
        self.assertEqual(SentAlert.objects.count(), 0)


def timezone_now_minus_hours(n):
    from django.utils import timezone

    return timezone.now() - timedelta(hours=n)

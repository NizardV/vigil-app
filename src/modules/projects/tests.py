from datetime import UTC, datetime, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import TrackedRepo
from .services import sync_repos

RAW = {
    "full_name": "NizardV/demo",
    "description": "d",
    "language": "Python",
    "private": False,
    "archived": False,
    "open_issues_count": 2,
    "pushed_at": "2026-11-01T10:00:00Z",
    "fork": False,
}


class ProjectsTests(TestCase):
    def test_sync_est_idempotent(self):
        sync_repos([RAW])
        sync_repos([RAW])
        self.assertEqual(TrackedRepo.objects.count(), 1)

    def test_verdict_inactif(self):
        old = datetime.now(UTC) - timedelta(days=200)
        repo = TrackedRepo(full_name="x/y", pushed_at=old)
        self.assertEqual(repo.verdict, TrackedRepo.Verdict.A_RELANCER)

    def test_liste_exige_une_connexion(self):
        resp = self.client.get(reverse("repo_list"))
        self.assertEqual(resp.status_code, 302)

    def test_liste_connecte(self):
        User.objects.create_user("n", password="p")
        self.client.login(username="n", password="p")
        self.assertEqual(self.client.get(reverse("repo_list")).status_code, 200)


class SyncCommandTests(TestCase):
    """La commande de management doit appeler le service (régression : mauvais chemin d'import)."""

    def test_commande_synchronise_via_le_service(self):
        from io import StringIO
        from unittest import mock

        from django.core.management import call_command

        out = StringIO()
        with mock.patch("modules.projects.management.commands.sync_repos.run_sync", return_value=3):
            call_command("sync_repos", stdout=out)
        self.assertIn("3 repos", out.getvalue())


class TaskTests(TestCase):
    def test_tache_appelle_le_service(self):
        from unittest import mock

        from . import tasks

        with mock.patch("modules.projects.tasks.services.run_sync", return_value=7) as run:
            self.assertEqual(tasks.sync_repos(timestamp=0), 7)
        run.assert_called_once()

    def test_tache_enregistree_avec_son_nom_stable(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        self.assertIn("projects.sync_repos", app.tasks)

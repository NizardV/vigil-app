from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import TrackedRepo
from .services import sync_repos

RAW = {
    "full_name": "NizardV/demo", "description": "d", "language": "Python", "private": False,
    "archived": False, "open_issues_count": 2, "pushed_at": "2026-11-01T10:00:00Z", "fork": False,
}


class ProjectsTests(TestCase):
    def test_sync_est_idempotent(self):
        sync_repos([RAW])
        sync_repos([RAW])
        self.assertEqual(TrackedRepo.objects.count(), 1)

    def test_verdict_inactif(self):
        old = datetime.now(timezone.utc) - timedelta(days=200)
        repo = TrackedRepo(full_name="x/y", pushed_at=old)
        self.assertEqual(repo.verdict, TrackedRepo.Verdict.A_RELANCER)

    def test_liste_exige_une_connexion(self):
        resp = self.client.get(reverse("repo_list"))
        self.assertEqual(resp.status_code, 302)

    def test_liste_connecte(self):
        User.objects.create_user("n", password="p")
        self.client.login(username="n", password="p")
        self.assertEqual(self.client.get(reverse("repo_list")).status_code, 200)

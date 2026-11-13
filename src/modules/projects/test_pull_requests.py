"""Suivi des PR en direct (#projets-pr) : un message par PR, modifié seulement quand son contenu change."""

from datetime import UTC, datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.github import GitHubError

from .models import PullRequestMessage
from .pull_requests import track_pull_requests

NOW = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)
HOOK = {"projects-pr": "https://discord.com/api/webhooks/1/secret"}
REPO = "NizardV/Portfolio"


def pr(number=64, state="open", merged_at=None, author="NizardV"):
    return {
        "number": number, "state": state, "merged_at": merged_at, "title": "ci: fusion automatique",
        "html_url": f"https://github.com/{REPO}/pull/{number}", "user": {"login": author},
        "head": {"ref": "claude/x", "sha": "aaa"}, "base": {"ref": "main", "repo": {"full_name": REPO}},
        "merge_commit_sha": "bbb",
    }  # fmt: skip


class FakeGitHub:
    def __init__(self, pull, checks=(), statuses=(), merge_checks=()):
        self.pull, self.checks, self.statuses, self.merge_checks = (
            pull,
            list(checks),
            list(statuses),
            list(merge_checks),
        )
        self.error = None
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(path)
        if self.error:
            raise self.error
        if path == "/search/issues":
            return {
                "items": [{"number": self.pull["number"], "repository_url": f"https://api.github.com/repos/{REPO}"}]
            }
        if path.endswith(f"/pulls/{self.pull['number']}"):
            return self.pull
        if path.endswith("/aaa/check-runs"):
            return {"check_runs": self.checks}
        if path.endswith("/bbb/check-runs"):
            return {"check_runs": self.merge_checks}
        if path.endswith("/status"):
            return {"statuses": self.statuses if "aaa" in path else []}
        raise AssertionError(path)


def run(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion}


@override_settings(DISCORD_WEBHOOKS=HOOK, GITHUB_OWNER="NizardV")
class TrackTests(TestCase):
    def setUp(self):
        self.post = mock.Mock(return_value="111")
        self.edit = mock.Mock()

    def track(self, gh, now=NOW):
        return track_pull_requests(client=gh, post=self.post, edit=self.edit, now=now)

    def description(self, call):
        return call.args[-1]["embeds"][0]["description"]

    def test_nouvelle_pr_postee_avec_ses_verifications(self):
        gh = FakeGitHub(
            pr(), checks=[run("e2e", "in_progress", None)], statuses=[{"context": "Vercel", "state": "success"}]
        )
        result = self.track(gh)
        self.assertEqual((result["posted"], result["edited"]), (1, 0))
        text = self.description(self.post.call_args)
        self.assertIn("🟢 Ouverte", text)
        self.assertIn("⏳ e2e", text)
        self.assertIn("✅ Vercel", text)
        self.assertEqual(PullRequestMessage.objects.get().message_id, "111")

    def test_message_modifie_seulement_si_le_contenu_change(self):
        gh = FakeGitHub(pr(), checks=[run("e2e", "in_progress", None)])
        self.track(gh)
        self.track(gh)
        self.edit.assert_not_called()  # rien n'a changé
        gh.checks = [run("e2e")]
        self.track(gh)
        self.edit.assert_called_once()
        self.assertEqual(self.edit.call_args.args[1], "111")
        self.assertIn("✅ e2e", self.description(self.edit.call_args))

    def test_echec_signale(self):
        self.track(FakeGitHub(pr(), checks=[run("e2e", conclusion="failure")]))
        embed = self.post.call_args.args[-1]["embeds"][0]
        self.assertIn("❌ e2e", embed["description"])
        self.assertEqual(embed["color"], 0xDC2626)

    def test_fusion_puis_deploiement_puis_fin_du_suivi(self):
        merged = pr(state="closed", merged_at="2026-11-13T19:58:00Z")
        gh = FakeGitHub(merged, checks=[run("e2e")], merge_checks=[run("deploy", "in_progress", None)])
        self.track(gh)
        self.assertIn("🟣 Fusionnée", self.description(self.post.call_args))
        self.assertIn("⏳ deploy", self.description(self.post.call_args))
        self.assertFalse(PullRequestMessage.objects.get().finished)
        gh.merge_checks = [run("deploy")]
        self.track(gh)
        self.assertIn("✅ deploy", self.description(self.edit.call_args))
        self.assertTrue(PullRequestMessage.objects.get().finished)
        gh.calls.clear()
        self.track(gh)
        self.assertEqual(gh.calls, ["/search/issues"])  # PR terminée : plus aucun appel pour elle

    def test_fusionnee_sans_deploiement_suivi_arrete_apres_quinze_minutes(self):
        gh = FakeGitHub(pr(state="closed", merged_at="2026-11-13T19:30:00Z"), checks=[run("e2e")])
        self.track(gh)
        self.assertTrue(PullRequestMessage.objects.get().finished)

    def test_pr_fermee_sans_fusion(self):
        self.track(FakeGitHub(pr(state="closed"), checks=[run("e2e")]))
        self.assertIn("⚫ Fermée", self.description(self.post.call_args))
        self.assertTrue(PullRequestMessage.objects.get().finished)

    def test_pr_dependabot_marquee(self):
        self.track(FakeGitHub(pr(author="dependabot[bot]")))
        self.assertTrue(self.post.call_args.args[-1]["embeds"][0]["title"].startswith("🤖"))

    def test_erreur_github_rien_d_envoye(self):
        gh = FakeGitHub(pr())
        gh.error = GitHubError(502, "Bad gateway")
        self.assertEqual(self.track(gh)["status"], "error")
        self.post.assert_not_called()

    @override_settings(DISCORD_WEBHOOKS={})
    def test_sans_webhook(self):
        self.assertEqual(self.track(FakeGitHub(pr()))["status"], "not_configured")

    @override_settings(DISCORD_WEBHOOKS={})
    def test_commande(self):
        out = StringIO()
        call_command("track_prs", stdout=out)
        self.assertIn("non défini", out.getvalue())

    def test_tache_enregistree(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        self.assertIn("projects.track_pull_requests", app.tasks)


@override_settings(DISCORD_WEBHOOKS=HOOK, GITHUB_OWNER="NizardV")
class ConcurrencyTests(TestCase):
    def test_execution_simultanee_ne_poste_qu_un_message(self):
        """Une autre exécution a déjà réservé la PR (message en cours d'envoi) : on ne poste pas une seconde fois."""
        PullRequestMessage.objects.create(repo=REPO, number=64, message_id="")
        post = mock.Mock(return_value="222")
        track_pull_requests(client=FakeGitHub(pr()), post=post, edit=mock.Mock(), now=NOW)
        post.assert_not_called()

    def test_echec_d_envoi_libere_la_reservation(self):
        from core.notifications import NotificationError

        post = mock.Mock(side_effect=NotificationError("Discord indisponible"))
        result = track_pull_requests(client=FakeGitHub(pr()), post=post, edit=mock.Mock(), now=NOW)
        self.assertEqual(result["status"], "error")
        self.assertFalse(PullRequestMessage.objects.exists())  # retenté à la minute suivante

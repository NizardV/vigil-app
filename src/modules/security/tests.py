from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core.github import GitHubError

from .collectors.github import audit_github, check_branch_protection, check_dependabot, check_secret_scanning
from .models import CheckResult, Finding
from .outcomes import ERROR, FINDINGS, OK, UNAVAILABLE, CheckOutcome, Observation
from .services import record_outcome, summary


def outcome(status=FINDINGS, keys=("1",), check="dependabot", severity=3):
    obs = [Observation(key=k, severity=severity, title=f"alerte {k}") for k in keys]
    return CheckOutcome("github", "NizardV/demo", check, status, obs if status == FINDINGS else [])


class LifecycleTests(TestCase):
    def test_nouveau_constat_ouvert(self):
        record_outcome(outcome())
        f = Finding.objects.get()
        self.assertEqual(f.status, Finding.Status.OPEN)
        self.assertEqual(CheckResult.objects.get().status, FINDINGS)

    def test_relancer_ne_duplique_pas(self):
        record_outcome(outcome())
        first_seen = Finding.objects.get().first_seen
        record_outcome(outcome())
        f = Finding.objects.get()
        self.assertEqual(Finding.objects.count(), 1)
        self.assertEqual(f.first_seen, first_seen)
        self.assertGreater(f.last_seen, first_seen)

    def test_constat_disparu_est_resolu_puis_rouvert(self):
        record_outcome(outcome())
        record_outcome(outcome(status=OK))
        f = Finding.objects.get()
        self.assertEqual(f.status, Finding.Status.RESOLVED)
        self.assertIsNotNone(f.resolved_at)
        record_outcome(outcome())
        f.refresh_from_db()
        self.assertEqual((f.status, f.resolved_at), (Finding.Status.OPEN, None))

    def test_constat_ignore_le_reste(self):
        record_outcome(outcome())
        Finding.objects.update(status=Finding.Status.IGNORED, note="faux positif")
        record_outcome(outcome())
        self.assertEqual(Finding.objects.get().status, Finding.Status.IGNORED)

    def test_controle_indisponible_ne_resout_rien(self):
        """Régression critique : 'on n'a pas pu regarder' ne doit JAMAIS fermer un constat."""
        record_outcome(outcome())
        for status in (UNAVAILABLE, ERROR):
            record_outcome(CheckOutcome("github", "NizardV/demo", "dependabot", status, detail="403"))
            self.assertEqual(Finding.objects.get().status, Finding.Status.OPEN)
        self.assertEqual(CheckResult.objects.get().status, ERROR)

    def test_resolution_limitee_au_meme_controle(self):
        record_outcome(outcome(check="dependabot"))
        record_outcome(outcome(check="code_scanning", status=OK))
        self.assertEqual(Finding.objects.get().status, Finding.Status.OPEN)

    def test_resume_signale_les_controles_non_couverts(self):
        record_outcome(outcome())
        record_outcome(CheckOutcome("github", "NizardV/demo", "code_scanning", UNAVAILABLE, detail="404"))
        report = summary()
        self.assertEqual(report["open_total"], 1)
        self.assertEqual(report["not_covered"], 1)


class FakeClient:
    """Client GitHub factice : `routes` associe un début de chemin à une réponse ou à une exception."""

    def __init__(self, routes, repos=None):
        self.routes, self.repos, self.calls = routes, repos or [], []

    def _answer(self, path):
        self.calls.append(path)
        # La route la plus spécifique (la plus longue) l'emporte.
        for prefix, answer in sorted(self.routes.items(), key=lambda kv: -len(kv[0])):
            if path.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise GitHubError(404, "Not Found")

    def get(self, path, params=None):
        return self._answer(path)

    def paginate(self, path, params=None, max_pages=20):
        return self._answer(path)

    def list_owner_repos(self, owner):
        return self.repos


REPO = {"full_name": "NizardV/demo", "default_branch": "main", "archived": False}


class CollectorTests(TestCase):
    def test_dependabot_mappe_la_gravite(self):
        alerts = [
            {
                "number": 7,
                "html_url": "https://github.com/x",
                "security_advisory": {"severity": "critical", "summary": "RCE", "cve_id": "CVE-1"},
                "dependency": {"package": {"name": "django", "ecosystem": "pip"}},
            }
        ]
        out = check_dependabot(FakeClient({"/repos/NizardV/demo/dependabot": alerts}), REPO)
        self.assertEqual(out.status, FINDINGS)
        self.assertEqual(out.observations[0].severity, Finding.Severity.CRITICAL)
        self.assertIn("django", out.observations[0].title)

    def test_aucune_alerte_est_ok(self):
        self.assertEqual(check_dependabot(FakeClient({"/repos": []}), REPO).status, OK)

    def test_droits_insuffisants_est_indisponible_pas_ok(self):
        out = check_dependabot(FakeClient({"/repos": GitHubError(403, "Resource not accessible")}), REPO)
        self.assertEqual(out.status, UNAVAILABLE)

    def test_erreur_serveur_ou_reseau_est_erreur(self):
        for exc in (GitHubError(502, "Bad gateway"), GitHubError(0, "timeout")):
            self.assertEqual(check_dependabot(FakeClient({"/repos": exc}), REPO).status, ERROR)

    def test_secret_jamais_stocke(self):
        alerts = [{"number": 1, "secret": "ghp_SUPERSECRET", "secret_type": "github_pat", "html_url": "u"}]
        out = check_secret_scanning(FakeClient({"/repos": alerts}), REPO)
        record_outcome(out)
        stored = str([(f.title, f.detail, f.url) for f in Finding.objects.all()])
        self.assertNotIn("SUPERSECRET", stored)
        self.assertEqual(Finding.objects.get().severity, Finding.Severity.CRITICAL)

    def test_branche_non_protegee_est_un_constat(self):
        client = FakeClient({"/repos": GitHubError(404, "Branch not protected")})
        out = check_branch_protection(client, REPO)
        self.assertEqual((out.status, out.observations[0].severity), (FINDINGS, Finding.Severity.LOW))

    def test_branche_404_generique_est_indisponible(self):
        out = check_branch_protection(FakeClient({"/repos": GitHubError(404, "Not Found")}), REPO)
        self.assertEqual(out.status, UNAVAILABLE)

    def test_audit_ignore_les_depots_archives_et_noms_suspects(self):
        repos = [
            {"full_name": "NizardV/old", "default_branch": "main", "archived": True},
            {"full_name": "NizardV/../etc", "default_branch": "main", "archived": False},
        ]
        client = FakeClient({"/repos": []}, repos=repos)
        self.assertEqual(audit_github(client, "NizardV"), [])
        self.assertEqual(client.calls, [])

    def test_audit_s_arrete_quand_le_quota_est_epuise(self):
        limited = GitHubError(403, "API rate limit exceeded", rate_limited=True)
        client = FakeClient({"/repos": limited}, repos=[REPO, {**REPO, "full_name": "NizardV/other"}])
        outcomes = audit_github(client, "NizardV")
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].rate_limited)


class CommandTests(TestCase):
    def test_commande_affiche_un_resume(self):
        run = {"checks": 4, "by_status": {"ok": 4}, "rate_limited": False}
        out = StringIO()
        with mock.patch("modules.security.management.commands.scan_security.run_github_audit", return_value=run):
            call_command("scan_security", stdout=out)
        self.assertIn("4 contrôles", out.getvalue())

    def test_github_injoignable_donne_une_erreur_claire_sans_toucher_aux_constats(self):
        from django.core.management.base import CommandError

        record_outcome(outcome())
        failure = GitHubError(0, "getaddrinfo failed")
        target = "modules.security.management.commands.scan_security.run_github_audit"
        with mock.patch(target, side_effect=failure):
            with self.assertRaises(CommandError) as ctx:
                call_command("scan_security")
        self.assertIn("aucun constat modifié", str(ctx.exception))
        self.assertEqual(Finding.objects.get().status, Finding.Status.OPEN)

    def test_tache_enregistree(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        self.assertIn("security.audit_github", app.tasks)


# --- Contrôles de posture de remédiation Dependabot ---------------------------------------------------

import base64  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

from .collectors.posture import check_automerge_ready, check_stale_dependabot_prs  # noqa: E402

BASE = "/repos/NizardV/demo"
CI_ON_PR = "on:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
AUTOMERGE_WF = (
    "on: pull_request\njobs:\n  m:\n    steps:\n"
    "      - uses: dependabot/fetch-metadata@v2\n      - run: gh pr merge --auto\n"
)


def pr(number, days_old, author="dependabot[bot]"):
    created = (datetime.now(UTC) - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "number": number,
        "title": f"bump {number}",
        "created_at": created,
        "html_url": "u",
        "user": {"login": author},
    }


def workflow_routes(*texts):
    """Routes GitHub pour un dossier de workflows contenant les fichiers donnés."""
    routes = {
        f"{BASE}/contents/.github/workflows": [
            {"type": "file", "path": f".github/workflows/w{i}.yml"} for i in range(len(texts))
        ]
    }
    for i, text in enumerate(texts):
        routes[f"{BASE}/contents/.github/workflows/w{i}.yml"] = {
            "encoding": "base64",
            "content": base64.b64encode(text.encode()).decode(),
        }
    return routes


class StaleDependabotTests(TestCase):
    def test_pr_recente_n_est_pas_signalee(self):
        out = check_stale_dependabot_prs(FakeClient({f"{BASE}/pulls": [pr(1, 3)]}), REPO)
        self.assertEqual(out.status, OK)

    def test_pr_ancienne_signalee_avec_gravite_croissante(self):
        client = FakeClient({f"{BASE}/pulls": [pr(1, 20), pr(2, 45)]})
        out = check_stale_dependabot_prs(client, REPO)
        self.assertEqual(out.status, FINDINGS)
        by_key = {o.key: o.severity for o in out.observations}
        self.assertEqual(by_key, {"1": Finding.Severity.LOW, "2": Finding.Severity.MEDIUM})

    def test_pr_humaine_ignoree(self):
        out = check_stale_dependabot_prs(FakeClient({f"{BASE}/pulls": [pr(1, 90, author="nizard")]}), REPO)
        self.assertEqual(out.status, OK)

    def test_pr_fusionnee_resout_le_constat(self):
        record_outcome(check_stale_dependabot_prs(FakeClient({f"{BASE}/pulls": [pr(1, 40)]}), REPO))
        self.assertEqual(Finding.objects.get().status, Finding.Status.OPEN)
        record_outcome(check_stale_dependabot_prs(FakeClient({f"{BASE}/pulls": []}), REPO))
        self.assertEqual(Finding.objects.get().status, Finding.Status.RESOLVED)

    def test_erreur_donne_indisponible(self):
        out = check_stale_dependabot_prs(FakeClient({f"{BASE}/pulls": GitHubError(403, "denied")}), REPO)
        self.assertEqual(out.status, UNAVAILABLE)


class AutomergeReadyTests(TestCase):
    def routes(self, *, allow=True, required=True, workflows=(), prs=()):
        base = {
            BASE: {"allow_auto_merge": allow},
            f"{BASE}/pulls": list(prs),
            f"{BASE}/branches/main/protection/required_status_checks": (
                {"contexts": ["test"]} if required else GitHubError(404, "Branch not protected")
            ),
            f"{BASE}/rules/branches/main": [],
        }
        return {**base, **workflow_routes(*workflows)}

    def test_tout_en_place_est_ok(self):
        client = FakeClient(self.routes(workflows=(CI_ON_PR, AUTOMERGE_WF)))
        self.assertEqual(check_automerge_ready(client, REPO).status, OK)

    def test_automerge_sans_controle_obligatoire_est_haute_gravite(self):
        """Le piège principal : GitHub fusionne immédiatement, sans attendre la CI."""
        client = FakeClient(self.routes(required=False, workflows=(CI_ON_PR, AUTOMERGE_WF)))
        out = check_automerge_ready(client, REPO)
        unsafe = [o for o in out.observations if o.key == "unsafe_automerge"]
        self.assertEqual(unsafe[0].severity, Finding.Severity.HIGH)

    def test_manques_listes_et_gravite_selon_les_pr_en_attente(self):
        client = FakeClient(self.routes(allow=False, required=False, prs=[pr(1, 2)]))
        out = check_automerge_ready(client, REPO)
        readiness = next(o for o in out.observations if o.key == "readiness")
        self.assertEqual(readiness.severity, Finding.Severity.LOW)  # des PR attendent : plus pressant
        self.assertEqual(
            set(readiness.detail["gaps"]),
            {"pas de CI sur les PR", "branche sans contrôle obligatoire", "auto-merge non autorisé",
             "pas de workflow d'auto-merge"},
        )  # fmt: skip
        client = FakeClient(self.routes(allow=False, required=False))
        readiness = next(o for o in check_automerge_ready(client, REPO).observations if o.key == "readiness")
        self.assertEqual(readiness.severity, Finding.Severity.INFO)  # rien en attente : simple info

    def test_depot_sans_dossier_workflows_n_a_pas_de_ci(self):
        routes = self.routes()
        routes[f"{BASE}/contents/.github/workflows"] = GitHubError(404, "Not Found")
        out = check_automerge_ready(FakeClient(routes), REPO)
        self.assertIn("pas de CI sur les PR", out.observations[0].detail["gaps"])

    def test_protection_indeterminable_est_indisponible_pas_ok(self):
        """Offre gratuite + dépôt privé : impossible de savoir. Ne doit surtout pas passer pour « OK »."""
        routes = self.routes(workflows=(CI_ON_PR, AUTOMERGE_WF))
        routes[f"{BASE}/branches/main/protection/required_status_checks"] = GitHubError(403, "Upgrade to Pro")
        routes[f"{BASE}/rules/branches/main"] = GitHubError(403, "Upgrade to Pro")
        out = check_automerge_ready(FakeClient(routes), REPO)
        self.assertEqual(out.status, UNAVAILABLE)
        self.assertIn("contrôles obligatoires", out.detail)

    def test_ruleset_suffit_comme_controle_obligatoire(self):
        routes = self.routes(required=False, workflows=(CI_ON_PR, AUTOMERGE_WF))
        routes[f"{BASE}/rules/branches/main"] = [{"type": "required_status_checks"}]
        self.assertEqual(check_automerge_ready(FakeClient(routes), REPO).status, OK)

    def test_option_auto_merge_inconnue_est_indisponible(self):
        routes = self.routes(workflows=(CI_ON_PR,))
        routes[BASE] = {}  # champ absent : droits insuffisants pour le lire
        self.assertEqual(check_automerge_ready(FakeClient(routes), REPO).status, UNAVAILABLE)

    def test_chemin_de_workflow_hors_dossier_ignore(self):
        routes = self.routes()
        routes[f"{BASE}/contents/.github/workflows"] = [{"type": "file", "path": "../../etc/passwd"}]
        out = check_automerge_ready(FakeClient(routes), REPO)
        self.assertIn("pas de CI sur les PR", out.observations[0].detail["gaps"])

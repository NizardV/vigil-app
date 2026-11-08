from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.sonarcloud import SonarCloudError

from . import notifications, services
from .collectors import sonarcloud
from .models import CheckResult, Finding
from .outcomes import ERROR, FINDINGS, OK, UNAVAILABLE

PROJECT = "NizardV_demo"


def issue(key="i1", quality="SECURITY", severity="HIGH", message="Faille", path="src/a.py", line=5):
    return {
        "key": key,
        "rule": "python:S1",
        "message": message,
        "component": f"{PROJECT}:{path}",
        "line": line,
        "impacts": [{"softwareQuality": quality, "severity": severity}],
    }


class FakeClient:
    def __init__(self, issues=(), gate=None, error=None):
        self._issues, self._gate, self._error = list(issues), gate or {"status": "OK"}, error

    def quality_gate(self, project):
        if self._error:
            raise self._error
        return self._gate

    def issues(self, project):
        if self._error:
            raise self._error
        return self._issues


class CollectorTests(TestCase):
    def test_securite_et_fiabilite_sont_signalees_a_toute_gravite(self):
        outcome = sonarcloud.check_issues(
            FakeClient([issue("a", "SECURITY", "LOW"), issue("b", "RELIABILITY", "MEDIUM")]), PROJECT
        )
        self.assertEqual(outcome.status, FINDINGS)
        self.assertEqual({o.key: int(o.severity) for o in outcome.observations}, {"a": 1, "b": 2})

    def test_maintenabilite_seulement_si_haute(self):
        outcome = sonarcloud.check_issues(
            FakeClient([issue("m1", "MAINTAINABILITY", "LOW"), issue("m2", "MAINTAINABILITY", "HIGH")]), PROJECT
        )
        self.assertEqual([o.key for o in outcome.observations], ["m2"])
        self.assertEqual(outcome.observations[0].severity, Finding.Severity.MEDIUM)  # plafonnée : jamais d'alerte

    def test_gravite_et_lien(self):
        obs = sonarcloud.check_issues(FakeClient([issue("k", "SECURITY", "BLOCKER")]), PROJECT).observations[0]
        self.assertEqual(obs.severity, Finding.Severity.CRITICAL)
        self.assertTrue(obs.url.startswith("https://sonarcloud.io/project/issues?"))
        self.assertIn("src/a.py:5", obs.title)
        self.assertNotIn(PROJECT, obs.title)  # le préfixe de composant est retiré

    def test_aucun_probleme_est_ok(self):
        self.assertEqual(sonarcloud.check_issues(FakeClient([]), PROJECT).status, OK)

    def test_quality_gate_en_echec_est_un_constat_haut(self):
        gate = {"status": "ERROR", "conditions": [{"status": "ERROR", "metricKey": "new_security_rating"}]}
        outcome = sonarcloud.check_quality_gate(FakeClient(gate=gate), PROJECT)
        self.assertEqual(outcome.status, FINDINGS)
        self.assertEqual(outcome.observations[0].severity, Finding.Severity.HIGH)
        self.assertIn("new_security_rating", outcome.observations[0].title)

    def test_quality_gate_ok_et_sans_analyse(self):
        self.assertEqual(sonarcloud.check_quality_gate(FakeClient(), PROJECT).status, OK)
        self.assertEqual(
            sonarcloud.check_quality_gate(FakeClient(gate={"status": "NONE"}), PROJECT).status, UNAVAILABLE
        )

    def test_erreurs_jamais_ok(self):
        self.assertEqual(
            sonarcloud.check_issues(FakeClient(error=SonarCloudError(404, "x")), PROJECT).status, UNAVAILABLE
        )
        self.assertEqual(sonarcloud.check_issues(FakeClient(error=SonarCloudError(0, "x")), PROJECT).status, ERROR)
        self.assertEqual(sonarcloud.check_issues(FakeClient(error=SonarCloudError(503, "x")), PROJECT).status, ERROR)


@override_settings(SONARCLOUD_PROJECTS=[PROJECT])
class ServiceTests(TestCase):
    def test_cycle_de_vie(self):
        services.run_sonarcloud_audit(FakeClient([issue("a"), issue("b")]))
        self.assertEqual(Finding.objects.filter(status=Finding.Status.OPEN).count(), 2)
        services.run_sonarcloud_audit(FakeClient([issue("a")]))  # « b » corrigé
        self.assertEqual(Finding.objects.get(title__contains="Faille", url__contains="issues=a").status, "open")
        self.assertEqual(Finding.objects.filter(status=Finding.Status.RESOLVED).count(), 1)

    def test_injoignable_ne_referme_rien(self):
        services.run_sonarcloud_audit(FakeClient([issue("a")]))
        services.run_sonarcloud_audit(FakeClient(error=SonarCloudError(0, "injoignable")))
        self.assertEqual(
            Finding.objects.filter(status=Finding.Status.OPEN).count(), 1
        )  # on ne sait pas : pas « réglé »
        self.assertTrue(CheckResult.objects.filter(source="sonarcloud", status=ERROR).exists())

    @override_settings(SONARCLOUD_PROJECTS=[])
    def test_non_configure(self):
        self.assertEqual(services.run_sonarcloud_audit(FakeClient())["status"], "not_configured")

    def test_commande_et_tache(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        self.assertIn("security.audit_sonarcloud", app.tasks)
        out = StringIO()
        with mock.patch("modules.security.services.SonarCloudClient", return_value=FakeClient([issue("a")])):
            call_command("audit_sonarcloud", stdout=out)
        self.assertIn("Constats ouverts : 1", out.getvalue())


class DigestSectionsTests(TestCase):
    def finding(self, source, key, severity=Finding.Severity.HIGH, status=Finding.Status.OPEN, **extra):
        return Finding.objects.create(
            fingerprint=f"{source}-{key}",
            source=source,
            target="NizardV/demo",
            check_name="c",
            severity=severity,
            title=f"Titre {source} {key}",
            status=status,
            **extra,
        )

    def test_un_seul_message_regroupe_les_sources(self):
        self.finding("vps", "1")
        self.finding("github", "1")
        self.finding("sonarcloud", "1", Finding.Severity.MEDIUM)
        description = notifications.digest_payload()["embeds"][0]["description"]
        for label in ("Serveur (VPS)", "GitHub", "Code (SonarCloud)"):
            self.assertIn(label, description)
        self.assertLess(description.index("Serveur"), description.index("GitHub"))  # ordre stable
        self.assertIn("Titre vps 1", description)
        self.assertNotIn("Titre sonarcloud 1", description)  # moyenne : comptée, pas détaillée

    def test_nouveaux_et_resolus_de_la_semaine(self):
        now = timezone.now()
        self.finding("vps", "vieux", first_seen=now - timedelta(days=30))
        self.finding("vps", "neuf", first_seen=now - timedelta(days=2))
        old = now - timedelta(days=40)
        self.finding(
            "vps", "regle", status=Finding.Status.RESOLVED, first_seen=old, resolved_at=now - timedelta(days=3)
        )
        self.finding(
            "vps", "ancien", status=Finding.Status.RESOLVED, first_seen=old, resolved_at=now - timedelta(days=20)
        )
        description = notifications.digest_payload(now)["embeds"][0]["description"]
        self.assertIn("2 ouvert(s) · 1 nouveau(x) · 1 résolu(s)", description)

    def test_source_inconnue_affichee_et_texte_neutralise(self):
        self.finding("autre*", "1")
        description = notifications.digest_payload()["embeds"][0]["description"]
        self.assertIn("autre\\*", description)

    def test_longueur_toujours_sous_la_limite_discord(self):
        for n in range(60):
            for source in ("vps", "github", "sonarcloud"):
                self.finding(source, str(n), Finding.Severity.CRITICAL)
        self.assertLessEqual(len(notifications.digest_payload()["embeds"][0]["description"]), 4096)

"""Admin sécurité : tableau de bord (indicateurs, graphiques, liens filtrés) et pastilles de couleur."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.testing import login_verified

from .models import CheckResult, Finding
from .outcomes import FINDINGS, UNAVAILABLE, CheckOutcome, Observation
from .services import record_outcome, summary


def audit(target="NizardV/demo", source="github", severities=(4, 3, 2), status=FINDINGS):
    obs = [Observation(key=str(i), severity=s, title=f"alerte {i}") for i, s in enumerate(severities)]
    record_outcome(CheckOutcome(source, target, "dependabot", status, obs if status == FINDINGS else []))


class DashboardTests(TestCase):
    def setUp(self):
        login_verified(self.client, User.objects.create_superuser("root", "root@example.com", "x" * 16))

    def test_sans_constat(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Aucun constat ouvert.")
        self.assertNotContains(response, 'data-type="bar"')

    def test_indicateurs_et_graphiques(self):
        audit()
        audit(target="OVH", source="vps", severities=(1,))
        record_outcome(CheckOutcome("github", "NizardV/demo", "code_scanning", UNAVAILABLE))
        response = self.client.get(reverse("admin:index"))
        kpis = {k["title"]: k["value"] for k in response.context["kpis"]}
        self.assertEqual(kpis, {"Constats ouverts": 4, "Critiques et hauts": 2, "Contrôles non couverts": 1})
        self.assertContains(response, 'data-type="bar"', count=2)
        self.assertContains(response, "Serveur (VPS)")

    def test_liens_des_indicateurs_acceptes_par_l_admin(self):
        """Un filtre refusé par l'admin redirige vers « ?e=1 » : chaque lien doit afficher la liste."""
        audit()
        for kpi in self.client.get(reverse("admin:index")).context["kpis"]:
            with self.subTest(kpi=kpi["title"]):
                response = self.client.get(kpi["href"])
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("e=1", response.request["QUERY_STRING"])

    def test_filtre_critiques_et_hauts(self):
        audit()
        response = self.client.get(reverse("admin:security_finding_changelist") + "?status__exact=open&severity__gte=3")
        self.assertEqual(response.context["cl"].result_count, 2)


class BadgeTests(TestCase):
    def setUp(self):
        login_verified(self.client, User.objects.create_superuser("root", "root@example.com", "x" * 16))

    def test_gravite_et_statut_en_pastilles(self):
        audit(severities=(4,))
        response = self.client.get(reverse("admin:security_finding_changelist"))
        self.assertContains(response, "Critique")
        self.assertContains(response, "bg-red-100")  # pastille « danger »

    def test_couverture_en_pastilles(self):
        record_outcome(CheckOutcome("github", "NizardV/demo", "code_scanning", UNAVAILABLE))
        response = self.client.get(reverse("admin:security_checkresult_changelist"))
        self.assertContains(response, CheckResult.Status.UNAVAILABLE.label)
        self.assertContains(response, "bg-blue-100")  # pastille « info »


class SummaryTests(TestCase):
    def test_constats_ouverts_par_source(self):
        audit()
        audit(target="OVH", source="vps", severities=(1,))
        Finding.objects.filter(source="vps").update(status=Finding.Status.IGNORED)
        self.assertEqual(summary()["open_by_source"], {"github": 3})

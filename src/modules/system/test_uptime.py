"""Disponibilité des sites surveillés : alerte après deux échecs d'affilée, puis au retour."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.netfetch import FetchError

from .models import SiteStatus
from .uptime import check_sites

SITES = [("Cyna (API)", "https://api.example.org/health")]
WEBHOOKS = {"system": "https://discord.com/api/webhooks/1/secret"}


def up(url, **kwargs):
    return b"Healthy"


def down(url, **kwargs):
    raise FetchError("le serveur a répondu 503")


@override_settings(VIGIL_UPTIME_SITES=SITES, DISCORD_WEBHOOKS=WEBHOOKS)
class UptimeTests(TestCase):
    def setUp(self):
        self.send = mock.Mock()

    def run_check(self, fetch, now=None):
        return check_sites(fetch=fetch, send=self.send, now=now)

    def test_site_en_ligne_sans_alerte(self):
        result = self.run_check(up)
        self.assertEqual((result["checked"], result["down"]), (1, []))
        self.assertTrue(SiteStatus.objects.get().is_up)
        self.send.assert_not_called()

    def test_un_echec_isole_n_alerte_pas(self):
        self.run_check(down)
        self.send.assert_not_called()
        self.assertTrue(SiteStatus.objects.get().is_up)

    def test_deux_echecs_alertent_une_seule_fois(self):
        for _ in range(4):
            self.run_check(down)
        self.send.assert_called_once()
        embed = self.send.call_args.args[1]["embeds"][0]
        self.assertIn("ne répond plus", embed["title"])
        self.assertIn("503", embed["description"])
        self.assertFalse(SiteStatus.objects.get().is_up)

    def test_retour_signale_avec_la_duree(self):
        start = timezone.now()
        self.run_check(down, now=start)
        self.run_check(down, now=start)
        self.run_check(up, now=start + timedelta(minutes=45))
        embed = self.send.call_args.args[1]["embeds"][0]
        self.assertIn("répond de nouveau", embed["title"])
        self.assertIn("45 min", embed["description"])
        status = SiteStatus.objects.get()
        self.assertEqual((status.is_up, status.consecutive_failures), (True, 0))

    def test_alerte_non_envoyee_retentee_au_passage_suivant(self):
        from core.notifications import NotificationError

        self.send.side_effect = NotificationError("Discord indisponible")
        self.run_check(down)
        self.run_check(down)
        self.assertTrue(SiteStatus.objects.get().is_up)  # rien n'est marqué
        self.send.side_effect = None
        self.run_check(down)
        self.assertFalse(SiteStatus.objects.get().is_up)

    def test_erreur_reseau_comptee_comme_panne(self):
        def refused(url, **kwargs):
            raise ConnectionRefusedError("refusé")

        self.run_check(refused)
        self.assertIn("ConnectionRefusedError", SiteStatus.objects.get().last_error)

    @override_settings(DISCORD_WEBHOOKS={})
    def test_sans_webhook_rien_n_est_marque(self):
        self.run_check(down)
        self.run_check(down)
        self.assertTrue(SiteStatus.objects.get().is_up)

    def test_site_retire_de_la_configuration(self):
        SiteStatus.objects.create(name="Ancien", url="https://old.example.org")
        self.run_check(up)
        self.assertEqual(list(SiteStatus.objects.values_list("name", flat=True)), ["Cyna (API)"])

    def test_commande(self):
        out = StringIO()
        with mock.patch("core.netfetch.fetch_public", up):
            call_command("check_sites", stdout=out)
        self.assertIn("1 site(s) vérifié(s)", out.getvalue())

    def test_tache_enregistree(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        self.assertIn("system.uptime_check", app.tasks)

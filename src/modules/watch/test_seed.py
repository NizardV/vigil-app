from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from . import seed
from .models import Source, Theme


class SeedTests(TestCase):
    def test_cree_les_trois_themes_en_hebdomadaire(self):
        result = seed.seed()
        self.assertEqual(result["themes"], 3)
        self.assertEqual(Source.objects.count(), result["sources"])
        for theme in Theme.objects.all():
            self.assertEqual((theme.digest_days, theme.digest_hour, theme.lookback_days), ([0], 9, 7))

    def test_rejouable_sans_doublon_ni_ecrasement(self):
        seed.seed()
        count = Source.objects.count()
        Theme.objects.filter(slug="juridique").update(min_score=8.0)
        self.assertEqual(seed.seed(), {"themes": 0, "sources": 0})
        self.assertEqual(Source.objects.count(), count)
        self.assertEqual(Theme.objects.get(slug="juridique").min_score, 8.0)

    def test_identifiant_de_salon(self):
        seed.seed({"cybersecurite": "123"})
        self.assertEqual(Theme.objects.get(slug="cybersecurite").discord_channel_id, "123")

    def test_commande(self):
        out = StringIO()
        call_command("watch_seed", "--channel", "juridique=456", stdout=out)
        self.assertIn("3 thème(s)", out.getvalue())
        self.assertEqual(Theme.objects.get(slug="juridique").discord_channel_id, "456")

    def test_commande_refuse_un_identifiant_invalide(self):
        with self.assertRaises(CommandError):
            call_command("watch_seed", "--channel", "juridique=abc")

    def test_un_thème_par_salon_webhook(self):
        """Le slug doit correspondre à DISCORD_WEBHOOK_WATCH_<SLUG>."""
        self.assertEqual({s["slug"] for s in seed.PRESET}, {"cybersecurite", "juridique", "technologique"})

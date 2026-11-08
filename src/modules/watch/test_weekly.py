from datetime import UTC, datetime, timedelta

from django.test import override_settings

from . import digest
from .test_digest import HOOK, NOW, Base, Recorder

MONDAY_9H = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)  # lundi 9 novembre, 09:00 à Paris (hiver, UTC+1)


class WeeklyDueTests(Base):
    def setUp(self):
        super().setUp()
        self.theme.digest_hour, self.theme.digest_days, self.theme.lookback_days = 9, [0], 7
        self.theme.save()

    def test_chaque_lundi_a_9h_heure_de_paris(self):
        self.assertEqual(digest.due_themes(MONDAY_9H), [self.theme])

    def test_pas_les_autres_jours_meme_a_9h(self):
        for days in range(1, 7):  # mardi … dimanche
            self.assertEqual(digest.due_themes(MONDAY_9H + timedelta(days=days)), [], f"+{days} j")

    def test_pas_le_lundi_a_une_autre_heure(self):
        self.assertEqual(digest.due_themes(MONDAY_9H + timedelta(hours=1)), [])
        self.assertEqual(digest.due_themes(MONDAY_9H - timedelta(hours=1)), [])

    def test_lundi_suivant(self):
        self.assertEqual(digest.due_themes(MONDAY_9H + timedelta(days=7)), [self.theme])

    def test_deux_fois_par_semaine(self):
        self.theme.digest_days = [0, 3]  # lundi et jeudi
        self.theme.save()
        self.assertEqual(digest.due_themes(MONDAY_9H), [self.theme])
        self.assertEqual(digest.due_themes(MONDAY_9H + timedelta(days=3)), [self.theme])  # jeudi
        self.assertEqual(digest.due_themes(MONDAY_9H + timedelta(days=1)), [])

    def test_heure_d_ete(self):
        """Lundi 6 juillet 2026 : Paris est en UTC+2, donc 09:00 à Paris = 07:00 UTC."""
        monday_summer = datetime(2026, 7, 6, 7, 0, tzinfo=UTC)
        self.assertEqual(digest.due_themes(monday_summer), [self.theme])
        self.assertEqual(digest.due_themes(datetime(2026, 7, 6, 8, 0, tzinfo=UTC)), [])  # 10:00 à Paris

    def test_aucun_jour_configure_n_envoie_jamais(self):
        self.theme.digest_days = []
        self.theme.save()
        for days in range(7):
            self.assertEqual(digest.due_themes(MONDAY_9H + timedelta(days=days)), [])


class WeeklyWindowTests(Base):
    def test_fenetre_par_defaut_de_48h(self):
        self.article(1, hours_ago=24 * 5)  # 5 jours : trop ancien pour un résumé quotidien
        self.assertEqual(digest.candidates(self.theme, NOW), [])

    def test_fenetre_hebdomadaire(self):
        self.theme.lookback_days = 7
        self.theme.save()
        self.article(1, hours_ago=24 * 5)  # 5 jours : retenu
        self.article(2, hours_ago=24 * 8)  # 8 jours : trop ancien
        got = digest.candidates(self.theme, NOW)
        self.assertEqual([c.article.title for c in got], ["Article 1"])

    @override_settings(DISCORD_WEBHOOKS={"watch-ia": HOOK}, DISCORD_BOT_TOKEN="")
    def test_un_article_de_la_semaine_n_est_envoye_qu_une_fois(self):
        """Résumé du lundi : les articles de la semaine partent, et ne reviennent pas le lundi d'après."""
        self.theme.digest_hour, self.theme.digest_days, self.theme.lookback_days = 9, [0], 7
        self.theme.save()
        self.article(1, hours_ago=24 * 4)
        first, second = Recorder(), Recorder()
        self.send(first, now=MONDAY_9H)
        self.send(second, now=MONDAY_9H + timedelta(days=7))
        self.assertEqual((len(first.sent), len(second.sent)), (2, 0))

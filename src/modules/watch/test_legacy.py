from datetime import UTC, datetime

from django.db import connection
from django.test import TestCase

from . import digest
from .legacy import LegacyError, import_legacy
from .models import Analysis, Article, Digest, Feedback, Source, Theme

OLD_HOOK = "https://discord.com/api/webhooks/123/ANCIEN-SECRET-EN-CLAIR"
WINTER = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)  # Paris = UTC+1
SUMMER = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)  # Paris = UTC+2

DDL = """
CREATE SCHEMA legacy;
CREATE TABLE legacy.themes (id serial PRIMARY KEY, user_id uuid, name text, description text, keywords text[],
    digest_hour int, digest_enabled bool, created_at timestamp);
CREATE TABLE legacy.sources (id serial PRIMARY KEY, theme_id int, name text, url text, type text, active bool,
    fetch_interval_hours int, created_at timestamp);
CREATE TABLE legacy.articles (id serial PRIMARY KEY, source_id int, title text, url text, content_raw text,
    published_at timestamp, fetched_at timestamp, processed bool);
CREATE TABLE legacy.analyses (id serial PRIMARY KEY, article_id int, summary text, relevance_score float,
    theme_match text, llm_prompt_version text, created_at timestamp, key_points text[]);
CREATE TABLE legacy.feedbacks (id serial PRIMARY KEY, user_id uuid, article_id int, rating smallint, comment text,
    created_at timestamp);
CREATE TABLE legacy.digests (id serial PRIMARY KEY, theme_id int, content text, sent_at timestamp, channel text);
CREATE TABLE legacy.webhooks (id serial PRIMARY KEY, user_id uuid, theme_id int, url text, type text, active bool);
"""


class LegacyBase(TestCase):
    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute(DDL)
            cursor.execute(
                "INSERT INTO legacy.themes (name, description, keywords, digest_hour, digest_enabled, created_at) "
                "VALUES ('Cybersécurité', 'Failles', ARRAY['cve','rce'], 7, true, '2026-01-01'), "
                "('Cyber sécurité', 'Doublon de slug', ARRAY[]::text[], 9, false, '2026-01-01')"
            )
            cursor.execute(
                "INSERT INTO legacy.sources (theme_id, name, url, type, active, fetch_interval_hours, created_at) VALUES "
                "(1, 'Blog', 'https://blog.example/feed.xml', 'rss', true, 2, '2026-01-01'), "
                "(1, 'Chaîne', 'https://www.youtube.com/feeds/videos.xml?channel_id=UCabcdefghijklmnopqrstuv', 'youtube', true, 6, '2026-01-01'), "
                "(1, 'Inconnu', 'https://x.example/f.xml', 'type-bizarre', false, 999, '2026-01-01')"
            )
            cursor.execute(
                "INSERT INTO legacy.articles (source_id, title, url, content_raw, published_at, fetched_at, processed) VALUES "
                "(1, 'Article A', 'https://blog.example/a?utm_source=nl&id=1', '<p>x</p>', '2026-01-02 10:00', '2026-01-02 11:00', true), "
                "(1, 'Article A bis', 'https://blog.example/a?id=1&utm_campaign=autre', 'doublon', NULL, '2026-01-03', true), "
                "(1, 'Article B', 'https://blog.example/b', NULL, NULL, '2026-01-04', false), "
                "(1, 'Piégé', 'javascript:alert(1)', NULL, NULL, '2026-01-05', false)"
            )
            cursor.execute(
                "INSERT INTO legacy.analyses (article_id, summary, relevance_score, theme_match, llm_prompt_version, created_at, key_points) "
                "VALUES (1, 'Résumé', 11, 'sous-thème', 'v1.0', '2026-01-02 12:00', ARRAY['a','b','c','d']), (3, 'Sans score', NULL, '', 'v1.0', '2026-01-04', NULL)"
            )
            cursor.execute(
                "INSERT INTO legacy.feedbacks (user_id, article_id, rating, comment, created_at) VALUES "
                "('00000000-0000-0000-0000-000000000001', 1, 1, 'bien', '2026-01-03'), "
                "('00000000-0000-0000-0000-000000000001', 3, 0, 'note invalide', '2026-01-03')"
            )
            cursor.execute(
                "INSERT INTO legacy.digests (theme_id, content, sent_at, channel) VALUES (1, 'Ancien résumé', '2026-01-05 07:00', 'discord')"
            )
            cursor.execute(
                f"INSERT INTO legacy.webhooks (theme_id, url, type, active) VALUES (1, '{OLD_HOOK}', 'discord', true)"
            )


class ImportTests(LegacyBase):
    def test_contenu_importe(self):
        counts = import_legacy(now=WINTER)
        self.assertEqual((counts["themes"], counts["sources"], counts["digests"]), (2, 3, 1))
        theme = Theme.objects.get(name="Cybersécurité")
        self.assertEqual((theme.keywords, theme.slug), (["cve", "rce"], "cybersecurite"))
        self.assertEqual(Source.objects.get(name="Chaîne").kind, "youtube")
        self.assertEqual(Source.objects.get(name="Blog").feed_url, "https://blog.example/feed.xml")

    def test_heure_convertie_de_l_utc_vers_paris(self):
        import_legacy(now=WINTER)
        self.assertEqual(Theme.objects.get(name="Cybersécurité").digest_hour, 8)  # 07:00 UTC = 08:00 à Paris l'hiver
        import_legacy(now=SUMMER)
        self.assertEqual(Theme.objects.get(name="Cybersécurité").digest_hour, 9)  # et 09:00 l'été

    def test_slugs_uniques(self):
        import_legacy(now=WINTER)
        self.assertEqual(Theme.objects.values_list("slug", flat=True).distinct().count(), 2)

    def test_valeurs_aberrantes_corrigees(self):
        import_legacy(now=WINTER)
        unknown = Source.objects.get(name="Inconnu")
        self.assertEqual((unknown.kind, unknown.fetch_interval_hours, unknown.active), ("rss", 168, False))
        analysis = Analysis.objects.get()
        self.assertEqual(analysis.relevance_score, 10.0)  # 11 borné
        self.assertEqual(len(analysis.key_points), 3)
        self.assertEqual(Analysis.objects.count(), 1)  # l'analyse sans score est écartée

    def test_urls_normalisees_et_doublons_fusionnes(self):
        """Sans cela, un article déjà connu (avec utm_*) serait recréé en doublon à la prochaine collecte."""
        counts = import_legacy(now=WINTER)
        urls = set(Article.objects.values_list("url", flat=True))
        self.assertEqual(urls, {"https://blog.example/a?id=1", "https://blog.example/b"})
        self.assertEqual(Article.objects.count(), 2)
        self.assertEqual(counts["skipped_articles"], 1)  # le lien « javascript: » est écarté

    def test_l_historique_est_deja_envoye(self):
        """Sinon le premier résumé renverrait tout l'historique importé."""
        import_legacy(now=WINTER)
        self.assertEqual(Article.objects.filter(digested_at__isnull=True).count(), 0)
        theme = Theme.objects.get(name="Cybersécurité")
        self.assertEqual(digest.candidates(theme, datetime(2026, 1, 5, 12, 0, tzinfo=UTC)), [])

    def test_dates_naives_traitees_comme_utc(self):
        import_legacy(now=WINTER)
        article = Article.objects.get(url="https://blog.example/a?id=1")
        self.assertEqual(article.fetched_at, datetime(2026, 1, 2, 11, 0, tzinfo=UTC))

    def test_retours_importes_note_invalide_ecartee(self):
        import_legacy(now=WINTER)
        feedback = Feedback.objects.get()
        self.assertEqual((feedback.rating, feedback.origin), (1, "legacy"))
        self.assertTrue(feedback.author.startswith("legacy:"))

    def test_les_webhooks_de_l_ancienne_base_ne_sont_jamais_repris(self):
        """Ces URL étaient stockées en clair en base : ce sont des secrets, qui passent en variables d'environnement."""
        import_legacy(now=WINTER)
        everything = str(
            [list(model.objects.values()) for model in (Theme, Source, Article, Analysis, Feedback, Digest)]
        )
        self.assertNotIn("ANCIEN-SECRET", everything)
        self.assertNotIn("api/webhooks", everything)

    def test_idempotent(self):
        import_legacy(now=WINTER)
        before = [m.objects.count() for m in (Theme, Source, Article, Analysis, Feedback, Digest)]
        import_legacy(now=WINTER)
        self.assertEqual([m.objects.count() for m in (Theme, Source, Article, Analysis, Feedback, Digest)], before)

    def test_simulation_ne_conserve_rien(self):
        counts = import_legacy(dry_run=True, now=WINTER)
        self.assertEqual(counts["themes"], 2)
        self.assertEqual(Theme.objects.count() + Article.objects.count() + Source.objects.count(), 0)


class SafetyTests(TestCase):
    def test_noms_de_schema_hostiles_refuses(self):
        for bad in ("", "legacy; DROP SCHEMA public", 'a"b', "A-B", "x" * 70, "../x", "legacy--", "1abc"):
            with self.subTest(schema=bad), self.assertRaises(LegacyError):
                import_legacy(bad)

    def test_schema_ou_tables_absents(self):
        with self.assertRaises(LegacyError) as ctx:
            import_legacy("inexistant")
        self.assertIn("tables absentes", str(ctx.exception))

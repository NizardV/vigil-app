from datetime import UTC, datetime, timedelta

from django.test import SimpleTestCase, TestCase

from core.netfetch import FetchError

from . import feeds
from .models import Article, Source, Theme

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Blog</title>
<item><title>Premier article</title><link>https://blog.example/a?utm_source=x&amp;id=1#haut</link>
<description>&lt;p&gt;Du contenu&lt;/p&gt;</description><pubDate>Fri, 06 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Second</title><link>https://blog.example/b</link></item>
<item><title>Sans lien</title></item>
<item><title>Lien dangereux</title><link>javascript:alert(1)</link></item>
</channel></rss>"""


class NormalizeTests(SimpleTestCase):
    def test_retire_suivi_et_fragment_garde_le_reste(self):
        url = "HTTPS://Blog.Example/a?utm_source=x&utm_medium=y&id=1&fbclid=z&ref=tw#haut"
        self.assertEqual(feeds.normalize_url(url), "https://blog.example/a?id=1")

    def test_schemas_dangereux_ou_vides_donnent_chaine_vide(self):
        for url in (
            "javascript:alert(1)",
            "ftp://x/y",
            "file:///etc/passwd",
            "",
            "https://",
            "data:text/html,x",
        ):
            self.assertEqual(feeds.normalize_url(url), "", url)

    def test_longueur_bornee(self):
        self.assertLessEqual(len(feeds.normalize_url("https://x.example/" + "a" * 5000)), feeds.URL_LIMIT)


class DetectTests(SimpleTestCase):
    def test_types(self):
        channel = "UC" + "a" * 22
        cases = {
            f"https://www.youtube.com/channel/{channel}": ("youtube", f"https://www.youtube.com/feeds/videos.xml?channel_id={channel}"),
            "https://www.youtube.com/@un.createur": ("youtube", ""),  # à résoudre
            "https://www.reddit.com/r/devops/": ("reddit", "https://www.reddit.com/r/devops/top/.rss?t=week"),
            "https://github.com/NizardV/vigil": ("github", "https://github.com/NizardV/vigil/releases.atom"),
            "https://github.com/NizardV/vigil.git": ("github", "https://github.com/NizardV/vigil/releases.atom"),
            "https://github.com/NizardV/vigil/releases.atom": ("rss", "https://github.com/NizardV/vigil/releases.atom"),
            "https://blog.example/feed.xml": ("rss", "https://blog.example/feed.xml"),
            "https://blog.example/": ("rss", "https://blog.example/"),
        }  # fmt: skip
        for url, expected in cases.items():
            self.assertEqual(feeds.detect_source(url), expected, url)

    def test_hacker_news_mot_cle_encode(self):
        kind, url = feeds.detect_source("hn:rust & c++ ?x=1")
        self.assertEqual(kind, "hackernews")
        self.assertNotIn(" ", url)
        self.assertNotIn("&c", url.replace("&points", ""))  # l'esperluette saisie ne casse pas la requête


class ParseTests(SimpleTestCase):
    def test_flux_valide(self):
        entries = feeds.parse_entries(RSS)
        self.assertEqual([e["title"] for e in entries], ["Premier article", "Second"])
        self.assertEqual(entries[0]["url"], "https://blog.example/a?id=1")  # suivi et fragment retirés
        self.assertEqual(entries[0]["published_at"], datetime(2026, 11, 6, 10, 0, tzinfo=UTC))

    def test_liens_absents_ou_dangereux_ecartes(self):
        urls = [e["url"] for e in feeds.parse_entries(RSS)]
        self.assertFalse(any(u.startswith("javascript") or not u for u in urls))

    def test_contenu_qui_n_est_pas_un_flux(self):
        for body in (b"<html><body>Page web</body></html>", b"", b"\x00\x01\x02"):
            with self.assertRaises(ValueError):
                feeds.parse_entries(body)

    def test_nombre_d_articles_borne(self):
        items = "".join(f"<item><title>t{i}</title><link>https://x.example/{i}</link></item>" for i in range(50))
        body = f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{items}</channel></rss>'.encode()
        self.assertEqual(len(feeds.parse_entries(body)), feeds.MAX_PER_SOURCE)


class FetchTests(TestCase):
    def setUp(self):
        self.theme = Theme.objects.create(name="IA", slug="ia")
        self.source = Source.objects.create(theme=self.theme, url="https://blog.example/feed.xml", name="Blog")

    def test_creation_et_idempotence(self):
        fetch = lambda url, **kw: RSS  # noqa: E731
        self.assertEqual(feeds.fetch_source(self.source, fetch=fetch, now=NOW), 2)
        self.assertEqual(feeds.fetch_source(self.source, fetch=fetch, now=NOW), 0)  # rien de nouveau
        self.assertEqual(Article.objects.count(), 2)
        self.source.refresh_from_db()
        self.assertEqual((self.source.last_fetched_at, self.source.failures, self.source.last_error), (NOW, 0, ""))

    def _feed(self, days_ago: list[int]) -> bytes:
        from email.utils import format_datetime

        items = "".join(
            f"<item><title>t{i}</title><link>https://x.example/{i}</link>"
            f"<pubDate>{format_datetime(NOW - timedelta(days=d))}</pubDate></item>"
            for i, d in enumerate(days_ago)
        )
        return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{items}</channel></rss>'.encode()

    def test_articles_trop_anciens_pour_le_resume_ignores(self):
        """Publié avant la période du résumé (7 j) : il n'y figurera jamais, inutile de payer son analyse."""
        self.theme.lookback_days = 7  # comme les thèmes en production (résumé hebdomadaire)
        self.theme.save()
        self.source.refresh_from_db()
        body = self._feed([1, 3, 6, 8, 30, 200])
        self.assertEqual(feeds.fetch_source(self.source, fetch=lambda url, **kw: body, now=NOW), 3)
        self.assertEqual(sorted(Article.objects.values_list("title", flat=True)), ["t0", "t1", "t2"])

    def test_au_plus_dix_nouveaux_articles_par_collecte_les_plus_recents(self):
        body = self._feed([0] * 5 + [1] * 5 + [2] * 5)  # 15 articles récents, du plus récent au plus ancien
        self.assertEqual(
            feeds.fetch_source(self.source, fetch=lambda url, **kw: body, now=NOW), feeds.MAX_NEW_PER_FETCH
        )
        self.assertFalse(Article.objects.filter(title__in=["t10", "t11", "t12", "t13", "t14"]).exists())

    def test_intervalle_par_defaut_deux_collectes_par_semaine(self):
        self.assertEqual(self.source.fetch_interval_hours, 84)

    def test_meme_article_avec_suivi_different_n_est_pas_duplique(self):
        feeds.fetch_source(self.source, fetch=lambda url, **kw: RSS, now=NOW)
        variant = RSS.replace(b"utm_source=x&amp;id=1", b"id=1&amp;utm_campaign=nl")
        feeds.fetch_source(self.source, fetch=lambda url, **kw: variant, now=NOW)
        self.assertEqual(Article.objects.count(), 2)

    def test_resolution_d_une_chaine_youtube_nommee(self):
        source = Source.objects.create(theme=self.theme, url="https://www.youtube.com/@un.createur")
        page = b'... "externalId":"UC' + b"b" * 22 + b'" ...'
        self.assertEqual(
            feeds.ensure_feed_url(source, fetch=lambda url, **kw: page),
            "https://www.youtube.com/feeds/videos.xml?channel_id=UC" + "b" * 22,
        )
        source.refresh_from_db()
        self.assertEqual(source.kind, "youtube")  # mémorisé : plus de résolution ensuite

    def test_chaine_youtube_introuvable(self):
        source = Source.objects.create(theme=self.theme, url="https://www.youtube.com/@inconnu")
        with self.assertRaises(ValueError):
            feeds.ensure_feed_url(source, fetch=lambda url, **kw: b"<html>rien</html>")


class DueTests(TestCase):
    def setUp(self):
        self.theme = Theme.objects.create(name="IA", slug="ia")

    def source(self, url, **kw):
        return Source.objects.create(theme=self.theme, url=url, **kw)

    def test_intervalle(self):
        jamais = self.source("https://a.example/f.xml")
        recent = self.source(
            "https://b.example/f.xml", last_fetched_at=NOW - timedelta(hours=1), fetch_interval_hours=6
        )
        ancien = self.source(
            "https://c.example/f.xml", last_fetched_at=NOW - timedelta(hours=7), fetch_interval_hours=6
        )
        limite = self.source(
            "https://d.example/f.xml",
            last_fetched_at=NOW - timedelta(hours=5, minutes=58),
            fetch_interval_hours=6,
        )
        self.assertEqual(
            [feeds.is_due(s, NOW) for s in (jamais, recent, ancien, limite)], [True, False, True, True]
        )  # 5 min de marge : un passage horaire ne rate pas une source à 5 h 58

    def test_une_source_en_echec_n_empeche_pas_les_autres(self):
        bonne = self.source("https://bonne.example/f.xml")
        mauvaise = self.source("https://mauvaise.example/f.xml")

        def fetch(url, **kw):
            if "mauvaise" in url:
                raise FetchError("l'adresse n'est pas publique (réseau interne refusé)")
            return RSS

        summary = feeds.fetch_due_sources(fetch=fetch, now=NOW)
        self.assertEqual((summary["due"], summary["fetched"], summary["failed"], summary["new_articles"]), (2, 1, 1, 2))
        mauvaise.refresh_from_db()
        bonne.refresh_from_db()
        self.assertEqual((mauvaise.failures, "refusé" in mauvaise.last_error), (1, True))
        self.assertEqual(bonne.failures, 0)

    def test_source_suspendue_apres_trop_d_echecs(self):
        self.source("https://morte.example/f.xml", failures=feeds.MAX_FAILURES)
        summary = feeds.fetch_due_sources(fetch=lambda url, **kw: RSS, now=NOW)
        self.assertEqual(summary["due"], 0)

    def test_source_inactive_ignoree(self):
        self.source("https://off.example/f.xml", active=False)
        self.assertEqual(feeds.fetch_due_sources(fetch=lambda url, **kw: RSS, now=NOW)["due"], 0)

    def test_succes_remet_les_echecs_a_zero(self):
        source = self.source("https://x.example/f.xml", failures=4, last_error="ancienne erreur")
        feeds.fetch_due_sources(fetch=lambda url, **kw: RSS, now=NOW)
        source.refresh_from_db()
        self.assertEqual((source.failures, source.last_error), (0, ""))

    def test_flux_illisible_est_un_echec_enregistre(self):
        source = self.source("https://x.example/f.xml")
        feeds.fetch_due_sources(fetch=lambda url, **kw: b"<html>pas un flux</html>", now=NOW)
        source.refresh_from_db()
        self.assertEqual(source.failures, 1)
        self.assertIn("flux", source.last_error)

from datetime import UTC, date, datetime, timedelta

from django.test import TestCase, override_settings

from core.llm import LLMError
from core.notifications import NotificationError

from . import digest, services
from .models import Analysis, Article, Digest, Source, Theme

NOW = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)  # 07:00 à Paris (heure d'hiver, UTC+1)
HOOK = "https://discord.com/api/webhooks/1/token"


class Recorder:
    """Faux envois Discord : mémorisent les messages, et peuvent échouer au N-ième envoi."""

    def __init__(self, fail_at=None):
        self.fail_at, self.sent = fail_at, []

    def _send(self, message):
        if self.fail_at is not None and len(self.sent) == self.fail_at:
            raise NotificationError("Discord a répondu 500")
        self.sent.append(message)

    def webhook(self, url, message):
        self._send(message)

    def bot(self, channel_id, message, token):
        self._send(message)


class FakeLLM:
    model = "gemini-test"

    def __init__(self, text="Intro. Points clés. Conclusion.", error=None):
        self.text, self.error = text, error

    def generate(self, *, system, user, **kwargs):
        if self.error:
            raise self.error
        return self.text


class Base(TestCase):
    def setUp(self):
        self.theme = Theme.objects.create(name="IA", slug="ia", digest_hour=7, min_score=5)
        self.source = Source.objects.create(theme=self.theme, url="https://x.example/f.xml")

    def article(self, n, score=8.0, hours_ago=2, **kw):
        article = Article.objects.create(
            source=self.source, title=f"Article {n}", url=f"https://x.example/{n}", processed=True,
            fetched_at=NOW - timedelta(hours=hours_ago), **kw,
        )  # fmt: skip
        Analysis.objects.create(article=article, summary=f"Résumé {n}", relevance_score=score)
        return article

    def send(self, recorder, *, llm=None, now=NOW, **kw):
        return digest.send_theme_digest(
            self.theme, llm=llm, now=now, send_webhook=recorder.webhook, send_bot=recorder.bot, pause=0, **kw
        )


class DueTests(Base):
    def test_heure_de_paris_hiver(self):
        self.assertEqual(digest.due_themes(NOW), [self.theme])  # 06:00 UTC = 07:00 à Paris
        self.assertEqual(digest.due_themes(NOW + timedelta(hours=1)), [])

    def test_heure_de_paris_ete(self):
        """En été Paris est en UTC+2 : 05:00 UTC = 07:00 à Paris, et 06:00 UTC = 08:00 (trop tard)."""
        self.assertEqual(digest.due_themes(datetime(2026, 7, 7, 5, 0, tzinfo=UTC)), [self.theme])
        self.assertEqual(digest.due_themes(datetime(2026, 7, 7, 6, 0, tzinfo=UTC)), [])

    def test_deja_envoye_aujourd_hui(self):
        self.theme.last_digest_on = date(2026, 11, 7)
        self.theme.save()
        self.assertEqual(digest.due_themes(NOW), [])  # déjà parti aujourd'hui
        self.assertEqual(digest.due_themes(NOW + timedelta(hours=1)), [])  # et ce n'est plus l'heure
        # Le lendemain à 07:00 (heure de Paris), le thème redevient dû.
        self.assertEqual(digest.due_themes(datetime(2026, 11, 8, 6, 0, tzinfo=UTC)), [self.theme])

    def test_theme_desactive(self):
        self.theme.digest_enabled = False
        self.theme.save()
        self.assertEqual(digest.due_themes(NOW), [])


class CandidatesTests(Base):
    def test_filtres(self):
        self.article(1, score=9)  # ok
        self.article(2, score=4.9)  # sous le score minimal
        self.article(3, score=9, hours_ago=60)  # trop vieux (fenêtre de 48 h)
        sent = self.article(4, score=9)
        sent.digested_at = NOW
        sent.save()  # déjà envoyé
        self.assertEqual([c.article.title for c in digest.candidates(self.theme, NOW)], ["Article 1"])

    def test_un_article_collecte_aujourd_hui_mais_publie_il_y_a_des_mois_est_exclu(self):
        """Première collecte d'un flux : les archives ne doivent pas finir dans le résumé."""
        self.article(1, published_at=NOW - timedelta(days=120))
        self.article(2, published_at=NOW - timedelta(hours=5))
        self.article(3)  # sans date de publication : on se rabat sur la date de collecte
        titles = {c.article.title for c in digest.candidates(self.theme, NOW)}
        self.assertEqual(titles, {"Article 2", "Article 3"})

    def test_autre_theme_exclu(self):
        other = Theme.objects.create(name="Autre", slug="autre")
        source = Source.objects.create(theme=other, url="https://y.example/f.xml")
        article = Article.objects.create(source=source, title="Ailleurs", url="https://y.example/1", fetched_at=NOW)
        Analysis.objects.create(article=article, summary="x", relevance_score=9)
        self.assertEqual(digest.candidates(self.theme, NOW), [])

    def test_meilleurs_d_abord_et_borne(self):
        for n in range(15):
            self.article(n, score=5 + n / 10)
        got = digest.candidates(self.theme, NOW)
        self.assertEqual(len(got), digest.LIMIT)
        self.assertEqual(got[0].relevance_score, max(c.relevance_score for c in got))


class SendTests(Base):
    @override_settings(DISCORD_WEBHOOKS={"watch-ia": HOOK}, DISCORD_BOT_TOKEN="")
    def test_webhook_sans_boutons_et_marquage(self):
        a1, a2 = self.article(1), self.article(2)
        rec = Recorder()
        result = self.send(rec, llm=FakeLLM())
        self.assertEqual((result["status"], result["mode"], result["articles"]), ("sent", "webhook", 2))
        self.assertEqual(len(rec.sent), 3)  # l'en-tête + 2 articles
        self.assertTrue(all("components" not in m for m in rec.sent))  # un webhook ne peut pas porter de boutons
        for article in (a1, a2):
            article.refresh_from_db()
            self.assertEqual(article.digested_at, NOW)
        self.assertEqual(Digest.objects.get().article_count, 2)
        self.theme.refresh_from_db()
        self.assertEqual(self.theme.last_digest_on, date(2026, 11, 7))

    @override_settings(DISCORD_WEBHOOKS={}, DISCORD_BOT_TOKEN="bot-key")  # noqa: S106
    def test_bot_avec_boutons(self):
        self.theme.discord_channel_id = "123456789012345678"
        self.theme.save()
        article = self.article(1)
        rec = Recorder()
        result = self.send(rec, llm=None)
        self.assertEqual(result["mode"], "bot")
        self.assertEqual(len(rec.sent), 1)  # un seul message : résumé + articles + boutons
        message = rec.sent[0]
        self.assertEqual(message["flags"], digest.IS_COMPONENTS_V2)
        rows = [b for b in message["components"][0]["components"] if b["type"] == digest.ACTION_ROW]
        buttons = rows[0]["components"]
        self.assertEqual(
            [b["custom_id"] for b in buttons[:2]], [f"watch:fb:{article.id}:1", f"watch:fb:{article.id}:-1"]
        )
        self.assertTrue(buttons[2]["url"].startswith("https://"))

    @override_settings(DISCORD_WEBHOOKS={}, DISCORD_BOT_TOKEN="bot-key")  # noqa: S106
    def test_bot_message_unique_dans_les_limites_de_discord(self):
        """Components V2 : 40 éléments au plus et 4 000 caractères de texte ; chaque article sous l'en-tête."""
        self.theme.discord_channel_id = "123456789012345678"
        self.theme.save()
        for n in range(10):
            a = self.article(n)
            Analysis.objects.filter(article=a).update(summary="Très long résumé. " * 80)
        rec = Recorder()
        result = self.send(rec, llm=FakeLLM(text="Tendance. " * 400))
        message = rec.sent[0]
        blocks = message["components"][0]["components"]

        def count(items):
            return sum(1 + count(i.get("components", [])) for i in items)

        self.assertLessEqual(count(message["components"]), 40)
        texts = [b["content"] for b in blocks if b["type"] == digest.TEXT_DISPLAY]
        self.assertLessEqual(sum(len(t) for t in texts), 4000)
        rows = [b for b in blocks if b["type"] == digest.ACTION_ROW]
        self.assertLessEqual(len(rows), digest.LIMIT)
        self.assertEqual(result["articles"], len(rows))  # seuls les articles affichés sont marqués comme envoyés
        self.assertEqual(Article.objects.filter(digested_at=NOW).count(), len(rows))
        self.assertTrue(texts[0].startswith("## 📰 Veille — IA"))

    @override_settings(DISCORD_WEBHOOKS={"watch-ia": HOOK}, DISCORD_BOT_TOKEN="")
    def test_un_article_n_est_jamais_renvoye(self):
        """L'ancien code renvoyait chaque jour les 10 meilleurs de tous les temps. Ici : une seule fois."""
        self.article(1)
        first, second = Recorder(), Recorder()
        self.send(first, llm=None)
        later = NOW + timedelta(days=1)
        self.send(second, llm=None, now=later)
        self.assertEqual((len(first.sent), len(second.sent)), (2, 0))
        self.assertEqual(Digest.objects.count(), 1)

    @override_settings(DISCORD_WEBHOOKS={}, DISCORD_BOT_TOKEN="")
    def test_salon_non_configure_ne_marque_rien(self):
        article = self.article(1)
        rec = Recorder()
        self.assertEqual(self.send(rec)["status"], "not_configured")
        article.refresh_from_db()
        self.assertEqual((rec.sent, article.digested_at), ([], None))  # sera envoyé quand le salon existera

    @override_settings(DISCORD_WEBHOOKS={"watch-ia": HOOK}, DISCORD_BOT_TOKEN="")
    def test_echec_en_cours_de_route_ne_perd_ni_ne_duplique(self):
        a1, a2, a3 = self.article(1, score=9), self.article(2, score=8), self.article(3, score=7)
        rec = Recorder(fail_at=2)  # l'en-tête et le 1er article partent, le 2e échoue
        result = self.send(rec)
        self.assertEqual((result["status"], result["sent"]), ("error", 1))
        for article in (a1, a2, a3):
            article.refresh_from_db()
        self.assertIsNotNone(a1.digested_at)  # déjà livré : jamais renvoyé
        self.assertIsNone(a2.digested_at)
        self.assertIsNone(a3.digested_at)  # sera repris au passage suivant
        self.theme.refresh_from_db()
        self.assertIsNone(self.theme.last_digest_on)  # donc le thème reste « dû »
        self.assertEqual(Digest.objects.count(), 0)

    @override_settings(DISCORD_WEBHOOKS={"watch-ia": HOOK}, DISCORD_BOT_TOKEN="")
    def test_echec_de_l_en_tete_ne_marque_rien(self):
        article = self.article(1)
        result = self.send(Recorder(fail_at=0))
        article.refresh_from_db()
        self.assertEqual((result["status"], result["sent"], article.digested_at), ("error", 0, None))

    @override_settings(DISCORD_WEBHOOKS={"watch-ia": HOOK}, DISCORD_BOT_TOKEN="")
    def test_simulation_n_envoie_ni_ne_marque(self):
        article = self.article(1)
        rec = Recorder()
        result = self.send(rec, dry_run=True)
        article.refresh_from_db()
        self.assertEqual((result["status"], rec.sent, article.digested_at), ("dry_run", [], None))
        self.assertEqual(len(result["messages"]), 2)

    def test_rien_a_envoyer_marque_le_jour_comme_traite(self):
        self.assertEqual(self.send(Recorder())["status"], "empty")
        self.theme.refresh_from_db()
        self.assertEqual(self.theme.last_digest_on, date(2026, 11, 7))  # pas de nouvel essai toute l'heure


class TextTests(Base):
    def test_panne_du_modele_le_resume_part_quand_meme(self):
        self.article(1)
        text = digest.digest_text(
            self.theme, digest.candidates(self.theme, NOW), FakeLLM(error=LLMError("Gemini injoignable"))
        )
        self.assertIn("1 article(s)", text)

    def test_les_liens_generes_sont_retires(self):
        """Un texte produit à partir de contenu externe ne doit pas pouvoir t'envoyer vers un lien piégé."""
        self.article(1)
        llm = FakeLLM("Voir [la source](https://evil.example/phishing) et [autre](http://x.example).")
        text = digest.digest_text(self.theme, digest.candidates(self.theme, NOW), llm)
        self.assertNotIn("evil.example", text)
        self.assertIn("la source", text)

    def test_mentions_neutralisees_dans_tous_les_messages(self):
        article = self.article(1)
        article.title = "@everyone **urgent** [clique](http://evil.example)"
        article.save()
        analysis = Analysis.objects.get(article=article)
        analysis.summary = "@here regarde <@123456>"
        analysis.save()
        message = digest.article_message(Analysis.objects.get(article=article), self.theme, buttons=False)
        embed = message["embeds"][0]
        self.assertNotIn("@everyone", embed["title"])
        self.assertNotIn("@here", embed["description"])
        header = digest.header_message(self.theme, "@everyone bonjour", 1)
        self.assertNotIn("@everyone", header["embeds"][0]["description"])

    def test_taille_bornee(self):
        self.article(1)
        header = digest.header_message(self.theme, "x" * 20000, 1)
        self.assertLessEqual(len(header["embeds"][0]["description"]), 4096)


class RunTests(Base):
    @override_settings(DISCORD_WEBHOOKS={"watch-ia": HOOK}, DISCORD_BOT_TOKEN="", GEMINI_API_KEY="")
    def test_deux_passages_le_meme_jour_n_envoient_qu_une_fois(self):
        self.article(1)
        from unittest import mock

        rec = Recorder()
        with mock.patch.object(digest, "send_discord", rec.webhook), mock.patch("time.sleep"):
            first = services.run_digests(now=NOW)
            second = services.run_digests(now=NOW)
        self.assertEqual((first["themes"], second["themes"]), (1, 0))
        self.assertEqual(len(rec.sent), 2)

    @override_settings(DISCORD_WEBHOOKS={}, DISCORD_BOT_TOKEN="", GEMINI_API_KEY="")
    def test_theme_cible_et_simulation(self):
        self.article(1)
        result = services.run_digests(now=NOW + timedelta(hours=5), only="ia", force=True, dry_run=True)
        self.assertEqual((result["themes"], result["results"][0]["status"]), (1, "dry_run"))
        self.assertEqual(services.run_digests(now=NOW + timedelta(hours=5), only="ia")["themes"], 0)  # pas l'heure

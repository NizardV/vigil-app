from unittest import mock

from django.test import TestCase, override_settings

from . import services
from .models import Analysis, Article, Feedback, Source, Theme


class ServicesTests(TestCase):
    def setUp(self):
        theme = Theme.objects.create(name="IA", slug="ia")
        source = Source.objects.create(theme=theme, url="https://x.example/f.xml")
        self.source = source

    def article(self, n, title, summary="", score=7):
        article = Article.objects.create(source=self.source, title=title, url=f"https://x.example/{n}", processed=True)
        Analysis.objects.create(article=article, summary=summary, relevance_score=score)
        return article

    def test_retour_remplace_le_precedent(self):
        article = self.article(1, "A")
        services.record_feedback(article.id, 1, "discord:1")
        services.record_feedback(article.id, -1, "discord:1")
        self.assertEqual(Feedback.objects.get().rating, -1)

    def test_note_invalide_refusee(self):
        article = self.article(1, "A")
        for bad in (0, 2, -2, 10, None):
            with self.assertRaises(ValueError):
                services.record_feedback(article.id, bad, "x")
        self.assertEqual(Feedback.objects.count(), 0)

    def test_article_inconnu(self):
        self.assertIsNone(services.record_feedback(999999, 1, "x"))

    def test_auteur_borne(self):
        article = self.article(1, "A")
        self.assertEqual(len(services.record_feedback(article.id, 1, "x" * 500).author), 100)

    def test_recherche_plein_texte(self):
        self.article(1, "Faille critique dans OpenSSH", "Une vulnérabilité permet l'exécution de code")
        self.article(2, "Nouvelle version de Django", "Support de Python 3.13")
        self.article(3, "Recette de crêpes", "Farine et oeufs")
        titles = lambda q: [a.title for a in services.search_articles(q)]  # noqa: E731
        self.assertEqual(titles("openssh"), ["Faille critique dans OpenSSH"])
        self.assertEqual(titles("python"), ["Nouvelle version de Django"])  # trouvé dans le RÉSUMÉ
        self.assertEqual(titles("inexistant"), [])

    def test_recherche_titre_prioritaire_sur_resume(self):
        self.article(1, "Autre sujet", "parle de rust dans le résumé")
        self.article(2, "Rust 2.0 est sorti", "Une grosse version")
        self.assertEqual(services.search_articles("rust")[0].title, "Rust 2.0 est sorti")

    def test_recherche_requetes_hostiles_ou_vides_sans_exception(self):
        self.article(1, "Article", "résumé")
        for query in (
            "",
            "   ",
            "'; DROP TABLE watch_article; --",
            '"',
            "a & | ! ( )",
            "x" * 5000,
            "\x00",
            "é\U0001f600",
        ):
            list(services.search_articles(query))
        self.assertEqual(Article.objects.count(), 1)

    @override_settings(GEMINI_API_KEY="")
    def test_analyse_sans_cle_ne_fait_rien_et_le_dit(self):
        self.assertEqual(services.run_analysis(), {"status": "not_configured"})

    @override_settings(GEMINI_API_KEY="cle", GEMINI_MODEL="gemini-2.5-flash")
    def test_analyse_avec_cle_construit_le_client(self):
        with mock.patch("modules.watch.services.analysis.process_pending", return_value={"analyzed": 2}) as run:
            result = services.run_analysis()
        self.assertEqual(result, {"status": "ok", "analyzed": 2})
        self.assertEqual(run.call_args.args[0].model, "gemini-2.5-flash")


class TaskTests(TestCase):
    def test_taches_enregistrees_avec_leurs_horaires_utc(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        crons = {name: p.cron for (name, _), p in app.periodic_registry.periodic_tasks.items()}
        self.assertEqual(crons["watch.fetch_sources"], "7 * * * *")
        self.assertEqual(crons["watch.analyze_pending"], "17,37,57 * * * *")
        self.assertEqual(crons["watch.send_digests"], "3 * * * *")

    def test_analyse_interrompue_fait_echouer_la_tache_pour_alerter(self):
        """Sinon la veille s'arrêterait en silence quand la clé Gemini expire ou que le quota est atteint."""
        from . import tasks

        stopped = {"status": "ok", "analyzed": 0, "stopped": "Gemini a répondu 401"}
        with mock.patch("modules.watch.tasks.services.run_analysis", return_value=stopped):
            with self.assertRaises(RuntimeError) as ctx:
                tasks.analyze_pending(timestamp=0)
        self.assertIn("401", str(ctx.exception))

    def test_analyse_normale_et_non_configuree_ne_levent_pas(self):
        from . import tasks

        for result in ({"status": "ok", "analyzed": 3, "stopped": ""}, {"status": "not_configured"}):
            with mock.patch("modules.watch.tasks.services.run_analysis", return_value=result):
                self.assertEqual(tasks.analyze_pending(timestamp=0), result)

    def test_resume_non_envoye_fait_echouer_la_tache(self):
        from . import tasks

        failed = {"status": "error", "themes": 1, "results": [{"status": "error", "theme": "ia"}]}
        with mock.patch("modules.watch.tasks.services.run_digests", return_value=failed):
            with self.assertRaises(RuntimeError) as ctx:
                tasks.send_digests(timestamp=0)
        self.assertIn("ia", str(ctx.exception))

    def test_une_source_en_echec_ne_fait_pas_echouer_la_collecte(self):
        from . import tasks

        summary = {"due": 3, "fetched": 2, "failed": 1, "new_articles": 5}
        with mock.patch("modules.watch.tasks.services.run_fetch", return_value=summary):
            self.assertEqual(tasks.fetch_sources(timestamp=0), summary)

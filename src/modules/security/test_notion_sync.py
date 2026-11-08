from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from core.notion import NotionError

from . import notion_sync
from .models import CheckResult, Finding
from .notion_sync import MARKER, build_blocks, render_text, run_notion_sync, sync_page
from .outcomes import OK
from .services import record_outcome
from .tests import outcome

PAGE = "394e0621-1f6f-80b5-9cc0-edddfab1ecaa"


def human(n):
    """Un bloc écrit à la main par l'utilisateur (au-dessus du repère)."""
    return {
        "id": f"human-{n}",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": f"note {n}"}]},
    }


def marker_block():
    return {"id": "marker", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": MARKER}]}}


def generated(n):
    return {
        "id": f"gen-{n}",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": f"ancien {n}"}]},
    }


class FakeNotion:
    """Notion factice AVEC ÉTAT : la page contient `children`, et les appels la modifient réellement."""

    def __init__(self, children, fail_append=False):
        self.children, self.calls, self.fail_append, self.counter = list(children), [], fail_append, 0

    def list_children(self, block_id):
        self.calls.append(("list", block_id))
        return list(self.children)

    def append_children(self, block_id, blocks):
        self.calls.append(("append", len(blocks)))
        if self.fail_append:
            raise NotionError(500, "boom")
        for block in blocks:
            self.counter += 1
            kind = block["type"]
            text = "".join(item["text"]["content"] for item in block[kind]["rich_text"])
            self.children.append(
                {"id": f"new-{self.counter}", "type": kind, kind: {"rich_text": [{"plain_text": text}]}}
            )

    def delete_block(self, block_id):
        self.calls.append(("delete", block_id))
        self.children = [b for b in self.children if b["id"] != block_id]

    def deleted(self):
        return [arg for name, arg in self.calls if name == "delete"]


def new_blocks():
    return [notion_sync.heading(MARKER, level=2), notion_sync.paragraph(notion_sync._text("contenu"))]


class SyncPageTests(TestCase):
    def test_repere_absent_ajoute_en_fin_de_page_sans_rien_supprimer(self):
        notion = FakeNotion([human(1), human(2)])
        result = sync_page(notion, PAGE, new_blocks())
        self.assertEqual((result["created_marker"], result["deleted"]), (True, 0))
        self.assertEqual(notion.deleted(), [])
        self.assertEqual([b["id"] for b in notion.children[:2]], ["human-1", "human-2"])  # tes notes intactes, en tête

    def test_le_contenu_ecrit_a_la_main_n_est_jamais_touche(self):
        notion = FakeNotion([human(1), human(2), marker_block(), generated(1), generated(2)])
        sync_page(notion, PAGE, new_blocks())
        self.assertEqual(notion.deleted(), ["gen-1", "gen-2"])  # uniquement la zone générée
        ids = [b["id"] for b in notion.children]
        self.assertEqual(ids[:3], ["human-1", "human-2", "marker"])

    def test_ajoute_d_abord_supprime_ensuite(self):
        notion = FakeNotion([marker_block(), generated(1)])
        sync_page(notion, PAGE, new_blocks())
        names = [name for name, _ in notion.calls]
        self.assertLess(names.index("append"), names.index("delete"))

    def test_echec_de_l_ajout_ne_supprime_rien(self):
        """Une panne ne doit jamais laisser la page vide ni effacer la version précédente."""
        notion = FakeNotion([marker_block(), generated(1)], fail_append=True)
        with self.assertRaises(NotionError):
            sync_page(notion, PAGE, new_blocks())
        self.assertEqual(notion.deleted(), [])
        self.assertEqual([b["id"] for b in notion.children], ["marker", "gen-1"])

    def test_garde_fou_contre_une_suppression_massive(self):
        notion = FakeNotion([marker_block(), *[generated(i) for i in range(notion_sync.MAX_DELETE + 1)]])
        with self.assertRaises(NotionError):
            sync_page(notion, PAGE, new_blocks())
        self.assertEqual([name for name, _ in notion.calls if name in ("append", "delete")], [])

    def test_idempotent_deux_synchros_ne_s_empilent_pas(self):
        notion = FakeNotion([human(1)])
        sync_page(notion, PAGE, new_blocks())
        after_first = len(notion.children)
        sync_page(notion, PAGE, new_blocks())
        sync_page(notion, PAGE, new_blocks())
        self.assertEqual(len(notion.children), after_first)  # même taille : la zone est remplacée, pas dupliquée
        self.assertEqual(notion.children[0]["id"], "human-1")


class BuildBlocksTests(TestCase):
    def finding(
        self,
        key,
        severity=Finding.Severity.HIGH,
        title="faille",
        target="NizardV/demo",
        url="https://x.test/1",
    ):
        return Finding.objects.create(
            fingerprint=f"fp-{key}", source="github", target=target, check_name="dependabot",
            severity=severity, title=title, url=url, status=Finding.Status.OPEN,
        )  # fmt: skip

    def test_le_repere_est_le_premier_bloc(self):
        record_outcome(outcome())
        first = build_blocks()[0]
        self.assertEqual((first["type"], first["heading_2"]["rich_text"][0]["text"]["content"]), ("heading_2", MARKER))

    def test_contenu_et_doublons_avec_multiplicateur(self):
        record_outcome(outcome(status=OK))
        for key in ("1", "2"):
            self.finding(key, Finding.Severity.CRITICAL, "proxy-addr : usurpation")
        self.finding("3", Finding.Severity.MEDIUM, "autre")
        text = render_text(build_blocks())
        self.assertIn("Critique : 2", text)
        self.assertIn("proxy-addr : usurpation ×2", text)
        self.assertEqual(text.count("proxy-addr"), 1)

    def test_les_liens_non_https_sont_ecartes(self):
        record_outcome(outcome(status=OK))
        self.finding("1", title="lien http", url="http://evil.example/x")
        self.finding("2", title="lien https", url="https://github.com/x")
        links = {}
        for block in build_blocks():
            for item in block.get(block["type"], {}).get("rich_text", []):
                content = item["text"]
                if content["content"].startswith(("lien http", "lien https")):
                    links[content["content"]] = content.get("link")
        self.assertIsNone(links["lien http"])
        self.assertEqual(links["lien https"], {"url": "https://github.com/x"})

    def test_texte_borne_a_la_limite_de_l_api(self):
        """L'API Notion refuse un segment de plus de 2 000 caractères : on coupe avant d'envoyer."""
        text = notion_sync._text("x" * 5000)
        self.assertLessEqual(len(text["text"]["content"]), notion_sync.MAX_TEXT)
        # …et build_blocks respecte la limite, même avec un titre au maximum de la base (300).
        record_outcome(outcome(status=OK))
        self.finding("1", title="x" * 300)
        for block in build_blocks():
            for item in block.get(block["type"], {}).get("rich_text", []):
                self.assertLessEqual(len(item["text"]["content"]), notion_sync.MAX_TEXT)

    def test_audit_perime_signale(self):
        text = render_text(build_blocks())  # aucun audit enregistré
        self.assertIn("n'a pas tourné", text)

    def test_audit_recent_pas_d_avertissement(self):
        record_outcome(outcome())
        self.assertNotIn("n'a pas tourné", render_text(build_blocks()))

    def test_audit_ancien_signale(self):
        record_outcome(outcome())
        CheckResult.objects.update(checked_at=timezone.now() - timedelta(days=5))
        self.assertIn("n'a pas tourné", render_text(build_blocks()))

    def test_controles_non_couverts_affiches(self):
        CheckResult.objects.create(
            source="github", target="a/b", check_name="x", status="unavailable", detail="404: off"
        )
        text = render_text(build_blocks())
        self.assertIn("Contrôles non couverts", text)
        self.assertIn("404: off", text)

    def test_pr_qui_trainent(self):
        record_outcome(outcome(status=OK))
        for n in range(3):
            Finding.objects.create(
                fingerprint=f"s{n}",
                source="github",
                target="NizardV/cyna-web",
                check_name="stale_dependabot_prs",
                severity=Finding.Severity.MEDIUM,
                title="PR",
                status=Finding.Status.OPEN,
            )
        self.assertIn("cyna-web : 3 PR en attente", render_text(build_blocks()))


class RunTests(TestCase):
    @override_settings(NOTION_API_KEY="", NOTION_SECURITY_PAGE_ID=PAGE)
    def test_sans_jeton_rien_n_est_ecrit(self):
        notion = FakeNotion([])
        self.assertEqual(run_notion_sync(client=notion)["status"], "not_configured")
        self.assertEqual(notion.calls, [])

    @override_settings(NOTION_API_KEY="tok", NOTION_SECURITY_PAGE_ID="pas-un-id")
    def test_identifiant_de_page_invalide_refuse(self):
        notion = FakeNotion([])
        self.assertEqual(run_notion_sync(client=notion)["status"], "not_configured")
        self.assertEqual(notion.calls, [])

    @override_settings(NOTION_API_KEY="", NOTION_SECURITY_PAGE_ID="")
    def test_dry_run_n_appelle_pas_notion_meme_sans_configuration(self):
        notion = FakeNotion([])
        result = run_notion_sync(client=notion, dry_run=True)
        self.assertEqual((result["status"], notion.calls), ("dry_run", []))
        self.assertEqual(result["blocks"][0]["type"], "heading_2")

    @override_settings(NOTION_API_KEY="tok", NOTION_SECURITY_PAGE_ID=PAGE)
    def test_succes_et_erreur(self):
        ok = run_notion_sync(client=FakeNotion([human(1)]))
        self.assertEqual((ok["status"], ok["created_marker"]), ("synced", True))
        failed = run_notion_sync(client=FakeNotion([marker_block(), generated(1)], fail_append=True))
        self.assertEqual(failed["status"], "error")

    @override_settings(NOTION_API_KEY="tok", NOTION_SECURITY_PAGE_ID=PAGE)
    def test_la_tache_echoue_pour_etre_reessayee(self):
        from . import tasks

        with (
            mock.patch(
                "modules.security.tasks.notion_sync.run_notion_sync",
                return_value={"status": "error", "error": "x"},
            ),
            self.assertRaises(RuntimeError),
        ):
            tasks.sync_notion(timestamp=0)

    def test_tache_enregistree(self):
        from procrastinate.contrib.django import app

        app.perform_import_paths()
        self.assertIn("security.sync_notion", app.tasks)

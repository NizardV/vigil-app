import io
import json
import urllib.error
from unittest import mock

from django.test import SimpleTestCase

from .notion import NotionClient, NotionError, valid_id
from .tests import http_error

PAGE = "394e0621-1f6f-80b5-9cc0-edddfab1ecaa"
NOTION_KEY = "secret_TOKEN-VALUE"


def reply(payload: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode())


class IdTests(SimpleTestCase):
    def test_identifiants_valides(self):
        self.assertTrue(valid_id(PAGE))
        self.assertTrue(valid_id(PAGE.replace("-", "")))

    def test_identifiants_invalides(self):
        for bad in ("", "pas-un-id", "../../users", PAGE + "/../x", "394e0621-1f6f-80b5-9cc0-edddfab1ecaaZ"):
            self.assertFalse(valid_id(bad), bad)


class ClientTests(SimpleTestCase):
    def test_identifiant_invalide_refuse_sans_appel_reseau(self):
        with mock.patch("urllib.request.urlopen") as opened:
            for call in (
                lambda c: c.list_children("../x"),
                lambda c: c.append_children("../x", [{}]),
                lambda c: c.delete_block("../x"),
            ):
                with self.assertRaises(NotionError):
                    call(NotionClient(NOTION_KEY))
        opened.assert_not_called()

    def test_en_tetes_et_hote_fixe(self):
        seen = {}

        def fake(request, timeout):
            seen["url"], seen["auth"] = request.full_url, request.get_header("Authorization")
            seen["version"] = request.get_header("Notion-version")
            return reply({"results": [], "has_more": False})

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            NotionClient(NOTION_KEY).list_children(PAGE)
        self.assertTrue(seen["url"].startswith("https://api.notion.com/v1/blocks/"))
        self.assertEqual((seen["auth"], seen["version"]), (f"Bearer {NOTION_KEY}", "2022-06-28"))

    def test_le_jeton_n_apparait_jamais_dans_les_erreurs(self):
        for failure in (
            http_error(401, "API token is invalid."),
            http_error(500, "boom"),
            urllib.error.URLError("dns"),
        ):
            with mock.patch("urllib.request.urlopen", side_effect=failure):
                with self.assertRaises(NotionError) as ctx:
                    NotionClient(NOTION_KEY).list_children(PAGE)
            self.assertNotIn("NOTION_KEY-VALUE", str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)

    def test_pagination_de_la_lecture(self):
        pages = [
            reply({"results": [{"id": "a"}], "has_more": True, "next_cursor": "c2"}),
            reply({"results": [{"id": "b"}], "has_more": False, "next_cursor": None}),
        ]
        urls = []

        def fake(request, timeout):
            urls.append(request.full_url)
            return pages[len(urls) - 1]

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            blocks = NotionClient(NOTION_KEY).list_children(PAGE)
        self.assertEqual([b["id"] for b in blocks], ["a", "b"])
        self.assertIn("start_cursor=c2", urls[1])

    def test_ajout_par_paquets_de_100(self):
        sizes = []

        def fake(request, timeout):
            sizes.append(len(json.loads(request.data)["children"]))
            return reply({})

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            NotionClient(NOTION_KEY).append_children(PAGE, [{"type": "paragraph"}] * 250)
        self.assertEqual(sizes, [100, 100, 50])

    def test_suppression_utilise_delete(self):
        methods = []

        def fake(request, timeout):
            methods.append(request.get_method())
            return reply({})

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            NotionClient(NOTION_KEY).delete_block(PAGE)
        self.assertEqual(methods, ["DELETE"])

    def test_limite_de_debit_reessayee_une_fois(self):
        limited = urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "0.01"}, io.BytesIO(b"{}"))
        with mock.patch(
            "urllib.request.urlopen", side_effect=[limited, reply({"results": [], "has_more": False})]
        ) as opened:
            NotionClient(NOTION_KEY).list_children(PAGE)
        self.assertEqual(opened.call_count, 2)

    def test_limite_de_debit_trop_longue_abandonne(self):
        limited = urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "600"}, io.BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", side_effect=[limited]) as opened:
            with self.assertRaises(NotionError):
                NotionClient(NOTION_KEY).list_children(PAGE)
        self.assertEqual(opened.call_count, 1)

import io
import json
import urllib.error
from unittest import mock

from django.test import SimpleTestCase

from . import sonarcloud
from .sonarcloud import SonarCloudClient, SonarCloudError


def reply(payload) -> io.BytesIO:
    return io.BytesIO(json.dumps(payload).encode())


class SonarCloudClientTests(SimpleTestCase):
    def test_hote_fixe_et_aucun_jeton_par_defaut(self):
        seen = {}

        def fake(request, timeout):
            seen["url"], seen["auth"] = request.full_url, request.get_header("Authorization")
            return reply({"projectStatus": {"status": "OK"}})

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            self.assertEqual(SonarCloudClient().quality_gate("a_b"), {"status": "OK"})
        self.assertTrue(seen["url"].startswith("https://sonarcloud.io/api/qualitygates/project_status?"))
        self.assertIn("projectKey=a_b", seen["url"])
        self.assertIsNone(seen["auth"])  # projet public : aucun identifiant n'est envoyé

    def test_jeton_optionnel_dans_l_en_tete(self):
        seen = {}

        def fake(request, timeout):
            seen["auth"] = request.get_header("Authorization")
            return reply({})

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            SonarCloudClient("secret-token").get("/api/x")
        self.assertEqual(seen["auth"], "Bearer secret-token")

    def test_pagination_des_problemes(self):
        pages = [{"total": 3, "issues": [{"key": "1"}, {"key": "2"}]}, {"total": 3, "issues": [{"key": "3"}]}]
        with (
            mock.patch.object(sonarcloud, "PAGE_SIZE", 2),
            mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout: reply(pages.pop(0))),
        ):
            self.assertEqual([i["key"] for i in SonarCloudClient().issues("p")], ["1", "2", "3"])

    def test_erreurs_http_et_reseau(self):
        http_error = urllib.error.HTTPError("u", 404, "nf", {}, io.BytesIO(b'{"errors":["corps sensible"]}'))
        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(SonarCloudError) as caught:
                SonarCloudClient().get("/x")
        self.assertEqual(caught.exception.status, 404)
        self.assertNotIn("sensible", str(caught.exception))  # le corps de la réponse n'est jamais repris
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
            with self.assertRaises(SonarCloudError) as caught:
                SonarCloudClient().get("/x")
        self.assertEqual(caught.exception.status, 0)
        with mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout: io.BytesIO(b"pas du json")):
            with self.assertRaises(SonarCloudError):
                SonarCloudClient().get("/x")

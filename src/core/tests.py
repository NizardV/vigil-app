import io
import json
import urllib.error
import urllib.parse
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .github import GitHubClient, GitHubError


def http_error(code, message, headers=None):
    body = io.BytesIO(json.dumps({"message": message}).encode())
    return urllib.error.HTTPError("https://api.github.com/x", code, message, headers or {}, body)


class Response(io.BytesIO):
    """Réponse HTTP factice : corps JSON + en-tête Link optionnel."""

    def __init__(self, body: bytes, link: str | None = None):
        super().__init__(body)
        self.headers = {"Link": link.removeprefix("Link: ")} if link else {}


class HealthTests(TestCase):
    def test_sante(self):
        self.assertEqual(self.client.get(reverse("health")).json(), {"status": "ok"})


class GitHubClientTests(SimpleTestCase):
    def test_erreur_http_est_convertie(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(404, "Not Found")):
            with self.assertRaises(GitHubError) as ctx:
                GitHubClient().get("/x")
        self.assertEqual((ctx.exception.status, ctx.exception.rate_limited), (404, False))

    def test_quota_epuise_detecte(self):
        exc = http_error(403, "API rate limit exceeded", {"X-RateLimit-Remaining": "0"})
        with mock.patch("urllib.request.urlopen", side_effect=exc):
            with self.assertRaises(GitHubError) as ctx:
                GitHubClient().get("/x")
        self.assertTrue(ctx.exception.rate_limited)

    def test_403_de_droits_n_est_pas_un_quota(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(403, "Resource not accessible")):
            with self.assertRaises(GitHubError) as ctx:
                GitHubClient().get("/x")
        self.assertFalse(ctx.exception.rate_limited)

    def test_erreur_reseau_a_le_statut_zero(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
            with self.assertRaises(GitHubError) as ctx:
                GitHubClient().get("/x")
        self.assertEqual(ctx.exception.status, 0)

    def test_jeton_envoye_seulement_a_l_api_github(self):
        captured = {}

        def fake(request, timeout):
            captured["url"], captured["auth"] = request.full_url, request.get_header("Authorization")
            return io.BytesIO(b"[]")

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            GitHubClient("tok").get("/user/repos")
        self.assertTrue(captured["url"].startswith("https://api.github.com/"))
        self.assertEqual(captured["auth"], "Bearer tok")

    def test_pagination_suit_le_lien_suivant_sans_parametre_page(self):
        """Régression : l'API Dependabot refuse `page` (400) et pagine par curseur via l'en-tête Link."""
        urls = []

        def fake(request, timeout):
            urls.append(request.full_url)
            if len(urls) == 1:
                return Response(b'[{"n": 1}]', 'Link: <https://api.github.com/x?after=abc&per_page=100>; rel="next"')
            return Response(b'[{"n": 2}]')

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            items = GitHubClient("tok").paginate("/x", {"state": "open"})
        self.assertEqual(items, [{"n": 1}, {"n": 2}])
        sent = urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)
        self.assertNotIn("page", sent)  # `per_page` est autorisé, `page` non
        self.assertEqual(sent["per_page"], ["100"])
        self.assertIn("after=abc", urls[1])

    def test_lien_suivant_hors_api_github_jamais_suivi(self):
        """Sécurité : un en-tête Link piégé ne doit pas faire partir le jeton vers un autre serveur."""
        calls = []

        def fake(request, timeout):
            calls.append(request.full_url)
            return Response(b'[{"n": 1}]', 'Link: <https://evil.example/steal>; rel="next"')

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            with self.assertRaises(GitHubError):
                GitHubClient("tok").paginate("/x")
        self.assertEqual(len(calls), 1)  # seule la première requête, vers api.github.com

    def test_depots_forks_exclus_et_repli_sans_jeton(self):
        repos = [
            {"full_name": "a/b", "fork": False, "owner": {"login": "a"}},
            {"full_name": "a/c", "fork": True},
        ]
        client = GitHubClient()
        with mock.patch.object(client, "paginate", return_value=repos):
            self.assertEqual([r["full_name"] for r in client.list_owner_repos("a")], ["a/b"])

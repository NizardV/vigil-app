import socket

from django.test import SimpleTestCase

from .netfetch import MAX_REDIRECTS, FetchError, Target, fetch_public, validate_target


def resolver_to(*addresses):
    """Faux résolveur DNS : renvoie toujours ces adresses (même format que socket.getaddrinfo)."""

    def resolve(host, port, type=0):  # noqa: A002 - même signature que getaddrinfo
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in addresses]

    return resolve


PUBLIC = resolver_to("93.184.216.34")


class ValidateTests(SimpleTestCase):
    def test_url_publique_acceptee_et_epinglee(self):
        target = validate_target("https://example.org/feed.xml?x=1", resolver=PUBLIC)
        self.assertEqual(target, Target("https", "example.org", 443, "/feed.xml?x=1", "93.184.216.34"))

    def test_adresses_non_publiques_refusees(self):
        for address in (
            "127.0.0.1", "10.0.0.5", "192.168.1.10", "172.16.0.1", "172.31.255.255",
            "169.254.169.254",  # métadonnées cloud
            "0.0.0.0", "100.64.0.1", "224.0.0.1", "::1", "fe80::1", "fc00::1",  # noqa: S104 - adresses à REFUSER
            "::ffff:127.0.0.1",  # IPv4 de bouclage déguisée en IPv6
            "::ffff:10.0.0.1",
        ):  # fmt: skip
            with self.subTest(address=address), self.assertRaises(FetchError):
                validate_target("http://exemple.test/", resolver=resolver_to(address))

    def test_un_nom_qui_resout_aussi_vers_le_reseau_interne_est_refuse(self):
        """Une seule adresse interne parmi plusieurs suffit : le nom pourrait cibler le réseau interne."""
        with self.assertRaises(FetchError):
            validate_target("http://mixte.test/", resolver=resolver_to("93.184.216.34", "10.0.0.1"))

    def test_litteraux_ip_internes_refuses(self):
        for url in ("http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/"):
            with self.subTest(url=url), self.assertRaises(FetchError):
                validate_target(url, resolver=resolver_to(url.split("/")[2].strip("[]")))

    def test_schemas_refuses(self):
        for url in (
            "file:///etc/passwd",
            "ftp://example.org/x",
            "gopher://example.org/",
            "javascript:alert(1)",
            "//example.org",
        ):
            with self.subTest(url=url), self.assertRaises(FetchError):
                validate_target(url, resolver=PUBLIC)

    def test_identifiants_dans_l_url_refuses(self):
        for url in ("http://user:pass@example.org/", "http://user@example.org/"):
            with self.subTest(url=url), self.assertRaises(FetchError):
                validate_target(url, resolver=PUBLIC)

    def test_ports_de_services_internes_refuses(self):
        for port in (22, 25, 3306, 5432, 5433, 6379, 8000, 8501, 5678, 9200):
            with self.subTest(port=port), self.assertRaises(FetchError):
                validate_target(f"http://example.org:{port}/", resolver=PUBLIC)

    def test_ports_autorises(self):
        for url in (
            "http://example.org/",
            "https://example.org/",
            "http://example.org:8080/",
            "https://example.org:8443/",
        ):
            validate_target(url, resolver=PUBLIC)

    def test_urls_invalides(self):
        for url in ("", "   ", "http://", "http://example.org:99999/", "http://[::1/"):
            with self.subTest(url=url), self.assertRaises(FetchError):
                validate_target(url, resolver=PUBLIC)

    def test_nom_introuvable(self):
        def unknown(host, port, type=0):  # noqa: A002
            raise socket.gaierror("introuvable")

        with self.assertRaises(FetchError):
            validate_target("http://nexiste.pas/", resolver=unknown)


class FetchTests(SimpleTestCase):
    def fetcher(self, *responses):
        """Faux transport : renvoie les réponses dans l'ordre et mémorise les cibles contactées."""
        calls = []

        def request(target, timeout, max_bytes):
            calls.append(target)
            return responses[len(calls) - 1]

        return request, calls

    def test_succes(self):
        request, calls = self.fetcher((200, {}, b"<rss/>"))
        self.assertEqual(fetch_public("https://example.org/f", resolver=PUBLIC, request=request), b"<rss/>")
        self.assertEqual(calls[0].ip, "93.184.216.34")  # la connexion est épinglée sur l'IP validée

    def test_redirection_vers_le_reseau_interne_refusee(self):
        """Le classique : une URL publique qui redirige vers http://169.254.169.254/."""
        request, calls = self.fetcher((302, {"location": "http://interne.test/secret"}, b""))

        def resolver(host, port, type=0):  # noqa: A002
            address = "169.254.169.254" if host == "interne.test" else "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

        with self.assertRaises(FetchError):
            fetch_public("http://example.org/", resolver=resolver, request=request)
        self.assertEqual(len(calls), 1)  # la seconde cible n'a JAMAIS été contactée

    def test_redirection_relative_suivie(self):
        request, calls = self.fetcher((301, {"location": "/nouveau"}, b""), (200, {}, b"ok"))
        self.assertEqual(fetch_public("https://example.org/ancien", resolver=PUBLIC, request=request), b"ok")
        self.assertEqual(calls[1].path, "/nouveau")

    def test_trop_de_redirections(self):
        loop = (302, {"location": "/boucle"}, b"")
        request, _ = self.fetcher(*[loop] * (MAX_REDIRECTS + 2))
        with self.assertRaises(FetchError):
            fetch_public("http://example.org/", resolver=PUBLIC, request=request)

    def test_statut_non_200(self):
        request, _ = self.fetcher((404, {}, b""))
        with self.assertRaises(FetchError):
            fetch_public("http://example.org/", resolver=PUBLIC, request=request)

    def test_contenu_trop_volumineux(self):
        request, _ = self.fetcher((200, {}, b"x" * 101))
        with self.assertRaises(FetchError):
            fetch_public("http://example.org/", resolver=PUBLIC, request=request, max_bytes=100)

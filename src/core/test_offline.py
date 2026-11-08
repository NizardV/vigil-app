import socket
import urllib.error
import urllib.request

from django.test import SimpleTestCase

from .test_runner import NetworkBlocked


class OfflineGuardTests(SimpleTestCase):
    """Garde-fou : si ces tests échouent, la suite pourrait contacter Internet sans que personne ne le voie."""

    def test_connexion_externe_bloquee(self):
        with self.assertRaises(NetworkBlocked):
            socket.create_connection(("93.184.216.34", 80), timeout=1)

    def test_resolution_dns_bloquee(self):
        with self.assertRaises(NetworkBlocked):
            socket.getaddrinfo("discord.com", 443)

    def test_requete_http_bloquee(self):
        with self.assertRaises((NetworkBlocked, urllib.error.URLError)):
            urllib.request.urlopen("https://discord.com/api/webhooks/1/x", timeout=2)  # noqa: S310

    def test_le_bouclage_reste_autorise(self):
        self.assertTrue(socket.getaddrinfo("127.0.0.1", 5433))

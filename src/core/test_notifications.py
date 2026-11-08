import io
import json
import urllib.error
from unittest import mock

from django.test import SimpleTestCase

from .notifications import NotificationError, escape, send_discord
from .tests import http_error

URL = "https://discord.com/api/webhooks/123/SECRET-TOKEN"


class DiscordTests(SimpleTestCase):
    def test_url_non_discord_refusee(self):
        for bad in (
            "http://discord.com/api/webhooks/1/x",  # pas https
            "https://evil.example/api/webhooks/1/x",  # mauvais hôte
            "https://discord.com.evil.example/api/webhooks/1/x",  # hôte déguisé
            "https://discord.com/other/path",  # pas un webhook
            "file:///etc/passwd",
        ):
            with mock.patch("urllib.request.urlopen") as opened:
                with self.assertRaises(NotificationError):
                    send_discord(bad, {"content": "x"})
            opened.assert_not_called()

    def test_aucune_mention_possible(self):
        captured = {}

        def fake(request, timeout):
            captured["body"] = json.loads(request.data)
            return io.BytesIO(b"")

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            send_discord(URL, {"content": "@everyone"})
        self.assertEqual(captured["body"]["allowed_mentions"], {"parse": []})

    def test_l_url_n_apparait_jamais_dans_les_erreurs(self):
        for failure in (http_error(500, "boom"), http_error(404, "gone"), urllib.error.URLError("dns")):
            with mock.patch("urllib.request.urlopen", side_effect=failure):
                with self.assertRaises(NotificationError) as ctx:
                    send_discord(URL, {"content": "x"})
            self.assertNotIn("SECRET-TOKEN", str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)
            self.assertTrue(ctx.exception.__suppress_context__)

    def test_limite_de_debit_reessayee_une_fois(self):
        limited = urllib.error.HTTPError(URL, 429, "rate", {}, io.BytesIO(b'{"retry_after": 0.01}'))
        with mock.patch("urllib.request.urlopen", side_effect=[limited, io.BytesIO(b"")]) as opened:
            send_discord(URL, {"content": "x"})
        self.assertEqual(opened.call_count, 2)

    def test_limite_de_debit_trop_longue_abandonne(self):
        limited = urllib.error.HTTPError(URL, 429, "rate", {}, io.BytesIO(b'{"retry_after": 600}'))
        with mock.patch("urllib.request.urlopen", side_effect=[limited]) as opened:
            with self.assertRaises(NotificationError):
                send_discord(URL, {"content": "x"})
        self.assertEqual(opened.call_count, 1)  # pas d'attente de 10 minutes dans une tâche

    def test_escape_neutralise_markdown_et_mentions(self):
        out = escape("@everyone **gras** [lien](http://x) `code` <@123>")
        self.assertNotIn("@everyone", out)
        self.assertNotIn("**", out)
        self.assertNotIn("](", out)
        self.assertEqual(len(escape("a" * 500, 50)), 50)


class PingTests(SimpleTestCase):
    PING_URL = "https://hc-ping.example/uuid-SECRET-ID"

    def test_http_refuse(self):
        from .notifications import ping

        with mock.patch("urllib.request.urlopen") as opened:
            with self.assertRaises(NotificationError):
                ping("http://hc-ping.example/uuid")
        opened.assert_not_called()

    def test_l_url_n_apparait_jamais_dans_les_erreurs(self):
        from .notifications import ping

        for failure in (http_error(500, "boom"), urllib.error.URLError("dns")):
            with mock.patch("urllib.request.urlopen", side_effect=failure):
                with self.assertRaises(NotificationError) as ctx:
                    ping(self.PING_URL)
            self.assertNotIn("SECRET-ID", str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)

    def test_succes(self):
        from .notifications import ping

        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(b"OK")) as opened:
            ping(self.PING_URL)
        opened.assert_called_once()

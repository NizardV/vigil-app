import io
import json
import time
import urllib.error
from unittest import mock

from django.test import SimpleTestCase
from nacl.signing import SigningKey

from .discord_interactions import send_bot_message, verify_signature
from .notifications import NotificationError
from .tests import http_error

KEY = SigningKey.generate()  # vraie paire Ed25519, comme Discord
PUBLIC = KEY.verify_key.encode().hex()
BODY = b'{"type":3,"data":{"custom_id":"x"}}'
BOT_KEY = "MTIz.BOT-TOKEN-SECRET.value"


def signed(body=BODY, timestamp=None, key=KEY):
    ts = str(int(timestamp if timestamp is not None else time.time()))
    return ts, key.sign(ts.encode() + body).signature.hex()


class SignatureTests(SimpleTestCase):
    def test_signature_valide(self):
        ts, sig = signed()
        self.assertTrue(verify_signature(PUBLIC, ts, BODY, sig))

    def test_corps_modifie_refuse(self):
        ts, sig = signed()
        self.assertFalse(verify_signature(PUBLIC, ts, BODY + b" ", sig))
        self.assertFalse(verify_signature(PUBLIC, ts, b'{"type":3,"data":{"custom_id":"autre"}}', sig))

    def test_mauvaise_cle_refusee(self):
        ts, sig = signed(key=SigningKey.generate())
        self.assertFalse(verify_signature(PUBLIC, ts, BODY, sig))

    def test_horodatage_modifie_refuse(self):
        ts, sig = signed()
        self.assertFalse(verify_signature(PUBLIC, str(int(ts) + 1), BODY, sig))

    def test_requete_rejouee_refusee(self):
        """Une signature valide mais vieille de plus de 5 minutes est un rejeu."""
        ts, sig = signed(timestamp=time.time() - 3600)
        self.assertFalse(verify_signature(PUBLIC, ts, BODY, sig))
        ts, sig = signed(timestamp=time.time() + 3600)
        self.assertFalse(verify_signature(PUBLIC, ts, BODY, sig))

    def test_entrees_malformees_refusees_sans_exception(self):
        ts, sig = signed()
        for args in (
            ("", ts, BODY, sig),  # clé publique non configurée : on n'accepte RIEN
            (PUBLIC, "", BODY, sig),
            (PUBLIC, ts, BODY, ""),
            (PUBLIC, ts, BODY, "pas-de-l-hexa"),
            (PUBLIC, "abc", BODY, sig),
            ("zz", ts, BODY, sig),
            (PUBLIC, ts, BODY, sig[:-2]),
        ):
            self.assertFalse(verify_signature(*args), args)


class BotMessageTests(SimpleTestCase):
    def test_envoi_avec_jeton_en_en_tete_et_aucune_mention(self):
        seen = {}

        def fake(request, timeout):
            seen["url"], seen["auth"], seen["body"] = (
                request.full_url,
                request.get_header("Authorization"),
                json.loads(request.data),
            )
            return io.BytesIO(b"{}")

        with mock.patch("urllib.request.urlopen", side_effect=fake):
            send_bot_message("123456789012345678", {"content": "@everyone"}, BOT_KEY)
        self.assertEqual(seen["url"], "https://discord.com/api/v10/channels/123456789012345678/messages")
        self.assertEqual(seen["auth"], f"Bot {BOT_KEY}")
        self.assertEqual(seen["body"]["allowed_mentions"], {"parse": []})

    def test_identifiant_de_salon_invalide_refuse_sans_appel(self):
        for bad in ("", "abc", "../../users/@me", "123/../x", "1" * 40):
            with mock.patch("urllib.request.urlopen") as opened, self.assertRaises(NotificationError):
                send_bot_message(bad, {}, BOT_KEY)
            opened.assert_not_called()

    def test_le_jeton_n_apparait_jamais_dans_les_erreurs(self):
        for failure in (http_error(401, "no"), http_error(500, "boom"), urllib.error.URLError("dns")):
            with mock.patch("urllib.request.urlopen", side_effect=failure):
                with self.assertRaises(NotificationError) as ctx:
                    send_bot_message("123456789012345678", {}, BOT_KEY)
            self.assertNotIn("BOT-TOKEN-SECRET", str(ctx.exception))
            self.assertIsNone(ctx.exception.__cause__)

    def test_sans_jeton_refuse(self):
        with self.assertRaises(NotificationError):
            send_bot_message("123456789012345678", {}, "")

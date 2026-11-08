"""Vérification des requêtes d'interaction Discord (boutons) et envoi de messages par le bot.

Discord signe chaque interaction (Ed25519). Sans vérification, n'importe qui pourrait forger un clic.
On refuse donc toute requête dont la signature est absente, invalide ou trop ancienne (rejeu), et on
n'accepte rien du tout si la clé publique n'est pas configurée.
"""

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .notifications import NotificationError

API = "https://discord.com/api/v10"
TOLERANCE_S = 300  # une signature plus ancienne est rejouée : refusée
_SNOWFLAKE = re.compile(r"^\d{5,25}$")


def verify_signature(
    public_key_hex: str, timestamp: str, body: bytes, signature_hex: str, *, now: float | None = None
) -> bool:
    """Vrai uniquement si la signature Ed25519 de `timestamp + corps` est valide et récente."""
    if not (public_key_hex and timestamp and signature_hex):
        return False
    try:
        if abs((now if now is not None else time.time()) - int(timestamp)) > TOLERANCE_S:
            return False
        VerifyKey(bytes.fromhex(public_key_hex)).verify(timestamp.encode() + body, bytes.fromhex(signature_hex))
    except (ValueError, BadSignatureError):
        return False
    return True


def send_bot_message(channel_id: str, payload: dict[str, Any], bot_token: str, timeout: int = 10) -> None:
    """Poste un message (avec boutons possibles) dans un salon, avec l'identité du bot."""
    if not _SNOWFLAKE.match(channel_id or ""):
        raise NotificationError("identifiant de salon Discord invalide")
    if not bot_token:
        raise NotificationError("jeton de bot Discord absent")
    body = json.dumps({**payload, "allowed_mentions": {"parse": []}}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bot {bot_token}",
        "User-Agent": "DiscordBot (vigil, 1.0)",
    }
    request = urllib.request.Request(  # noqa: S310 - hôte fixe (https)
        f"{API}/channels/{channel_id}/messages", data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310
            return
    except urllib.error.HTTPError as exc:
        raise NotificationError(f"Discord a répondu {exc.code}") from None  # jamais le jeton
    except (urllib.error.URLError, TimeoutError, OSError):
        raise NotificationError("Discord injoignable") from None

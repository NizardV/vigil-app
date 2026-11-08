"""Envoi de notifications Discord par webhook. Aucune dépendance vers les modules métier.

L'URL d'un webhook est un secret (quiconque la connaît peut poster dans le salon) : elle n'apparaît jamais
dans les messages d'erreur ni dans les logs, et seuls les domaines Discord sont acceptés.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ALLOWED_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
MAX_RETRY_WAIT = 5.0  # secondes ; au-delà d'un 429 on abandonne plutôt que de bloquer une tâche
_MARKDOWN = re.compile(r"([\\`*_~|>#\[\]()])")


class NotificationError(Exception):
    """Échec d'envoi. Le message ne contient jamais l'URL du webhook."""


def webhook_url(channel: str) -> str:
    """URL du webhook du salon `channel` (ex. "security-alerts"), ou "" s'il n'est pas configuré.

    Un webhook par salon : si l'un fuite, on ne révoque que lui ; chaque module écrit dans « son » salon.
    Configuration : variable d'environnement DISCORD_WEBHOOK_<NOM_EN_MAJUSCULES> (voir config/settings.py).
    """
    from django.conf import settings

    return settings.DISCORD_WEBHOOKS.get(channel, "")


def ping(url: str, timeout: int = 10) -> None:
    """Signal de vie : un simple GET vers un service externe de surveillance (https obligatoire).

    L'URL contient en général un identifiant secret : elle n'apparaît jamais dans une erreur.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise NotificationError("URL de signal de vie invalide (https obligatoire)")
    request = urllib.request.Request(url, headers={"User-Agent": "vigil-heartbeat"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310 - schéma https vérifié ci-dessus
            return
    except urllib.error.HTTPError as exc:
        raise NotificationError(f"Le service de signal de vie a répondu {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise NotificationError("Service de signal de vie injoignable") from None


def escape(text: str, limit: int | None = None) -> str:
    """Neutralise le markdown et les mentions d'un texte venant de l'extérieur (titre de PR, advisory…)."""
    cleaned = _MARKDOWN.sub(r"\\\1", " ".join(str(text).split()))
    cleaned = cleaned.replace("@", "@​")  # espace de largeur nulle : casse @everyone / @here / <@id>
    if limit and len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip("\\") + "…"
    return cleaned


def _validate(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or not parsed.path.startswith("/api/webhooks/"):
        raise NotificationError("URL de webhook Discord invalide")


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    try:
        return float(json.loads(exc.read().decode("utf-8", errors="replace")).get("retry_after"))
    except (ValueError, TypeError):
        return None


def send_discord(webhook_url: str, payload: dict[str, Any], timeout: int = 10) -> None:
    """Poste `payload` (content / embeds) sur le webhook. Lève NotificationError en cas d'échec."""
    _validate(webhook_url)
    # Aucune mention possible, quel que soit le contenu.
    body = json.dumps({**payload, "allowed_mentions": {"parse": []}}).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "DiscordBot (vigil, 1.0)"}
    for attempt in (1, 2):
        request = urllib.request.Request(webhook_url, data=body, headers=headers, method="POST")  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310 - https + hôte Discord validés
                return
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt == 1:
                wait = _retry_after(exc)
                if wait is not None and wait <= MAX_RETRY_WAIT:
                    time.sleep(wait)
                    continue
                raise NotificationError("Discord: limite de débit atteinte") from None
            raise NotificationError(f"Discord a répondu {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise NotificationError("Discord injoignable") from None

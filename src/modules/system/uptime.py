"""Disponibilité des sites hébergés à côté de Vigil (Cyna, portfolio…) : alerte #système en cas de panne.

Complète le signal de vie externe (`VIGIL_HEARTBEAT_URL`) : celui-ci voit le VPS ou Vigil s'arrêter ; ce contrôle voit
une **application** en panne sur un VPS qui tourne (ex. base de données arrêtée, du 2026-11-08 au 2026-11-10).
Une alerte après `FAILURES_BEFORE_ALERT` échecs d'affilée (un raté isolé n'alerte pas), puis une au retour.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from django.conf import settings
from django.utils import timezone

from core import netfetch, notifications

from .models import SiteStatus

CHANNEL = "system"
FAILURES_BEFORE_ALERT = 2
TIMEOUT = 10.0
RED, GREEN = 0xB91C1C, 0x16A34A

Fetcher = Callable[..., bytes]
Sender = Callable[[str, dict[str, Any]], None]


def configured_sites() -> dict[str, str]:
    return dict(settings.VIGIL_UPTIME_SITES)


def _probe(url: str, fetch: Fetcher) -> str:
    """Chaîne vide si le site répond 200 (après redirections), sinon la raison."""
    try:
        fetch(url, timeout=TIMEOUT, max_bytes=500_000)
    except netfetch.FetchError as exc:
        return str(exc)[:300]
    except (OSError, ValueError) as exc:  # réseau, TLS, DNS
        return f"{type(exc).__name__}: {exc}"[:300]
    return ""


def _duration(since: datetime, now: datetime) -> str:
    minutes = int((now - since).total_seconds() // 60)
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h {minutes % 60:02d}"


def _alert(send: Sender, title: str, description: str, color: int) -> bool:
    url = notifications.webhook_url(CHANNEL)
    if not url:
        return False
    payload = {"embeds": [{"title": title, "description": description, "color": color, "footer": {"text": "Vigil"}}]}
    try:
        send(url, payload)
    except notifications.NotificationError:
        return False
    return True


def check_sites(*, fetch: Fetcher | None = None, send: Sender | None = None, now: datetime | None = None) -> dict:
    """Vérifie chaque site configuré et alerte aux changements d'état. Une alerte non envoyée n'est pas marquée :
    elle sera retentée au passage suivant."""
    fetch = fetch or netfetch.fetch_public  # résolus à l'appel (remplaçables par mock.patch)
    send = send or notifications.send_discord
    now = now or timezone.now()
    sites = configured_sites()
    SiteStatus.objects.exclude(name__in=sites).delete()  # site retiré de la configuration
    result = {"checked": 0, "down": [], "alerted": []}
    for name, url in sites.items():
        status, _ = SiteStatus.objects.get_or_create(name=name, defaults={"url": url, "changed_at": now})
        status.url, status.last_checked_at = url, now
        error = _probe(url, fetch)
        result["checked"] += 1
        if error:
            result["down"].append(name)
            status.consecutive_failures += 1
            status.last_error = error
            if status.is_up and status.consecutive_failures >= FAILURES_BEFORE_ALERT:
                if _alert(
                    send,
                    f"🔴 {name} ne répond plus",
                    f"{url}\n{notifications.escape(error)}\n({status.consecutive_failures} échecs d'affilée)",
                    RED,
                ):
                    status.is_up, status.changed_at = False, now
                    result["alerted"].append(name)
        else:
            if not status.is_up:
                if _alert(
                    send,
                    f"🟢 {name} répond de nouveau",
                    f"{url}\nPanne de {_duration(status.changed_at, now)}.",
                    GREEN,
                ):
                    status.is_up, status.changed_at = True, now
                    result["alerted"].append(name)
                else:
                    status.save()
                    continue  # retour non signalé : on garde « hors ligne » pour le signaler au prochain passage
            status.consecutive_failures, status.last_error = 0, ""
        status.save()
    return result

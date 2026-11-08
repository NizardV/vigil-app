"""Récupération du rapport d'audit du VPS par SSH, avec des garde-fous stricts.

- La clé SSH est DÉDIÉE et restreinte côté serveur à l'exécution du seul script d'audit (commande
  forcée) : Vigil
  n'envoie aucune commande, il se contente de se connecter. Voir infra/audit/README.md.
- L'empreinte du serveur est vérifiée contre un fichier known_hosts fourni (aucune confiance aveugle).
- Aucun shell : la commande est une liste d'arguments, et l'hôte, l'utilisateur et le port sont validés.
"""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from django.conf import settings

TIMEOUT_S = 120
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.:-]{0,251}[A-Za-z0-9])?$")
_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class VpsError(Exception):
    """Échec de la récupération du rapport. Le message ne contient ni clé ni contenu de rapport."""


def configured() -> bool:
    return bool(settings.VPS_AUDIT_HOST and settings.VPS_AUDIT_SSH_KEY and settings.VPS_AUDIT_KNOWN_HOSTS)


def build_command() -> list[str]:
    host, user, port = settings.VPS_AUDIT_HOST, settings.VPS_AUDIT_USER, settings.VPS_AUDIT_PORT
    if not _HOST.match(host) or host.startswith("-"):
        raise VpsError("hôte d'audit invalide")
    if not _USER.match(user):
        raise VpsError("utilisateur d'audit invalide")
    if not 1 <= port <= 65535:
        raise VpsError("port d'audit invalide")
    key, known_hosts = Path(settings.VPS_AUDIT_SSH_KEY), Path(settings.VPS_AUDIT_KNOWN_HOSTS)
    if not key.is_file():
        raise VpsError("clé SSH d'audit introuvable")
    if not known_hosts.is_file():
        # Refus net : sans empreinte connue, on accepterait n'importe quel serveur se faisant passer pour lui.
        raise VpsError("fichier known_hosts introuvable : l'empreinte du serveur ne peut pas être vérifiée")
    return [
        "ssh",
        "-T",
        "-i", str(key),
        "-p", str(port),
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=3",
        "-o", "LogLevel=ERROR",
        f"{user}@{host}",
    ]  # fmt: skip


def fetch_report(*, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    command = build_command()
    try:
        result = run(  # noqa: S603 - arguments validés, aucun shell
            command, capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL, check=False
        )
    except subprocess.TimeoutExpired:
        raise VpsError(f"délai dépassé ({TIMEOUT_S} s)") from None
    except FileNotFoundError:
        raise VpsError("client ssh introuvable sur cette machine") from None
    if result.returncode != 0:
        reason = (result.stderr or "").strip().splitlines()[-1:] or ["aucun détail"]
        raise VpsError(f"ssh a échoué (code {result.returncode}) : {reason[0][:200]}")
    return result.stdout

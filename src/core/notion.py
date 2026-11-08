"""Client Notion minimal et partagé (blocs d'une page). Aucune dépendance vers les modules métier.

Le jeton d'intégration est un secret : il n'apparaît jamais dans un message d'erreur, et on n'appelle
jamais autre chose que l'API Notion.
"""

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
PAGE_SIZE = 100  # maximum de l'API, pour la lecture comme pour l'ajout de blocs
MAX_RETRY_WAIT = 5.0
_ID = re.compile(r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$")


class NotionError(Exception):
    """Échec d'un appel Notion (`status` = 0 pour une erreur réseau). Ne contient jamais le jeton."""

    def __init__(self, status: int, message: str):
        super().__init__(f"Notion {status}: {message}")
        self.status = status
        self.message = message


def valid_id(value: str) -> bool:
    return bool(_ID.match(value or ""))


class NotionClient:
    def __init__(self, token: str, timeout: int = 20):
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
            "User-Agent": "vigil",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in (1, 2):
            request = urllib.request.Request(f"{API}{path}", data=data, headers=headers, method=method)  # noqa: S310
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - hôte fixe (https)
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 1:
                    wait = self._retry_after(exc)
                    if wait is not None and wait <= MAX_RETRY_WAIT:
                        time.sleep(wait)
                        continue
                raise self._to_error(exc) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise NotionError(0, "Notion injoignable") from None
        raise NotionError(429, "limite de débit atteinte")  # pragma: no cover - sortie de sécurité

    @staticmethod
    def _retry_after(exc: urllib.error.HTTPError) -> float | None:
        try:
            return float(exc.headers.get("Retry-After"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_error(exc: urllib.error.HTTPError) -> NotionError:
        try:
            message = str(json.loads(exc.read().decode("utf-8", errors="replace")).get("message", ""))
        except ValueError:
            message = ""
        return NotionError(exc.code, (message or exc.reason or "erreur")[:300])

    @staticmethod
    def _check_id(value: str) -> str:
        if not valid_id(value):
            raise NotionError(0, "identifiant Notion invalide")  # jamais d'identifiant libre dans une URL
        return value

    def list_children(self, block_id: str) -> list[dict]:
        """Tous les blocs enfants (de premier niveau) d'une page ou d'un bloc."""
        block_id, blocks, cursor = self._check_id(block_id), [], None
        while True:
            query = f"?page_size={PAGE_SIZE}" + (f"&start_cursor={cursor}" if cursor else "")
            data = self._request("GET", f"/blocks/{block_id}/children{query}")
            blocks += data.get("results", [])
            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                return blocks

    def append_children(self, block_id: str, children: list[dict]) -> None:
        """Ajoute des blocs à la fin, par paquets de 100 (limite de l'API)."""
        block_id = self._check_id(block_id)
        for start in range(0, len(children), PAGE_SIZE):
            self._request("PATCH", f"/blocks/{block_id}/children", {"children": children[start : start + PAGE_SIZE]})

    def delete_block(self, block_id: str) -> None:
        """Archive un bloc (récupérable depuis la corbeille de Notion)."""
        self._request("DELETE", f"/blocks/{self._check_id(block_id)}")

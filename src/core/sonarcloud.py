"""Client SonarCloud minimal et partagé (lecture seule). Aucune dépendance vers les modules métier.

Un projet public se lit sans jeton ; un jeton optionnel permet de lire un projet privé. L'hôte est fixe (https) : aucune
URL venant de la configuration n'est suivie, et aucun corps de réponse d'erreur n'est repris dans les messages.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://sonarcloud.io"
PAGE_SIZE = 500
MAX_PAGES = 4  # 2 000 problèmes au plus : au-delà, l'audit n'a plus de sens, on signale simplement la troncature


class SonarCloudError(Exception):
    """Échec d'un appel (`status` = 0 pour une erreur réseau ou une réponse illisible)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"SonarCloud {status}: {message}")
        self.status = status
        self.message = message


class SonarCloudClient:
    def __init__(self, token: str = "", timeout: int = 20):
        self.token, self.timeout = token, timeout

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        headers = {"Accept": "application/json", "User-Agent": "vigil"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)  # noqa: S310 - hôte fixe (https)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise SonarCloudError(exc.code, f"HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise SonarCloudError(0, "injoignable") from None
        except ValueError:
            raise SonarCloudError(0, "réponse illisible") from None

    def quality_gate(self, project: str) -> dict[str, Any]:
        return self.get("/api/qualitygates/project_status", {"projectKey": project}).get("projectStatus", {})

    def issues(self, project: str) -> list[dict[str, Any]]:
        """Problèmes ouverts du projet (tous niveaux), sur les pages disponibles."""
        found: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            data = self.get(
                "/api/issues/search",
                {"componentKeys": project, "issueStatuses": "OPEN,CONFIRMED", "ps": PAGE_SIZE, "p": page},
            )
            found += data.get("issues", [])
            if page * PAGE_SIZE >= int(data.get("total", 0)):
                break
        return found

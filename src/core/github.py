"""Client GitHub REST minimal et partagé (lecture seule). Aucune dépendance vers les modules métier."""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.github.com"
PAGE_SIZE = 100


_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


def _next_link(header: str | None) -> str | None:
    match = _NEXT.search(header or "")
    return match.group(1) if match else None


class GitHubError(Exception):
    """Réponse d'erreur de GitHub (`status` = 0 pour une erreur réseau)."""

    def __init__(self, status: int, message: str, *, rate_limited: bool = False):
        super().__init__(f"GitHub {status}: {message}")
        self.status = status
        self.message = message
        self.rate_limited = rate_limited


class GitHubClient:
    def __init__(self, token: str = "", timeout: int = 20):
        self.token = token
        self.timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._fetch(url)[0]

    def _fetch(self, url: str) -> tuple[Any, str | None]:
        """Renvoie (corps JSON, URL de la page suivante). N'appelle jamais autre chose que l'API GitHub."""
        if not url.startswith(f"{API}/"):
            raise GitHubError(0, "URL hors de l'API GitHub refusée")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "vigil"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)  # noqa: S310 - schéma https vérifié ci-dessus
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                link = response.headers.get("Link") if hasattr(response, "headers") else None
                return json.load(response), _next_link(link)
        except urllib.error.HTTPError as exc:
            raise self._to_error(exc) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubError(0, str(exc)) from exc

    @staticmethod
    def _to_error(exc: urllib.error.HTTPError) -> GitHubError:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(raw).get("message", raw)
        except ValueError:
            message = raw
        message = str(message)[:300]
        remaining = exc.headers.get("X-RateLimit-Remaining")
        limited = exc.code in (403, 429) and (remaining == "0" or "rate limit" in message.lower())
        return GitHubError(exc.code, message, rate_limited=limited)

    def paginate(self, path: str, params: dict[str, Any] | None = None, max_pages: int = 20) -> list[Any]:
        """Suit l'en-tête `Link: rel="next"` (pages *et* curseurs : l'API Dependabot n'accepte pas `page`)."""
        items: list[Any] = []
        url: str | None = f"{API}{path}?" + urllib.parse.urlencode({**(params or {}), "per_page": PAGE_SIZE})
        for _ in range(max_pages):
            if url is None:
                break
            batch, url = self._fetch(url)
            if not isinstance(batch, list):
                break
            items += batch
        return items

    def list_owner_repos(self, owner: str) -> list[dict]:
        """Dépôts non-fork d'un compte. Avec un jeton : inclut les privés du propriétaire."""
        repos: list[dict] = []
        if self.token:
            mine = self.paginate("/user/repos", {"affiliation": "owner"})
            repos = [r for r in mine if r["owner"]["login"].lower() == owner.lower()]
        if not repos:  # pas de jeton, ou jeton d'un autre compte
            repos = self.paginate(f"/users/{owner}/repos")
        return [r for r in repos if not r["fork"]]

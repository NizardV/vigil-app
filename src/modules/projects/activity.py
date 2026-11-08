"""Activité réelle des projets : commits et PR fusionnées de la période, bruit écarté.

Pourquoi par interrogation plutôt que par webhook : l'ancienne version transformait chaque push en
message, sur n'importe quelle branche, d'où un « Deploy to GitHub pages » quotidien sur la branche
générée du dépôt de profil. Ici on ne lit que la **branche par défaut** (les branches générées `output`
et `gh-pages` disparaissent d'office), on écarte bots, déploiements et fusions, et il n'y a plus de point
d'entrée public à protéger.

Un dépôt qu'on n'a pas pu lire est listé comme « non vérifié », jamais présenté comme « sans activité ».
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from django.conf import settings

from core.github import GitHubClient, GitHubError

SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")  # ne jamais injecter autre chose dans une URL
DEPENDABOT = "dependabot[bot]"
MAX_COMMITS_PER_REPO = 100  # une page suffit pour un résumé ; au-delà, on affiche le compte exact de la page


@dataclass
class RepoActivity:
    repo: str
    commits: list[str] = field(default_factory=list)  # première ligne des messages, du plus récent au plus ancien
    merged_prs: list[str] = field(default_factory=list)  # titres des PR fusionnées (hors Dependabot)
    dependency_updates: int = 0  # PR Dependabot fusionnées

    @property
    def is_active(self) -> bool:
        return bool(self.commits or self.merged_prs or self.dependency_updates)


@dataclass
class ActivityReport:
    entries: list[RepoActivity] = field(default_factory=list)
    inactive: int = 0
    unchecked: list[str] = field(default_factory=list)  # dépôts illisibles (droits, erreur) : on ne sait pas


def is_noise(commit: dict) -> bool:
    """Un commit qui n'est pas de l'activité humaine : bot, fusion, ou message de déploiement automatique."""
    author = commit.get("author") or {}
    if author.get("type") == "Bot" or str(author.get("login", "")).endswith("[bot]"):
        return True
    if len(commit.get("parents") or []) > 1:  # commit de fusion : la PR correspondante est déjà comptée
        return True
    message = ((commit.get("commit") or {}).get("message") or "").lower()
    return any(pattern.lower() in message for pattern in settings.PROJECTS_IGNORED_COMMIT_PATTERNS)


def _first_line(commit: dict) -> str:
    message = (commit.get("commit") or {}).get("message") or ""
    return message.splitlines()[0].strip() if message else ""


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def repo_activity(client: GitHubClient, repo: str, since: datetime) -> RepoActivity:
    """Activité d'un dépôt depuis `since`. Lève GitHubError si le dépôt n'a pas pu être lu."""
    # Sans paramètre `sha`, GitHub renvoie les commits de la branche par défaut uniquement.
    commits = client.get(f"/repos/{repo}/commits", {"since": _iso(since), "per_page": MAX_COMMITS_PER_REPO})
    pulls = client.get(
        f"/repos/{repo}/pulls",
        {"state": "closed", "sort": "updated", "direction": "desc", "per_page": 30},
    )
    activity = RepoActivity(repo)
    activity.commits = [_first_line(c) for c in commits if not is_noise(c)]
    for pr in pulls:
        merged_at = _parse(pr.get("merged_at"))
        if merged_at is None or merged_at < since:
            continue
        if (pr.get("user") or {}).get("login") == DEPENDABOT:
            activity.dependency_updates += 1
        else:
            activity.merged_prs.append(pr.get("title", ""))
    return activity


def collect_activity(client: GitHubClient, repos: list[str], since: datetime) -> ActivityReport:
    report = ActivityReport()
    for repo in repos:
        if not SAFE_REPO.match(repo):
            continue
        try:
            activity = repo_activity(client, repo, since)
        except GitHubError as exc:
            if exc.status == 409 and "empty" in exc.message.lower():
                report.inactive += 1  # dépôt vide : on sait avec certitude qu'il n'y a aucune activité
                continue
            report.unchecked.append(repo)
            if exc.rate_limited:  # quota épuisé : inutile d'insister, le reste serait non vérifié aussi
                report.unchecked += [r for r in repos[repos.index(repo) + 1 :] if SAFE_REPO.match(r)]
                break
            continue
        if activity.is_active:
            report.entries.append(activity)
        else:
            report.inactive += 1
    report.entries.sort(key=lambda a: (-(len(a.commits) + len(a.merged_prs)), a.repo))
    return report

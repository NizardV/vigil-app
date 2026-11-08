"""Logique métier du module projects (seule porte d'entrée pour les autres modules)."""

import json
import urllib.request
from datetime import datetime, timedelta, timezone

from django.conf import settings

from .models import TrackedRepo

API = "https://api.github.com"
INACTIVITY_DAYS = 90


def fetch_repos(owner: str, token: str = "") -> list[dict]:
    """Récupère les repos non-fork d'un compte (pagination incluse)."""
    repos: list[dict] = []
    page = 1
    while True:
        url = f"{API}/users/{owner}/repos?per_page=100&page={page}"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=20) as resp:
            batch = json.load(resp)
        if not batch:
            return repos
        repos += [r for r in batch if not r["fork"]]
        page += 1


def _parse_date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def sync_repos(raw_repos: list[dict]) -> int:
    """Insère ou met à jour les repos ; renvoie le nombre de repos traités."""
    for r in raw_repos:
        TrackedRepo.objects.update_or_create(
            full_name=r["full_name"],
            defaults={
                "description": r.get("description") or "",
                "language": r.get("language") or "",
                "is_private": r["private"],
                "is_archived": r["archived"],
                "open_issues": r["open_issues_count"],
                "pushed_at": _parse_date(r.get("pushed_at")),
            },
        )
    return len(raw_repos)


def compute_verdict(repo: TrackedRepo) -> str:
    if repo.is_archived:
        return TrackedRepo.Verdict.A_ARCHIVER
    if repo.pushed_at is None:
        return TrackedRepo.Verdict.A_RELANCER
    age = datetime.now(timezone.utc) - repo.pushed_at
    return (
        TrackedRepo.Verdict.A_RELANCER
        if age > timedelta(days=INACTIVITY_DAYS)
        else TrackedRepo.Verdict.ACTIF
    )


def run_sync() -> int:
    return sync_repos(fetch_repos(settings.GITHUB_OWNER, settings.GITHUB_TOKEN))

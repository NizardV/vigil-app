"""Suivi des PR en direct dans Discord (#projets-pr) : un message par PR, modifié en place à chaque changement.

Toutes les minutes, UNE recherche GitHub liste les PR du compte mises à jour récemment (tous dépôts confondus). Pour
chacune encore active : ses vérifications (checks + statuts, ex. Vercel) puis, une fois fusionnée, celles du commit
de fusion (workflows de déploiement, statut Vercel). Le message n'est modifié que si son contenu change ; le suivi
s'arrête quand la PR est fusionnée/fermée et que plus rien ne tourne.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from core import notifications
from core.github import GitHubClient, GitHubError

from .models import PullRequestMessage

CHANNEL = "projects-pr"
WINDOW = timedelta(hours=24)  # PR mises à jour dans cette fenêtre
MAX_PRS = 20
GREEN, PURPLE, GREY, RED = 0x16A34A, 0x8957E5, 0x6B7280, 0xDC2626
DEPLOY_GRACE = timedelta(minutes=15)  # PR fusionnée sans déploiement : on arrête de suivre après ce délai
RUNNING = {"queued", "in_progress", "waiting", "requested", "pending"}

Poster = Callable[[str, dict[str, Any]], str]
Editor = Callable[[str, str, dict[str, Any]], None]


def _icon(status: str, conclusion: str | None) -> str:
    if status in RUNNING:
        return "⏳"
    return {"success": "✅", "neutral": "➖", "skipped": "⏭️", "cancelled": "🚫"}.get(conclusion or "", "❌")


def _checks(client: GitHubClient, repo: str, sha: str) -> list[tuple[str, str, str | None]]:
    """(nom, statut, conclusion) des check-runs et des statuts de commit (Vercel…) d'un commit."""
    runs = client.get(f"/repos/{repo}/commits/{sha}/check-runs", {"per_page": 50}).get("check_runs", [])
    items = [(r["name"], r["status"], r.get("conclusion")) for r in runs]
    for s in client.get(f"/repos/{repo}/commits/{sha}/status").get("statuses", []):
        state = s.get("state", "")
        conclusion = {"success": "success", "failure": "failure", "error": "failure"}.get(state)
        items.append((s.get("context", "statut"), "pending" if state == "pending" else "completed", conclusion))
    return items


def render(pr: dict[str, Any], checks: list, deploys: list, now: datetime) -> tuple[dict[str, Any], bool]:
    """Message Discord de la PR et indicateur « suivi terminé »."""
    repo = pr["base"]["repo"]["full_name"]
    if pr.get("merged_at"):
        state, color = "🟣 Fusionnée", PURPLE
    elif pr["state"] == "closed":
        state, color = "⚫ Fermée", GREY
    else:
        state, color = "🟢 Ouverte", GREEN
    lines = [f"**{state}** · `{notifications.escape(pr['head']['ref'], 80)}` → `{pr['base']['ref']}`"]
    if checks:
        lines.append("**Vérifications** : " + " · ".join(f"{_icon(s, c)} {notifications.escape(n, 60)}"
                                                       for n, s, c in sorted(checks)))  # fmt: skip
        if any(c not in {None, "success", "neutral", "skipped"} and s not in RUNNING for _, s, c in checks):
            color = RED if not pr.get("merged_at") else color
    if deploys:
        lines.append("**Après fusion** : " + " · ".join(f"{_icon(s, c)} {notifications.escape(n, 60)}"
                                                       for n, s, c in sorted(deploys)))  # fmt: skip
    author = pr["user"]["login"]
    title = f"{'🤖 ' if author.endswith('[bot]') else ''}{repo.split('/')[-1]} #{pr['number']} — {pr['title']}"
    message = {
        "embeds": [
            {
                "title": notifications.escape(title, 250),
                "url": pr["html_url"],
                "description": "\n".join(lines)[:3500],
                "color": color,
                "footer": {"text": f"Vigil Projects · {author}"},
            }
        ]
    }
    running = any(s in RUNNING for _, s, _ in checks + deploys)
    merged_at = pr.get("merged_at")
    settled = not merged_at or bool(deploys) or now - _parse(merged_at) > DEPLOY_GRACE  # rien ne s'est lancé
    finished = pr["state"] == "closed" and not running and settled
    return message, finished


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fingerprint(message: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()


def track_pull_requests(
    *,
    client: GitHubClient | None = None,
    post: Poster | None = None,
    edit: Editor | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    url = notifications.webhook_url(CHANNEL)
    if not url:
        return {"status": "not_configured"}
    client = client or GitHubClient(settings.GITHUB_TOKEN)
    post = post or notifications.post_discord  # résolus à l'appel : remplaçables par mock.patch
    edit = edit or notifications.edit_discord
    now = now or timezone.now()
    since = (now - WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = f"is:pr user:{settings.GITHUB_OWNER} updated:>={since}"
    try:
        found = client.get("/search/issues", {"q": query, "sort": "updated", "per_page": MAX_PRS}).get("items", [])
    except GitHubError as exc:
        return {"status": "error", "error": str(exc)}

    result = {"status": "ok", "seen": 0, "posted": 0, "edited": 0}
    for item in found:
        repo = item["repository_url"].split("/repos/", 1)[1]
        known = PullRequestMessage.objects.filter(repo=repo, number=item["number"]).first()
        if known and known.finished:
            continue
        try:
            pr = client.get(f"/repos/{repo}/pulls/{item['number']}")
            checks = _checks(client, repo, pr["head"]["sha"])
            # Après fusion : vérifications du commit de fusion (workflows de déploiement, statut Vercel…).
            deploys = _checks(client, repo, pr["merge_commit_sha"]) if pr.get("merged_at") else []
        except GitHubError:
            continue  # retenté à la minute suivante
        result["seen"] += 1
        message, finished = render(pr, checks, deploys, now)
        fingerprint = _fingerprint(message)
        try:
            if known is None:
                message_id = post(url, message)
                PullRequestMessage.objects.create(
                    repo=repo, number=pr["number"], message_id=message_id, fingerprint=fingerprint, finished=finished
                )
                result["posted"] += 1
            elif known.fingerprint != fingerprint or known.finished != finished:
                if known.fingerprint != fingerprint:
                    edit(url, known.message_id, message)
                    result["edited"] += 1
                known.fingerprint, known.finished = fingerprint, finished
                known.save(update_fields=["fingerprint", "finished", "updated_at"])
        except notifications.NotificationError as exc:
            return {**result, "status": "error", "error": str(exc)}
    return result

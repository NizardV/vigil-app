"""Suivi des PR en direct dans Discord (#projets-pr) : un message par PR, modifié en place à chaque changement.

Toutes les minutes, UNE recherche GitHub liste les PR du compte mises à jour récemment (tous dépôts confondus). Pour
chacune encore active : ses vérifications (checks + statuts, ex. Vercel) puis, une fois fusionnée, celles du commit
de fusion (workflows de déploiement, statut Vercel). Le message n'est modifié que si son contenu change ; le suivi
s'arrête quand la PR est fusionnée/fermée et que plus rien ne tourne.
"""

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
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


def _checks(client: GitHubClient, repo: str, sha: str) -> list[dict[str, Any]]:
    """Check-runs et statuts de commit (Vercel…) d'un commit : nom, statut, conclusion, horaires, lien."""
    runs = client.get(f"/repos/{repo}/commits/{sha}/check-runs", {"per_page": 50}).get("check_runs", [])
    items = [
        {
            "name": r["name"],
            "status": r["status"],
            "conclusion": r.get("conclusion"),
            "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"),
            "url": r.get("html_url") or r.get("details_url") or "",
        }
        for r in runs
    ]
    for st in client.get(f"/repos/{repo}/commits/{sha}/status").get("statuses", []):
        state = st.get("state", "")
        items.append(
            {
                "name": st.get("context", "statut"),
                "status": "pending" if state == "pending" else "completed",
                "conclusion": {"success": "success", "failure": "failure", "error": "failure"}.get(state),
                "started_at": st.get("created_at"),
                "completed_at": None if state == "pending" else st.get("updated_at"),
                "url": st.get("target_url") or "",
            }
        )
    return items


IGNORED = {"skipped", "neutral"}  # vérifications sans intérêt (ex. « automerge » sauté) : ni affichées ni comptées
DEPLOY = re.compile(r"deploy|vercel|\bcd\b|release|publish", re.IGNORECASE)  # workflows de mise en production


def _ago(value: str | None) -> str:
    """Horodatage relatif Discord (« il y a 2 minutes ») : mis à jour par Discord, sans modifier le message."""
    return f" <t:{int(_parse(value).timestamp())}:R>" if value else ""


def _minutes(start: str, end: str) -> str:
    minutes = max(1, round((_parse(end) - _parse(start)).total_seconds() / 60))
    return f"{minutes} min" if minutes < 120 else f"{minutes // 60} h {minutes % 60:02d}"


def _checks_line(checks: list[dict[str, Any]]) -> tuple[str, bool]:
    """Résumé des vérifications : le total si tout va bien, le détail de ce qui tourne ou échoue."""
    shown = [c for c in checks if c["conclusion"] not in IGNORED]
    if not shown:
        return "", False
    running = [c for c in shown if c["status"] in RUNNING]
    failed = [c for c in shown if c["status"] not in RUNNING and c["conclusion"] != "success"]
    ok = len(shown) - len(running) - len(failed)
    parts = [f"{'✅' if ok == len(shown) else '☑️'} {ok}/{len(shown)} vérifications"]
    parts += [f"❌ [{notifications.escape(c['name'], 50)}]({c['url']})" if c["url"] else
              f"❌ {notifications.escape(c['name'], 50)}" for c in failed]  # fmt: skip
    parts += [f"⏳ {notifications.escape(c['name'], 50)}{_ago(c['started_at'])}" for c in running]
    return " · ".join(parts), bool(failed)


def _deploy_line(merged_at: str, merge_checks: list[dict[str, Any]]) -> str:
    deploys = [c for c in merge_checks if DEPLOY.search(c["name"]) and c["conclusion"] not in IGNORED]
    if not deploys:
        return ""
    if any(c["status"] in RUNNING for c in deploys):
        starts = [c["started_at"] for c in deploys if c["started_at"]]
        return f"⏳ Déploiement en cours{_ago(min(starts)) if starts else ''}"
    failed = [c for c in deploys if c["conclusion"] != "success"]
    if failed:
        link = f" — [logs]({failed[0]['url']})" if failed[0]["url"] else ""
        return f"❌ Déploiement échoué : {notifications.escape(failed[0]['name'], 50)}{link}"
    ends = [c["completed_at"] for c in deploys if c["completed_at"]]
    return f"🚀 Déployé en prod en {_minutes(merged_at, max(ends))}" if ends else "🚀 Déployé en prod"


def render(pr: dict[str, Any], checks: list, deploys: list, now: datetime) -> tuple[dict[str, Any], bool]:
    """Message Discord de la PR (compact) et indicateur « suivi terminé ». `deploys` = vérifications du commit de
    fusion, dont on ne retient que le déploiement (workflows « deploy », Vercel…)."""
    repo = pr["base"]["repo"]["full_name"]
    merged_at = pr.get("merged_at")
    if merged_at:
        state, color = "🟣 Fusionnée", PURPLE
    elif pr["state"] == "closed":
        state, color = "⚫ Fermée", GREY
    else:
        state, color = "🟢 Ouverte", GREEN
    stats = ""
    if pr.get("changed_files") is not None:
        stats = f" · +{pr.get('additions', 0)} −{pr.get('deletions', 0)} · {pr['changed_files']} fichier(s)"
    lines = [f"{state} · `{notifications.escape(pr['head']['ref'], 60)}` → `{pr['base']['ref']}`{stats}"]
    status, failed = _checks_line(checks)
    deploy = _deploy_line(merged_at, deploys) if merged_at else ""
    if failed and not merged_at:
        color = RED
    if deploy.startswith("❌"):
        color = RED
    lines.append(" · ".join(part for part in (status, deploy) if part))
    author = pr["user"]["login"]
    title = f"{'🤖 ' if author.endswith('[bot]') else ''}#{pr['number']} — {pr['title']}"
    message = {
        "embeds": [
            {
                "author": {"name": f"{repo.split('/')[-1]} · {author}", "url": f"https://github.com/{repo}"},
                "title": notifications.escape(title, 250),
                "url": pr["html_url"],
                "description": "\n".join(line for line in lines if line)[:3500],
                "color": color,
            }
        ]
    }
    running = any(c["status"] in RUNNING for c in checks + deploys)
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
        if known and (known.finished or not known.message_id):
            continue  # terminé, ou message en cours de création par une autre exécution
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
                # Réserve la ligne AVANT de poster : deux exécutions simultanées (tâche + commande manuelle) ne
                # peuvent pas poster deux messages pour la même PR ; la seconde bute sur la contrainte d'unicité.
                try:
                    with transaction.atomic():
                        row = PullRequestMessage.objects.create(repo=repo, number=pr["number"], message_id="")
                except IntegrityError:
                    continue
                try:
                    row.message_id = post(url, message)
                except notifications.NotificationError:
                    row.delete()  # rien n'est parti : retenté à la minute suivante
                    raise
                row.fingerprint, row.finished = fingerprint, finished
                row.save(update_fields=["message_id", "fingerprint", "finished", "updated_at"])
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

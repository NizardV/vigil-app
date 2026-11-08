"""Résumé d'activité hebdomadaire des projets, envoyé sur le salon Discord `projects-digest`.

Un seul message par semaine et par période, au lieu d'un message par push.
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from core.github import GitHubClient
from core.notifications import NotificationError, escape, send_discord, webhook_url

from .activity import ActivityReport, RepoActivity, collect_activity
from .models import TrackedRepo

CHANNEL = "projects-digest"
MAX_REPOS = 10  # projets détaillés ; le reste est compté
MAX_LINES_PER_REPO = 3
NEUTRAL_COLOR = 0x2563EB
DESCRIPTION_LIMIT = 3800  # l'API Discord plafonne la description d'un embed à 4096 caractères


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _entry(activity: RepoActivity) -> list[str]:
    parts = []
    if activity.commits:
        parts.append(_plural(len(activity.commits), "commit", "commits"))
    if activity.merged_prs:
        parts.append(_plural(len(activity.merged_prs), "PR fusionnée", "PR fusionnées"))
    if activity.dependency_updates:
        parts.append(_plural(activity.dependency_updates, "mise à jour de dépendance", "mises à jour de dépendances"))
    lines = [f"**{escape(activity.repo.split('/')[-1], 40)}** · {' · '.join(parts)}"]
    highlights = [*activity.merged_prs, *activity.commits][:MAX_LINES_PER_REPO]
    lines += [f"› {escape(text, 90)}" for text in highlights if text]
    return lines


def digest_payload(report: ActivityReport, since, until) -> dict[str, Any]:
    lines: list[str] = []
    for activity in report.entries[:MAX_REPOS]:
        lines += [*_entry(activity), ""]
    if len(report.entries) > MAX_REPOS:
        lines.append(f"… et **{len(report.entries) - MAX_REPOS}** autre(s) projet(s) actif(s).")
    if not report.entries:
        lines.append("_Aucune activité sur cette période._")
    notes = []
    if report.inactive:
        notes.append(_plural(report.inactive, "projet sans activité", "projets sans activité"))
    if report.unchecked:
        count = _plural(len(report.unchecked), "projet non vérifié", "projets non vérifiés")
        notes.append(f"⚠️ {count} (erreur ou droits)")
    if notes:
        lines += ["", f"_{' · '.join(notes)}_"]
    period = f"{timezone.localtime(since):%d/%m} → {timezone.localtime(until):%d/%m}"
    description = "\n".join(lines)
    return {
        "embeds": [
            {
                "title": f"📁 Résumé projets — {period}",
                "description": description[:DESCRIPTION_LIMIT],
                "color": 0xEA580C if report.unchecked else NEUTRAL_COLOR,
                "footer": {"text": "Vigil Projects"},
            }
        ]
    }


def send_weekly_digest(*, client=None, send=send_discord, dry_run=False, days: int = 7) -> dict[str, Any]:
    url = webhook_url(CHANNEL)
    if not url and not dry_run:
        return {"status": "not_configured"}  # inutile de consommer le quota GitHub pour rien
    until = timezone.now()
    since = until - timedelta(days=days)
    client = client or GitHubClient(settings.GITHUB_TOKEN)
    repos = list(TrackedRepo.objects.filter(is_archived=False).values_list("full_name", flat=True))
    report = collect_activity(client, repos, since)
    payload = digest_payload(report, since, until)
    summary = {"active": len(report.entries), "inactive": report.inactive, "unchecked": len(report.unchecked)}
    if dry_run:
        return {"status": "dry_run", "payload": payload, **summary}
    try:
        send(url, payload)
    except NotificationError as exc:
        return {"status": "error", "error": str(exc), **summary}
    return {"status": "sent", **summary}

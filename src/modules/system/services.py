"""Santé de Vigil lui-même : tâches en échec, bloquées ou en retard. Alerte sur le salon Discord « system ».

Ce que ce module ne peut PAS voir : un worker arrêté, puisque c'est lui qui exécute cette vérification.
Pour cela, un signal de vie vers un service externe (`VIGIL_HEARTBEAT_URL`) est envoyé après chaque
vérification réussie : si Vigil se tait, c'est ce service qui prévient.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import croniter
from django.conf import settings
from django.db.models import Max, Min
from django.utils import timezone
from procrastinate.contrib.django import app
from procrastinate.contrib.django.models import ProcrastinateEvent, ProcrastinateJob

from core.notifications import NotificationError, escape, ping, send_discord, webhook_url

from .models import SentAlert

CHANNEL = "system"
FAILED_WINDOW = timedelta(days=7)  # un échec plus ancien n'est plus signalé (évite un déluge au premier passage)
STUCK_AFTER = timedelta(hours=1)  # une tâche « en cours » depuis plus longtemps est considérée bloquée
OVERDUE_GRACE = timedelta(hours=2)  # marge après l'heure prévue d'une tâche périodique (durée, réessais)
MAX_LISTED = 10
RED = 0xB91C1C

Sender = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class Problem:
    key: str  # identifiant stable : sert à ne notifier qu'une fois
    kind: str  # failed | stuck | overdue
    task: str
    detail: str


def _last_event(job_id: int, event_type: str) -> datetime | None:
    return ProcrastinateEvent.objects.filter(job_id=job_id, type=event_type).aggregate(at=Max("at"))["at"]


def failed_jobs(now: datetime) -> list[Problem]:
    """Échecs regroupés PAR TÂCHE ET PAR JOUR : une tâche périodique qui échoue toutes les 20 minutes (API du modèle
    en panne, par exemple) ne doit produire qu'une alerte par jour, pas 72."""
    grouped: dict[tuple[str, str], list[tuple[ProcrastinateJob, datetime]]] = {}
    for job in ProcrastinateJob.objects.filter(status="failed"):
        failed_at = _last_event(job.id, "failed")
        if failed_at is None or now - failed_at > FAILED_WINDOW:
            continue
        day = timezone.localtime(failed_at).strftime("%Y%m%d")
        grouped.setdefault((job.task_name, day), []).append((job, failed_at))
    problems = []
    for (task_name, day), failures in grouped.items():
        job, failed_at = max(failures, key=lambda item: item[1])  # le plus récent représente le groupe
        local = timezone.localtime(failed_at)
        if len(failures) == 1:
            plural = "s" if job.attempts > 1 else ""
            detail = f"échec le {local:%d/%m à %H:%M} ({job.attempts} tentative{plural})"
        else:
            detail = f"{len(failures)} échecs le {local:%d/%m} (dernier à {local:%H:%M})"
        problems.append(Problem(f"failed:{task_name}:{day}", "failed", task_name, detail))
    return problems


def stuck_jobs(now: datetime) -> list[Problem]:
    problems = []
    for job in ProcrastinateJob.objects.filter(status="doing"):
        started = _last_event(job.id, "started")
        if started and now - started > STUCK_AFTER:
            minutes = int((now - started).total_seconds() // 60)
            problems.append(Problem(f"stuck:{job.id}", "stuck", job.task_name, f"en cours depuis {minutes} min"))
    return problems


def overdue_periodic(now: datetime) -> list[Problem]:
    """Tâches planifiées dont la dernière exécution prévue n'a jamais réussi (cron en UTC, comme eux)."""
    installed = ProcrastinateEvent.objects.aggregate(first=Min("at"))["first"]
    if installed is None:
        return []  # aucune activité : rien à comparer
    app.perform_import_paths()
    problems = []
    for (task_name, _), periodic in app.periodic_registry.periodic_tasks.items():
        expected = datetime.fromtimestamp(croniter.croniter(periodic.cron, now.timestamp()).get_prev(float), tz=UTC)
        if expected < installed or now < expected + OVERDUE_GRACE:
            continue  # prévu avant l'installation, ou encore dans la marge
        succeeded = ProcrastinateEvent.objects.filter(
            job__task_name=task_name, type="succeeded", at__gte=expected
        ).exists()
        if not succeeded:
            local = timezone.localtime(expected)
            problems.append(
                Problem(
                    f"overdue:{task_name}:{expected:%Y%m%d%H%M}",
                    "overdue",
                    task_name,
                    f"exécution prévue le {local:%d/%m à %H:%M} jamais terminée avec succès",
                )
            )
    return problems


def collect_problems(now: datetime | None = None) -> list[Problem]:
    now = now or timezone.now()
    return [*failed_jobs(now), *stuck_jobs(now), *overdue_periodic(now)]


ICON = {"failed": "❌", "stuck": "⏳", "overdue": "🕐"}


def payload(problems: list[Problem]) -> dict[str, Any]:
    lines = [f"{ICON[p.kind]} **{escape(p.task, 60)}** · {escape(p.detail, 100)}" for p in problems[:MAX_LISTED]]
    if len(problems) > MAX_LISTED:
        lines.append(f"… et **{len(problems) - MAX_LISTED}** autre(s).")
    return {
        "embeds": [
            {
                "title": f"⚠️ Vigil — {len(problems)} problème(s) système",
                "description": "\n".join(lines),
                "color": RED,
                "footer": {"text": "Vigil System"},
            }
        ]
    }


def check_and_notify(*, send: Sender = send_discord, dry_run: bool = False, now: datetime | None = None) -> dict:
    """Détecte les problèmes et notifie les nouveaux. Rien n'est marqué si l'envoi échoue."""
    problems = collect_problems(now)
    already = set(SentAlert.objects.filter(key__in=[p.key for p in problems]).values_list("key", flat=True))
    new = [p for p in problems if p.key not in already]
    summary = {"problems": len(problems), "new": len(new)}
    if not new:
        return {"status": "nothing", **summary}
    message = payload(new)
    if dry_run:
        return {"status": "dry_run", "payload": message, **summary}
    url = webhook_url(CHANNEL)
    if not url:
        return {"status": "not_configured", **summary}  # rien n'est marqué : ce sera signalé plus tard
    try:
        send(url, message)
    except NotificationError as exc:
        return {"status": "error", "error": str(exc), **summary}
    SentAlert.objects.bulk_create([SentAlert(key=p.key, kind=p.kind) for p in new], ignore_conflicts=True)
    return {"status": "sent", **summary}


def send_heartbeat(*, ping_fn: Callable[[str], None] = ping) -> str:
    """Signal de vie vers le service externe. Renvoie « sent », « not_configured » ou « error »."""
    url = settings.VIGIL_HEARTBEAT_URL
    if not url:
        return "not_configured"
    try:
        ping_fn(url)
    except NotificationError:
        return "error"
    return "sent"

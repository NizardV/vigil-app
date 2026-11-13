"""Tâches de fond du module projects : enveloppes fines autour de `services.py` (ADR 0003)."""

from procrastinate import RetryStrategy
from procrastinate.contrib.django import app

from . import notifications, services


@app.periodic(cron="0 4 * * *")  # tous les jours à 04:00
@app.task(
    name="projects.sync_repos",
    queueing_lock="projects.sync_repos",  # jamais deux synchros en attente en même temps
    retry=RetryStrategy(max_attempts=3, wait=300),
)
def sync_repos(timestamp: int) -> int:
    """Synchronise les repos GitHub. Idempotent : peut être relancé sans effet de bord."""
    return services.run_sync()


@app.periodic(cron="0 9 * * 1")  # chaque lundi à 09:00, après le résumé sécurité (08:00)
@app.task(
    name="projects.weekly_digest",
    queueing_lock="projects.weekly_digest",
    retry=RetryStrategy(max_attempts=3, wait=900),
)
def weekly_digest(timestamp: int) -> dict:
    """Résumé d'activité hebdomadaire des projets (un message, bruit écarté)."""
    result = notifications.send_weekly_digest()
    if result["status"] == "error":
        raise RuntimeError(f"Résumé projets non envoyé : {result['error']}")
    return result


@app.periodic(cron="* * * * *")  # toutes les minutes : suivi des PR en direct (#projets-pr)
@app.task(name="projects.track_pull_requests", queueing_lock="projects.track_pull_requests")
def track_pull_requests(timestamp: int) -> dict:
    from .pull_requests import track_pull_requests as run

    return run()

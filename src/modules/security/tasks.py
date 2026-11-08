"""Tâches de fond du module security : enveloppes fines autour de `services.py` (ADR 0003)."""

from procrastinate import RetryStrategy
from procrastinate.contrib.django import app

from . import notifications, notion_sync, services


@app.periodic(cron="0 5 * * *")  # tous les jours à 05:00, après la synchro projets (04:00)
@app.task(
    name="security.audit_github",
    queueing_lock="security.audit_github",
    retry=RetryStrategy(max_attempts=2, wait=600),
)
def audit_github(timestamp: int) -> dict:
    """Audit des dépôts GitHub puis alerte Discord sur les nouveaux constats graves. Idempotent."""
    audit = services.run_github_audit()
    alert = notifications.notify_new_findings()
    if alert["status"] == "error":
        # L'audit est enregistré ; on échoue pour que la tâche soit réessayée (aucun constat n'a été
        # marqué notifié) : une alerte de sécurité perdue en silence est pire qu'une tâche en échec.
        raise RuntimeError(f"Alerte Discord non envoyée : {alert['error']}")
    return {"audit": audit, "alert": alert}


@app.periodic(cron="10 5 * * *")  # 05:10 UTC, juste après l'audit GitHub
@app.task(
    name="security.audit_vps",
    queueing_lock="security.audit_vps",
    retry=RetryStrategy(max_attempts=2, wait=600),
)
def audit_vps(timestamp: int) -> dict:
    """Audit du VPS par SSH (lecture seule), puis alerte Discord sur les nouveaux constats graves."""
    audit = services.run_vps_audit()
    if audit["status"] == "error":
        raise RuntimeError(f"Audit VPS impossible : {audit['error']}")
    alert = notifications.notify_new_findings()
    if alert["status"] == "error":
        raise RuntimeError(f"Alerte Discord non envoyée : {alert['error']}")
    return {"audit": audit, "alert": alert}


@app.periodic(cron="20 5 * * *")  # 05:20 UTC, après l'audit GitHub et celui du VPS
@app.task(
    name="security.audit_sonarcloud",
    queueing_lock="security.audit_sonarcloud",
    retry=RetryStrategy(max_attempts=2, wait=600),
)
def audit_sonarcloud(timestamp: int) -> dict:
    """Audit du code par SonarCloud (lecture publique), puis alerte Discord sur les nouveaux constats graves."""
    audit = services.run_sonarcloud_audit()
    alert = notifications.notify_new_findings()
    if alert["status"] == "error":
        raise RuntimeError(f"Alerte Discord non envoyée : {alert['error']}")
    return {"audit": audit, "alert": alert}


@app.periodic(cron="0 8 * * 1")  # chaque lundi à 08:00
@app.task(
    name="security.weekly_digest",
    queueing_lock="security.weekly_digest",
    retry=RetryStrategy(max_attempts=3, wait=900),
)
def weekly_digest(timestamp: int) -> dict:
    """Résumé hebdomadaire. Son absence est elle-même un signal : l'audit ou Vigil ne tourne plus."""
    result = notifications.send_digest()
    if result["status"] == "error":
        raise RuntimeError(f"Résumé Discord non envoyé : {result['error']}")
    return result


@app.periodic(cron="30 5 * * *")  # 05:30 UTC, une fois l'audit de 05:00 terminé
@app.task(
    name="security.sync_notion",
    queueing_lock="security.sync_notion",
    retry=RetryStrategy(max_attempts=3, wait=600),
)
def sync_notion(timestamp: int) -> dict:
    """Met à jour la page Notion « Vigil Security » (zone générée uniquement). Idempotent."""
    result = notion_sync.run_notion_sync()
    if result["status"] == "error":
        raise RuntimeError(f"Synchro Notion échouée : {result['error']}")
    return result

"""Tâche de fond du module system : vérification de santé toutes les 15 minutes."""

from procrastinate import RetryStrategy
from procrastinate.contrib.django import app

from . import services


@app.periodic(cron="*/15 * * * *")
@app.task(
    name="system.health_check",
    queueing_lock="system.health_check",
    retry=RetryStrategy(max_attempts=2, wait=120),
)
def health_check(timestamp: int) -> dict:
    """Détecte tâches en échec/bloquées/en retard, alerte, puis émet le signal de vie.

    Le signal de vie n'est envoyé qu'APRÈS une vérification réussie : si celle-ci échoue ou si Vigil est
    arrêté,
    le silence est détecté par le service externe.
    """
    result = services.check_and_notify()
    if result["status"] == "error":
        raise RuntimeError(f"Alerte système non envoyée : {result['error']}")
    return {**result, "heartbeat": services.send_heartbeat()}


@app.periodic(cron="*/5 * * * *")
@app.task(name="system.uptime_check", queueing_lock="system.uptime_check")
def uptime_check(timestamp: int) -> dict:
    """Disponibilité des sites hébergés à côté de Vigil (VIGIL_UPTIME_SITES) ; alerte #système aux changements."""
    from .uptime import check_sites

    return check_sites()

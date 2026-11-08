"""Tâches de fond du module watch : enveloppes fines autour de `services.py` (ADR 0003). Horaires en UTC."""

from procrastinate import RetryStrategy
from procrastinate.contrib.django import app

from . import services


@app.periodic(cron="7 * * * *")  # toutes les heures : collecte les sources dont l'intervalle est écoulé
@app.task(
    name="watch.fetch_sources",
    queueing_lock="watch.fetch_sources",
    retry=RetryStrategy(max_attempts=2, wait=300),
)
def fetch_sources(timestamp: int) -> dict:
    """Une source en échec n'est PAS une panne de la tâche : son erreur est visible dans l'admin."""
    return services.run_fetch()


@app.periodic(cron="17,37,57 * * * *")  # toutes les 20 minutes, au plus N analyses par passage (plafond de coût)
@app.task(name="watch.analyze_pending", queueing_lock="watch.analyze_pending")
def analyze_pending(timestamp: int) -> dict:
    result = services.run_analysis()
    if result.get("stopped"):
        # Le service du modèle est en panne (clé, quota, réseau) : sans cette alerte, la veille s'arrêterait en silence.
        raise RuntimeError(f"Analyse interrompue : {result['stopped']}")
    return result


@app.periodic(cron="3 * * * *")  # toutes les heures : envoie les résumés dont c'est l'heure (heure de Paris)
@app.task(
    name="watch.send_digests",
    queueing_lock="watch.send_digests",
    retry=RetryStrategy(max_attempts=3, wait=900),
)
def send_digests(timestamp: int) -> dict:
    result = services.run_digests()
    if result["status"] == "error":
        failed = ", ".join(r["theme"] for r in result["results"] if r["status"] == "error")
        raise RuntimeError(f"Résumé non envoyé pour : {failed}")
    return result

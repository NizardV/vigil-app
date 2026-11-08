"""Logique métier du module watch (seule porte d'entrée pour les autres modules)."""

import re
from datetime import datetime
from typing import Any

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.utils import timezone

from core.llm import GeminiClient

from . import analysis, digest, feeds
from .models import Article, Feedback

FEEDBACK_AUTHOR_LIMIT = 100
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def make_llm() -> GeminiClient | None:
    """Client Gemini, ou None si la clé n'est pas configurée."""
    if not settings.GEMINI_API_KEY:
        return None
    return GeminiClient(settings.GEMINI_API_KEY, settings.GEMINI_MODEL)


def run_fetch() -> dict[str, Any]:
    return feeds.fetch_due_sources()


def run_analysis() -> dict[str, Any]:
    llm = make_llm()
    if llm is None:
        return {"status": "not_configured"}
    return {"status": "ok", **analysis.process_pending(llm)}


def run_digests(*, now: datetime | None = None, dry_run: bool = False, only: str | None = None, force: bool = False):
    """Envoie les résumés dus. `only` cible un thème (slug) ; `force` ignore l'heure et le « déjà envoyé »."""
    from .models import Theme

    now = now or timezone.now()
    themes = list(Theme.objects.filter(slug=only)) if only else digest.due_themes(now)
    if only and not force:
        themes = [t for t in themes if t in digest.due_themes(now)]
    llm = make_llm()
    results = [digest.send_theme_digest(t, llm=llm, now=now, dry_run=dry_run) for t in themes]
    errors = [r for r in results if r["status"] == "error"]
    return {"status": "error" if errors else "ok", "themes": len(themes), "results": results}


def record_feedback(article_id: int, rating: int, author: str, origin: str = "discord") -> Feedback | None:
    """Enregistre un 👍/👎 (le dernier retour d'un auteur remplace le précédent). None si l'article n'existe pas."""
    if rating not in (1, -1):
        raise ValueError("la note doit valoir 1 (👍) ou -1 (👎)")
    if not Article.objects.filter(pk=article_id).exists():
        return None
    feedback, _ = Feedback.objects.update_or_create(
        article_id=article_id,
        author=(author or "inconnu")[:FEEDBACK_AUTHOR_LIMIT],
        defaults={"rating": rating, "origin": origin, "created_at": timezone.now()},
    )
    return feedback


def search_articles(query: str, limit: int = 20):
    """Recherche plein texte (titre + résumé). Remplace l'ancienne recherche sémantique, sans modèle local."""
    # PostgreSQL refuse l'octet nul (erreur 500) : on retire les caractères de contrôle et on borne la longueur.
    query = _CONTROL.sub(" ", query or "").strip()[:200]
    if not query:
        return Article.objects.none()
    vector = SearchVector("title", weight="A", config="simple") + SearchVector(
        "analysis__summary", weight="B", config="simple"
    )
    search = SearchQuery(query, config="simple", search_type="websearch")
    return (
        Article.objects.annotate(rank=SearchRank(vector, search))
        .filter(rank__gt=0)
        .order_by("-rank", "-fetched_at")
        .select_related("source__theme", "analysis")[:limit]
    )

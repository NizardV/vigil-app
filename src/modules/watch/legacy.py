"""Import des données de veille de l'ancien Vigil (FastAPI) vers ce module.

Principe : on charge d'abord un `pg_dump` de l'ancienne base dans un schéma temporaire de la nouvelle base (par
exemple `legacy`), puis on copie ce schéma vers les tables du module. L'import est **idempotent** (relancer ne
duplique rien) et peut être simulé (`dry_run`).

Ce que l'import fait volontairement :
- les anciens articles sont marqués « déjà envoyés » : sinon le premier résumé renverrait tout l'historique ;
- les URL d'articles sont normalisées comme celles des nouveaux articles : sinon un article déjà connu (avec des
  paramètres `utm_*`) serait recréé en doublon ;
- l'heure d'envoi, stockée en UTC dans l'ancienne version, est convertie en heure de Paris ;
- les embeddings ne sont pas repris (recherche plein texte à la place) ;
- les URL de webhooks stockées en clair dans l'ancienne base sont IGNORÉES : les salons sont désormais des
  variables d'environnement (`DISCORD_WEBHOOK_WATCH_<THEME>`).
"""

import re
from datetime import UTC, datetime
from typing import Any

from django.db import connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from .digest import PARIS
from .feeds import normalize_url
from .models import Analysis, Article, Digest, Feedback, Source, Theme

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
TABLES = ("themes", "sources", "articles", "analyses", "feedbacks", "digests")


class LegacyError(Exception):
    pass


def paris_offset_hours(now: datetime | None = None) -> int:
    """Décalage actuel de Paris par rapport à l'UTC (1 en hiver, 2 en été)."""
    return int((now or timezone.now()).astimezone(PARIS).utcoffset().total_seconds() // 3600)


def _rows(schema: str, table: str) -> list[dict[str, Any]]:
    quoted = f"{connection.ops.quote_name(schema)}.{connection.ops.quote_name(table)}"
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * FROM {quoted} ORDER BY id")  # noqa: S608 - identifiants validés puis quotés
        columns = [c.name for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _existing_tables(schema: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", [schema])
        return {row[0] for row in cursor.fetchall()}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return (
        timezone.make_aware(value, UTC) if timezone.is_naive(value) else value
    )  # l'ancien code stockait de l'UTC naïf


def _unique_slug(name: str) -> str:
    base = slugify(name)[:50] or "theme"
    slug, n = base, 2
    while Theme.objects.filter(slug=slug).exclude(name=name).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def import_legacy(schema: str = "legacy", *, dry_run: bool = False, now: datetime | None = None) -> dict[str, Any]:
    if not _SCHEMA.match(schema):
        raise LegacyError("nom de schéma invalide")
    present = _existing_tables(schema)
    missing = [t for t in TABLES if t not in present]
    if missing:
        raise LegacyError(f"tables absentes du schéma « {schema} » : {', '.join(missing)}")
    offset = paris_offset_hours(now)
    counts = {name: 0 for name in (*TABLES, "skipped_articles")}

    with transaction.atomic():
        themes: dict[int, Theme] = {}
        for row in _rows(schema, "themes"):
            theme, _ = Theme.objects.update_or_create(
                name=row["name"][:100],
                defaults={
                    "slug": _unique_slug(row["name"][:100]),
                    "description": row.get("description") or "",
                    "keywords": [str(k)[:60] for k in (row.get("keywords") or [])],
                    "digest_enabled": bool(row.get("digest_enabled", True)),
                    "digest_hour": ((row.get("digest_hour") or 0) + offset) % 24,  # UTC -> heure de Paris
                },
            )
            themes[row["id"]] = theme
            counts["themes"] += 1

        sources: dict[int, Source] = {}
        valid_kinds = set(Source.Kind.values)
        for row in _rows(schema, "sources"):
            theme = themes.get(row["theme_id"])
            if theme is None:
                continue
            kind = row.get("type") if row.get("type") in valid_kinds else Source.Kind.RSS
            source, _ = Source.objects.update_or_create(
                theme=theme,
                url=row["url"][:500],
                defaults={
                    "name": (row.get("name") or "")[:100],
                    "feed_url": row["url"][:700],  # l'ancien code stockait déjà l'URL du flux détectée
                    "kind": kind,
                    "active": bool(row.get("active", True)),
                    "fetch_interval_hours": min(168, max(1, row.get("fetch_interval_hours") or 6)),
                },
            )
            sources[row["id"]] = source
            counts["sources"] += 1

        articles: dict[int, Article] = {}
        for row in _rows(schema, "articles"):
            source, url = sources.get(row["source_id"]), normalize_url(row["url"])
            if source is None or not url:
                counts["skipped_articles"] += 1
                continue
            fetched = _aware(row.get("fetched_at")) or timezone.now()
            article, _ = Article.objects.get_or_create(
                url=url,
                defaults={
                    "source": source,
                    "title": (row.get("title") or "Sans titre")[:500],
                    "content_raw": (row.get("content_raw") or "")[:5000],
                    "published_at": _aware(row.get("published_at")),
                    "fetched_at": fetched,
                    "processed": bool(row.get("processed")),
                    "digested_at": fetched,  # l'historique est considéré comme déjà envoyé
                },
            )
            articles[row["id"]] = article
            counts["articles"] += 1

        for row in _rows(schema, "analyses"):
            article = articles.get(row["article_id"])
            if article is None or row.get("relevance_score") is None:
                continue
            Analysis.objects.update_or_create(
                article=article,
                defaults={
                    "summary": row.get("summary") or "",
                    "key_points": [str(p)[:300] for p in (row.get("key_points") or [])][:3],
                    "relevance_score": min(10.0, max(1.0, float(row["relevance_score"]))),
                    "theme_match": (row.get("theme_match") or "")[:100],
                    "prompt_version": (row.get("llm_prompt_version") or "")[:20],
                    "created_at": _aware(row.get("created_at")) or timezone.now(),
                },
            )
            counts["analyses"] += 1

        for row in _rows(schema, "feedbacks"):
            article = articles.get(row["article_id"])
            if article is None or row.get("rating") not in (1, -1):
                continue
            Feedback.objects.update_or_create(
                article=article,
                author=f"legacy:{row.get('user_id') or 'inconnu'}"[:100],
                defaults={
                    "rating": row["rating"],
                    "origin": "legacy",
                    "comment": row.get("comment") or "",
                    "created_at": _aware(row.get("created_at")) or timezone.now(),
                },
            )
            counts["feedbacks"] += 1

        for row in _rows(schema, "digests"):
            theme = themes.get(row["theme_id"])
            if theme is None:
                continue
            Digest.objects.get_or_create(
                theme=theme,
                sent_at=_aware(row.get("sent_at")) or timezone.now(),
                defaults={"content": row.get("content") or "", "channel": (row.get("channel") or "discord")[:50]},
            )
            counts["digests"] += 1

        if dry_run:
            transaction.set_rollback(True)  # simulation : rien n'est conservé
    return counts

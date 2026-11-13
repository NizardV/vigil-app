"""Collecte des sources de veille : détection du type de flux, téléchargement sûr, enregistrement des articles.

Le téléchargement passe par `core.netfetch` : une URL saisie par l'utilisateur ne peut jamais atteindre le réseau
interne (127.0.0.1, services Docker, métadonnées cloud).
"""

import re
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import feedparser
from django.utils import timezone

from core.netfetch import FetchError, fetch_public

from .models import Article, Source

MAX_PER_SOURCE = 20
MAX_NEW_PER_FETCH = 10  # nouveaux articles gardés par source et par collecte (plafond du coût d'analyse)
MAX_FAILURES = 10  # une source qui échoue 10 fois d'affilée est suspendue (à remettre à zéro à la main)
SLACK = timedelta(minutes=5)
URL_LIMIT = 1000
TRACKING_PARAMS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "igshid", "yclid", "_hsenc", "_hsmi",
}  # fmt: skip

_YOUTUBE_ID = re.compile(r"youtube\.com/channel/(UC[\w-]{22})")
_YOUTUBE_NAMED = re.compile(r"youtube\.com/(?:@[\w.-]+|user/[\w.-]+|c/[\w.-]+)")
_YOUTUBE_PAGE_ID = re.compile(r'(?:"externalId"|"channelId"):"(UC[\w-]{22})"')
_REDDIT = re.compile(r"reddit\.com/r/([\w-]+)")
_GITHUB = re.compile(r"github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/|$)")
_FEED_SUFFIX = (".xml", ".atom", ".rss", ".rdf")

Fetcher = Callable[..., bytes]


def normalize_url(url: str) -> str:
    """URL canonique d'un article : sans fragment ni paramètres de suivi (utm_*, fbclid…). « » si non http(s)."""
    try:
        parts = urllib.parse.urlsplit((url or "").strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    query = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    cleaned = urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path, urllib.parse.urlencode(query), "")
    )
    return cleaned[:URL_LIMIT]


def detect_source(url: str) -> tuple[str, str]:
    """Renvoie (type, URL du flux). Pour une chaîne YouTube nommée, l'URL du flux est vide : il faut la résoudre."""
    url = url.strip()
    if url.startswith("hn:"):
        keyword = urllib.parse.quote(url[3:].strip())
        return Source.Kind.HACKERNEWS, f"https://hnrss.org/newest?q={keyword}&points=10"
    if urllib.parse.urlsplit(url).path.lower().endswith(_FEED_SUFFIX):
        return Source.Kind.RSS, url  # c'est déjà un flux
    if match := _YOUTUBE_ID.search(url):
        return Source.Kind.YOUTUBE, f"https://www.youtube.com/feeds/videos.xml?channel_id={match.group(1)}"
    if _YOUTUBE_NAMED.search(url):
        return Source.Kind.YOUTUBE, ""
    if match := _REDDIT.search(url):
        return Source.Kind.REDDIT, f"https://www.reddit.com/r/{match.group(1)}/top/.rss?t=week"
    if match := _GITHUB.search(url):
        return Source.Kind.GITHUB, f"https://github.com/{match.group(1)}/{match.group(2)}/releases.atom"
    return Source.Kind.RSS, url


def ensure_feed_url(source: Source, *, fetch: Fetcher = fetch_public) -> str:
    """Détermine (et mémorise) l'URL réelle du flux d'une source."""
    if source.feed_url:
        return source.feed_url
    kind, feed_url = detect_source(source.url)
    if kind == Source.Kind.YOUTUBE and not feed_url:
        page = fetch(source.url).decode("utf-8", errors="replace")
        match = _YOUTUBE_PAGE_ID.search(page)
        if not match:
            raise ValueError("identifiant de la chaîne YouTube introuvable")
        feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={match.group(1)}"
    source.kind, source.feed_url = kind, feed_url
    source.save(update_fields=["kind", "feed_url"])
    return feed_url


def _published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    try:
        return datetime(*parsed[:6], tzinfo=UTC) if parsed else None
    except (TypeError, ValueError):
        return None


def parse_entries(body: bytes) -> list[dict]:
    """Extrait les articles d'un flux RSS/Atom. Lève ValueError si le contenu n'est pas un flux."""
    feed = feedparser.parse(body)  # des octets : feedparser ne fait AUCUN accès réseau
    # `version` est vide quand le contenu n'est PAS reconnu comme un flux (page web, contenu vide…). Sans ce contrôle,
    # une source mal saisie passerait pour un flux valide mais vide, et ne produirait jamais la moindre erreur.
    if not feed.get("version"):
        raise ValueError("contenu illisible : ce n'est pas un flux RSS/Atom")
    entries = []
    for entry in feed.entries[:MAX_PER_SOURCE]:
        url = normalize_url(entry.get("link", ""))
        if not url:
            continue
        content = entry.get("summary") or entry.get("description") or ""
        entries.append(
            {
                "url": url,
                "title": (entry.get("title") or "Sans titre").strip()[:500],
                "content_raw": content[:5000],
                "published_at": _published(entry),
            }
        )
    return entries


def fetch_source(source: Source, *, fetch: Fetcher = fetch_public, now: datetime | None = None) -> int:
    """Collecte une source et renvoie le nombre de NOUVEAUX articles. Lève en cas d'échec (l'appelant enregistre)."""
    now = now or timezone.now()
    entries = parse_entries(fetch(ensure_feed_url(source, fetch=fetch)))
    # Chaque nouvel article coûte une analyse Gemini : on ne garde que ce qui peut finir dans un résumé.
    # - publié avant la période du résumé du thème : il n'y figurera jamais ;
    # - au plus MAX_NEW_PER_FETCH nouveaux articles par source et par collecte, les plus récents d'abord.
    oldest = now - timedelta(days=source.theme.lookback_days)
    known = set(Article.objects.filter(url__in=[e["url"] for e in entries]).values_list("url", flat=True))
    fresh = [e for e in entries if e["url"] not in known and (e["published_at"] is None or e["published_at"] >= oldest)]
    fresh.sort(key=lambda e: e["published_at"] or now, reverse=True)
    entries = fresh[:MAX_NEW_PER_FETCH]
    created = 0
    for item in entries:
        _, was_created = Article.objects.get_or_create(
            url=item["url"],
            defaults={
                "source": source,
                "title": item["title"],
                "content_raw": item["content_raw"],
                "published_at": item["published_at"],
                "fetched_at": now,
            },
        )
        created += was_created
    source.last_fetched_at, source.last_error, source.failures = now, "", 0
    source.save(update_fields=["last_fetched_at", "last_error", "failures"])
    return created


def is_due(source: Source, now: datetime) -> bool:
    if source.last_fetched_at is None:
        return True
    return now - source.last_fetched_at >= timedelta(hours=source.fetch_interval_hours) - SLACK


def fetch_due_sources(*, fetch: Fetcher = fetch_public, now: datetime | None = None) -> dict:
    """Collecte les sources dont l'intervalle est écoulé. Une source en échec n'empêche jamais les autres."""
    now = now or timezone.now()
    sources = [s for s in Source.objects.filter(active=True, failures__lt=MAX_FAILURES) if is_due(s, now)]
    summary = {"due": len(sources), "fetched": 0, "failed": 0, "new_articles": 0}
    for source in sources:
        try:
            summary["new_articles"] += fetch_source(source, fetch=fetch, now=now)
            summary["fetched"] += 1
        except (FetchError, ValueError, OSError) as exc:
            summary["failed"] += 1
            source.failures += 1
            source.last_error = str(exc)[:300]
            source.save(update_fields=["failures", "last_error"])
    return summary

"""Thèmes et sources de départ (sources officielles ou de référence, flux testés). Rejouable sans doublon."""

from .models import Source, Theme

# Résumé le lundi à 9h (Paris), articles des 7 derniers jours. Modifiable ensuite dans l'admin.
WEEKLY = {"digest_hour": 9, "digest_days": [0], "lookback_days": 7, "min_score": 6.0}

PRESET: list[dict] = [
    {
        "slug": "cybersecurite",
        "name": "Cybersécurité",
        "description": "Vulnérabilités critiques, attaques en cours, fuites de données, bonnes pratiques de défense.",
        "keywords": [
            "vulnérabilité",
            "CVE",
            "ransomware",
            "0-day",
            "fuite de données",
            "authentification",
            "chiffrement",
        ],
        "sources": [
            ("CERT-FR : alertes", "https://www.cert.ssi.gouv.fr/alerte/feed/"),
            ("CERT-FR : actualités", "https://www.cert.ssi.gouv.fr/actualite/feed/"),
            ("CISA : alertes", "https://www.cisa.gov/cybersecurity-advisories/alerts.xml"),
            ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
            ("BleepingComputer", "https://www.bleepingcomputer.com/feed/"),
            ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews"),
            ("Schneier on Security", "https://www.schneier.com/feed/atom/"),
            ("SANS Internet Storm Center", "https://isc.sans.edu/rssfeed.xml"),
            ("Google Project Zero", "https://googleprojectzero.blogspot.com/feeds/posts/default"),
        ],
    },
    {
        "slug": "juridique",
        "name": "Juridique",
        "description": "Droit du numérique et des données : RGPD, décisions de la CNIL, lois et jurisprudence.",
        "keywords": [
            "RGPD",
            "CNIL",
            "protection des données",
            "droit du numérique",
            "jurisprudence",
            "régulation",
            "IA Act",
        ],
        "sources": [
            ("CNIL : actualités", "https://www.cnil.fr/fr/rss.xml"),
            ("Dalloz actualité", "https://www.dalloz-actualite.fr/rss.xml"),
            ("Village de la Justice", "https://www.village-justice.com/articles/spip.php?page=backend"),
        ],
    },
    {
        "slug": "technologique",
        "name": "Technologique",
        "description": "Évolutions techniques de fond : logiciel, cloud, Linux, IA, langages et outils.",
        "keywords": [
            "Linux",
            "cloud",
            "intelligence artificielle",
            "Python",
            "Django",
            "infrastructure",
            "open source",
        ],
        "sources": [
            ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
            ("Next", "https://next.ink/feed/"),
            ("LWN.net", "https://lwn.net/headlines/rss"),
            ("InfoQ", "https://feed.infoq.com/"),
            ("The Register", "https://www.theregister.com/headlines.atom"),
            ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
            ("Cloudflare : blog", "https://blog.cloudflare.com/rss/"),
            ("GitHub : blog", "https://github.blog/feed/"),
            ("Django News", "https://django-news.com/issues.rss"),
        ],
    },
]


def seed(channels: dict[str, str] | None = None) -> dict[str, int]:
    """Crée les thèmes et sources manquants. `channels` : {slug: identifiant du salon Discord}.

    Ne modifie jamais un thème existant (sauf l'identifiant du salon s'il est fourni) : les réglages faits dans l'admin
    sont conservés.
    """
    channels = channels or {}
    created = {"themes": 0, "sources": 0}
    for spec in PRESET:
        theme, new = Theme.objects.get_or_create(
            slug=spec["slug"],
            defaults={
                "name": spec["name"],
                "description": spec["description"],
                "keywords": spec["keywords"],
                **WEEKLY,
            },
        )
        created["themes"] += new
        channel = channels.get(spec["slug"])
        if channel and theme.discord_channel_id != channel:
            theme.discord_channel_id = channel
            theme.save(update_fields=["discord_channel_id"])
        for name, url in spec["sources"]:
            _, new = Source.objects.get_or_create(theme=theme, url=url, defaults={"name": name})
            created["sources"] += new
    return created

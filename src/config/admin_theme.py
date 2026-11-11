"""Thème de l'admin (django-unfold, ADR 0006) : identité visuelle, navigation, tableau de bord.

Importé par settings.py : n'importer ici ni `django.conf.settings` ni un module métier (tout est paresseux).
"""

from django.templatetags.static import static
from django.urls import reverse_lazy


def _link(title: str, icon: str, url_name: str) -> dict:
    return {"title": title, "icon": icon, "link": reverse_lazy(url_name)}


# Palette tirée du logo : cyan (primaire) sur fond bleu nuit (base). Échelles OKLCH attendues par unfold.
_PRIMARY = {  # cyan
    "50": "oklch(98.4% .019 200.9)",
    "100": "oklch(95.6% .045 203.4)",
    "200": "oklch(91.7% .08 205)",
    "300": "oklch(86.5% .127 207.1)",
    "400": "oklch(78.9% .154 211.5)",
    "500": "oklch(71.5% .143 215.2)",
    "600": "oklch(60.9% .126 221.7)",
    "700": "oklch(52% .105 223.1)",
    "800": "oklch(45% .085 224.3)",
    "900": "oklch(39.8% .07 227.4)",
    "950": "oklch(30.2% .056 229.7)",
}
_BASE = {  # gris teinté bleu nuit, comme le fond du logo
    "50": "oklch(98.5% .003 278)",
    "100": "oklch(96.7% .006 278)",
    "200": "oklch(92.8% .011 278)",
    "300": "oklch(87.2% .017 278)",
    "400": "oklch(70.7% .03 278)",
    "500": "oklch(55.1% .038 278)",
    "600": "oklch(44.6% .045 278)",
    "700": "oklch(37.3% .052 278)",
    "800": "oklch(27.8% .058 278)",
    "900": "oklch(21% .062 278)",
    "950": "oklch(16% .06 278)",
}

UNFOLD = {
    "SITE_TITLE": "Vigil",
    "SITE_HEADER": "Vigil",
    "SITE_SUBHEADER": "Automatisation et veille",
    "SITE_URL": "/",
    "SITE_ICON": lambda request: static("vigil/logo.png"),
    "SITE_SYMBOL": "shield",
    "SITE_FAVICONS": [
        {"rel": "icon", "sizes": "32x32", "type": "image/png", "href": lambda request: static("vigil/favicon-32.png")},
        {"rel": "icon", "sizes": "16x16", "type": "image/png", "href": lambda request: static("vigil/favicon-16.png")},
        {"rel": "apple-touch-icon", "sizes": "180x180", "href": lambda request: static("vigil/apple-touch-icon.png")},
    ],
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {"base": _BASE, "primary": _PRIMARY},
    "DASHBOARD_CALLBACK": "config.admin_dashboard.dashboard_callback",
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Tableau de bord",
                "items": [_link("Accueil", "dashboard", "admin:index")],
            },
            {
                "title": "Sécurité",
                "collapsible": True,
                "items": [
                    _link("Constats", "gpp_maybe", "admin:security_finding_changelist"),
                    _link("Couverture des contrôles", "fact_check", "admin:security_checkresult_changelist"),
                ],
            },
            {
                "title": "Veille",
                "collapsible": True,
                "items": [
                    _link("Thèmes", "category", "admin:watch_theme_changelist"),
                    _link("Sources", "rss_feed", "admin:watch_source_changelist"),
                    _link("Articles", "article", "admin:watch_article_changelist"),
                    _link("Résumés", "summarize", "admin:watch_digest_changelist"),
                    _link("Retours", "thumbs_up_down", "admin:watch_feedback_changelist"),
                ],
            },
            {
                "title": "Projets",
                "collapsible": True,
                "items": [_link("Dépôts suivis", "folder_code", "admin:projects_trackedrepo_changelist")],
            },
            {
                "title": "Système",
                "collapsible": True,
                "items": [
                    _link("Sites surveillés", "monitor_heart", "admin:system_sitestatus_changelist"),
                    _link("Tâches de fond", "schedule", "admin:procrastinate_procrastinatejob_changelist"),
                    _link("Alertes envoyées", "notifications", "admin:system_sentalert_changelist"),
                    _link("Utilisateurs", "person", "admin:auth_user_changelist"),
                    _link("Groupes", "group", "admin:auth_group_changelist"),
                ],
            },
            {
                "title": "Accès",
                "collapsible": True,
                "items": [
                    _link("Tentatives de connexion", "lock_person", "admin:axes_accessattempt_changelist"),
                    _link("Passkeys", "passkey", "two_factor_passkeys"),
                    _link("Appareils 2FA", "phonelink_lock", "admin:otp_totp_totpdevice_changelist"),
                ],
            },
        ],
    },
}

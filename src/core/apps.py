from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Socle commun : aucune dépendance vers les modules métier (voir .claude/CLAUDE.md)."""

    name = "core"
    default_auto_field = "django.db.models.BigAutoField"

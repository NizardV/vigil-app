from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Socle commun : aucune dépendance vers les modules métier (voir .claude/CLAUDE.md)."""

    name = "core"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from . import two_factor  # noqa: F401 - enregistre l'alerte de blocage (signal django-axes)

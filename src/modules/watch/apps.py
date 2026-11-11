from django.apps import AppConfig


class WatchConfig(AppConfig):
    name = "modules.watch"
    verbose_name = "Veille"
    label = "watch"
    default_auto_field = "django.db.models.BigAutoField"

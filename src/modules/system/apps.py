from django.apps import AppConfig


class SystemConfig(AppConfig):
    name = "modules.system"
    verbose_name = "Système"
    label = "system"
    default_auto_field = "django.db.models.BigAutoField"

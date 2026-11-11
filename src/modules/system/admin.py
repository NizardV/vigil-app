from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import SentAlert, SiteStatus


@admin.register(SentAlert)
class SentAlertAdmin(ModelAdmin):
    list_display = ("key", "kind", "sent_at")
    list_filter = ("kind",)
    search_fields = ("key",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SiteStatus)
class SiteStatusAdmin(ModelAdmin):
    list_display = ("name", "etat", "url", "consecutive_failures", "last_error", "last_checked_at", "changed_at")

    @display(description="État", label={True: "success", False: "danger"})
    def etat(self, obj: SiteStatus) -> tuple:
        return obj.is_up, "En ligne" if obj.is_up else "Hors ligne"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

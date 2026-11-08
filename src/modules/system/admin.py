from django.contrib import admin

from .models import SentAlert


@admin.register(SentAlert)
class SentAlertAdmin(admin.ModelAdmin):
    list_display = ("key", "kind", "sent_at")
    list_filter = ("kind",)
    search_fields = ("key",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

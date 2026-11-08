from django.contrib import admin

from .models import CheckResult, Finding


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("severity", "target", "check_name", "title", "status", "last_seen")
    list_display_links = ("title",)
    list_filter = ("status", "severity", "source", "check_name")
    search_fields = ("target", "title")
    date_hierarchy = "last_seen"
    # Les audits écrivent tout ; seul le statut (ex. « ignoré ») et la note se modifient à la main.
    readonly_fields = (
        "fingerprint", "source", "target", "check_name", "severity", "title", "detail", "url",
        "first_seen", "last_seen", "resolved_at", "notified_at",
    )  # fmt: skip

    def has_add_permission(self, request):
        return False


@admin.register(CheckResult)
class CheckResultAdmin(admin.ModelAdmin):
    """Couverture : ce qui a été vérifié, et surtout ce qui n'a *pas* pu l'être."""

    list_display = ("target", "check_name", "status", "detail", "checked_at")
    list_filter = ("status", "check_name", "source")
    search_fields = ("target",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

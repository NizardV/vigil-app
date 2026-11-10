from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import CheckResult, Finding

# Couleurs des pastilles (variantes unfold) : gravité, cycle de vie, couverture.
SEVERITY_LABELS = {
    Finding.Severity.CRITICAL: "danger",
    Finding.Severity.HIGH: "warning",
    Finding.Severity.MEDIUM: "info",
    Finding.Severity.LOW: "success",
}
FINDING_STATUS_LABELS = {
    Finding.Status.OPEN: "danger",
    Finding.Status.RESOLVED: "success",
    Finding.Status.IGNORED: "",
}
CHECK_STATUS_LABELS = {
    CheckResult.Status.OK: "success",
    CheckResult.Status.FINDINGS: "warning",
    CheckResult.Status.UNAVAILABLE: "info",
    CheckResult.Status.ERROR: "danger",
}


@admin.register(Finding)
class FindingAdmin(ModelAdmin):
    list_display = ("gravite", "target", "check_name", "title", "statut", "last_seen")
    list_display_links = ("title",)
    list_filter = ("status", "severity", "source", "check_name")
    list_filter_submit = True
    search_fields = ("target", "title")
    date_hierarchy = "last_seen"
    # Les audits écrivent tout ; seul le statut (ex. « ignoré ») et la note se modifient à la main.
    readonly_fields = (
        "fingerprint", "source", "target", "check_name", "severity", "title", "detail", "url",
        "first_seen", "last_seen", "resolved_at", "notified_at",
    )  # fmt: skip

    @display(description="Gravité", ordering="severity", label=SEVERITY_LABELS)
    def gravite(self, obj: Finding) -> tuple:
        return obj.severity, obj.get_severity_display()

    @display(description="Statut", ordering="status", label=FINDING_STATUS_LABELS)
    def statut(self, obj: Finding) -> tuple:
        return obj.status, obj.get_status_display()

    def has_add_permission(self, request):
        return False


@admin.register(CheckResult)
class CheckResultAdmin(ModelAdmin):
    """Couverture : ce qui a été vérifié, et surtout ce qui n'a *pas* pu l'être."""

    list_display = ("target", "check_name", "statut", "detail", "checked_at")
    list_filter = ("status", "check_name", "source")
    list_filter_submit = True
    search_fields = ("target",)

    @display(description="Statut", ordering="status", label=CHECK_STATUS_LABELS)
    def statut(self, obj: CheckResult) -> tuple:
        return obj.status, obj.get_status_display()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

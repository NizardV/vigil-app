from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import TrackedRepo
from .services import run_sync

VERDICT_LABELS = {
    TrackedRepo.Verdict.ACTIF: "success",
    TrackedRepo.Verdict.A_RELANCER: "warning",
    TrackedRepo.Verdict.A_ARCHIVER: "",
}


@admin.register(TrackedRepo)
class TrackedRepoAdmin(ModelAdmin):
    list_display = ("full_name", "language", "open_issues", "pushed_at", "verdict", "is_private")
    list_filter = ("language", "is_private", "is_archived")
    list_filter_submit = True
    search_fields = ("full_name", "description")
    readonly_fields = ("synced_at",)
    actions = ["synchroniser"]

    @display(description="Verdict", label=VERDICT_LABELS)
    def verdict(self, obj: TrackedRepo) -> tuple:
        verdict = TrackedRepo.Verdict(obj.verdict)
        return verdict.value, verdict.label

    @admin.action(description="Synchroniser avec GitHub")
    def synchroniser(self, request, queryset):
        count = run_sync()
        self.message_user(request, f"{count} repos synchronisés.")

from django.contrib import admin

from .models import TrackedRepo
from .services import run_sync


@admin.register(TrackedRepo)
class TrackedRepoAdmin(admin.ModelAdmin):
    list_display = ("full_name", "language", "open_issues", "pushed_at", "verdict", "is_private")
    list_filter = ("language", "is_private", "is_archived")
    search_fields = ("full_name", "description")
    readonly_fields = ("synced_at",)
    actions = ["synchroniser"]

    @admin.display(description="Verdict")
    def verdict(self, obj: TrackedRepo) -> str:
        return obj.verdict

    @admin.action(description="Synchroniser avec GitHub")
    def synchroniser(self, request, queryset):
        count = run_sync()
        self.message_user(request, f"{count} repos synchronisés.")

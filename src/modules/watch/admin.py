from django.contrib import admin, messages
from django.db.models import FloatField, OuterRef, Subquery

from core.netfetch import FetchError
from core.notifications import webhook_url

from . import feeds
from .models import Analysis, Article, Digest, Feedback, Source, Theme


class SourceInline(admin.TabularInline):
    model = Source
    extra = 0
    fields = (
        "name",
        "url",
        "kind",
        "active",
        "fetch_interval_hours",
        "last_fetched_at",
        "failures",
        "last_error",
    )
    readonly_fields = ("kind", "last_fetched_at", "failures", "last_error")


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ("name", "digest_days", "digest_hour", "min_score", "livraison", "last_digest_on", "digest_enabled")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [SourceInline]

    @admin.display(description="Livraison Discord")
    def livraison(self, obj: Theme) -> str:
        if obj.discord_channel_id:
            return "bot (boutons 👍/👎)"
        return "webhook" if webhook_url(f"watch-{obj.slug}") else "⚠️ non configuré"


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("__str__", "theme", "kind", "active", "last_fetched_at", "failures", "last_error")
    list_filter = ("theme", "kind", "active")
    search_fields = ("name", "url")
    readonly_fields = ("kind", "feed_url", "last_fetched_at", "failures", "last_error")
    actions = ["collecter", "reinitialiser"]

    @admin.action(description="Collecter maintenant")
    def collecter(self, request, queryset):
        for source in queryset:
            try:
                created = feeds.fetch_source(source)
                self.message_user(request, f"{source} : {created} nouvel(s) article(s).")
            except (FetchError, ValueError, OSError) as exc:
                self.message_user(request, f"{source} : échec — {exc}", level=messages.ERROR)

    @admin.action(description="Réactiver (remettre les échecs à zéro)")
    def reinitialiser(self, request, queryset):
        queryset.update(failures=0, last_error="", active=True)


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "theme", "score", "processed", "analysis_failed", "digested_at", "fetched_at")
    list_filter = ("source__theme", "processed", "analysis_failed")
    search_fields = ("title", "url")
    date_hierarchy = "fetched_at"
    readonly_fields = [f.name for f in Article._meta.fields]
    actions = ["relancer_analyse"]

    def get_queryset(self, request):
        score = Analysis.objects.filter(article=OuterRef("pk")).values("relevance_score")[:1]
        queryset = super().get_queryset(request).select_related("source__theme")
        return queryset.annotate(_score=Subquery(score, output_field=FloatField()))

    @admin.display(description="Thème", ordering="source__theme__name")
    def theme(self, obj):
        return obj.source.theme

    @admin.display(description="Score", ordering="_score")
    def score(self, obj):
        return "" if obj._score is None else f"{obj._score:.1f}"

    @admin.action(description="Relancer l'analyse")
    def relancer_analyse(self, request, queryset):
        queryset.update(processed=False, analysis_failed=False, attempts=0)

    def has_add_permission(self, request):
        return False


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("article", "rating", "author", "origin", "created_at")
    list_filter = ("rating", "origin")


@admin.register(Digest)
class DigestAdmin(admin.ModelAdmin):
    list_display = ("theme", "sent_at", "article_count")
    readonly_fields = [f.name for f in Digest._meta.fields]

    def has_add_permission(self, request):
        return False

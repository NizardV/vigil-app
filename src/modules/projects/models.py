from django.db import models


class TrackedRepo(models.Model):
    class Verdict(models.TextChoices):
        ACTIF = "actif", "Actif"
        A_RELANCER = "a_relancer", "À relancer"
        A_ARCHIVER = "a_archiver", "À archiver"

    full_name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    language = models.CharField(max_length=50, blank=True)
    is_private = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    open_issues = models.PositiveIntegerField(default=0)
    pushed_at = models.DateTimeField(null=True, blank=True)
    notion_page_url = models.URLField(blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-pushed_at"]

    def __str__(self) -> str:
        return self.full_name

    @property
    def verdict(self) -> str:
        from .services import compute_verdict

        return compute_verdict(self)

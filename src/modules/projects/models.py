from django.db import models


class TrackedRepo(models.Model):
    class Verdict(models.TextChoices):
        ACTIF = "actif", "Actif"
        A_RELANCER = "a_relancer", "À relancer"
        A_ARCHIVER = "a_archiver", "À archiver"

    full_name = models.CharField(verbose_name="dépôt", max_length=200, unique=True)
    description = models.TextField(verbose_name="description", blank=True)
    language = models.CharField(verbose_name="langage", max_length=50, blank=True)
    is_private = models.BooleanField(verbose_name="privé", default=False)
    is_archived = models.BooleanField(verbose_name="archivé", default=False)
    open_issues = models.PositiveIntegerField(verbose_name="issues ouvertes", default=0)
    pushed_at = models.DateTimeField(verbose_name="dernier push", null=True, blank=True)
    notion_page_url = models.URLField(verbose_name="page Notion", blank=True)
    synced_at = models.DateTimeField(verbose_name="synchronisé le", auto_now=True)

    class Meta:
        verbose_name = "dépôt suivi"
        verbose_name_plural = "dépôts suivis"
        ordering = ["-pushed_at"]

    def __str__(self) -> str:
        return self.full_name

    @property
    def verdict(self) -> str:
        from .services import compute_verdict

        return compute_verdict(self)

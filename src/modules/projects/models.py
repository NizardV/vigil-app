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


class PullRequestMessage(models.Model):
    """Message Discord (#projets-pr) qui suit une PR en direct : modifié à chaque changement d'état."""

    repo = models.CharField(verbose_name="dépôt", max_length=200)
    number = models.PositiveIntegerField(verbose_name="numéro")
    message_id = models.CharField(verbose_name="message Discord", max_length=30)
    fingerprint = models.CharField(
        verbose_name="empreinte du contenu", max_length=64
    )  # évite les modifications inutiles
    finished = models.BooleanField(verbose_name="terminé", default=False)  # fusionnée/fermée et plus rien en cours
    updated_at = models.DateTimeField(verbose_name="mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "suivi de PR"
        verbose_name_plural = "suivi des PR"
        constraints = [models.UniqueConstraint(fields=["repo", "number"], name="projects_pr_message_unique")]

    def __str__(self) -> str:
        return f"{self.repo}#{self.number}"

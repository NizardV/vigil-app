from django.db import models
from django.utils import timezone


class SentAlert(models.Model):
    """Problème système déjà signalé : garantit qu'un même problème n'est notifié qu'une fois."""

    key = models.CharField(
        max_length=200, unique=True
    )  # ex. « failed:42 », « overdue:security.audit_github:2026110805 »
    kind = models.CharField(max_length=20)
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self) -> str:
        return self.key


class SiteStatus(models.Model):
    """État d'un site surveillé (VIGIL_UPTIME_SITES) : sert à n'alerter qu'aux changements (panne, retour)."""

    name = models.CharField("site", max_length=100, unique=True)
    url = models.URLField(max_length=300)
    is_up = models.BooleanField("en ligne", default=True)
    consecutive_failures = models.PositiveSmallIntegerField("échecs d'affilée", default=0)
    last_error = models.CharField("dernière erreur", max_length=300, blank=True)
    last_checked_at = models.DateTimeField("dernière vérification", null=True, blank=True)
    changed_at = models.DateTimeField("depuis", default=timezone.now)  # dernier passage en ligne / hors ligne

    class Meta:
        ordering = ["name"]
        verbose_name = "site surveillé"
        verbose_name_plural = "sites surveillés"

    def __str__(self) -> str:
        return f"{self.name} ({'en ligne' if self.is_up else 'hors ligne'})"

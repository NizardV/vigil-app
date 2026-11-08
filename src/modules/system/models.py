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

from django.db import models
from django.utils import timezone


class Finding(models.Model):
    """Un constat de sécurité, suivi dans le temps. Créé et mis à jour par les audits uniquement."""

    class Severity(models.IntegerChoices):
        INFO = 0, "Info"
        LOW = 1, "Faible"
        MEDIUM = 2, "Moyenne"
        HIGH = 3, "Haute"
        CRITICAL = 4, "Critique"

    class Status(models.TextChoices):
        OPEN = "open", "Ouvert"
        RESOLVED = "resolved", "Résolu"
        IGNORED = "ignored", "Ignoré"

    fingerprint = models.CharField(verbose_name="empreinte", max_length=64, unique=True)
    source = models.CharField(verbose_name="source", max_length=30, db_index=True)
    target = models.CharField(verbose_name="cible", max_length=200, db_index=True)
    check_name = models.CharField(verbose_name="contrôle", max_length=50)
    severity = models.PositiveSmallIntegerField(verbose_name="gravité", choices=Severity, db_index=True)
    title = models.CharField(verbose_name="titre", max_length=300)
    detail = models.JSONField(verbose_name="détail", default=dict, blank=True)
    url = models.URLField(verbose_name="lien", max_length=500, blank=True)
    status = models.CharField(verbose_name="statut", max_length=10, choices=Status, default=Status.OPEN, db_index=True)
    note = models.TextField(verbose_name="note", blank=True)
    first_seen = models.DateTimeField(verbose_name="vu la première fois", default=timezone.now)
    last_seen = models.DateTimeField(verbose_name="vu la dernière fois", default=timezone.now)
    resolved_at = models.DateTimeField(verbose_name="résolu le", null=True, blank=True)
    # Dernière notification envoyée pour ce constat ; remis à None s'il se rouvre ou s'aggrave.
    notified_at = models.DateTimeField(verbose_name="notifié le", null=True, blank=True)

    class Meta:
        verbose_name = "constat"
        verbose_name_plural = "constats"
        ordering = ["-severity", "target", "title"]

    def __str__(self) -> str:
        return f"[{self.get_severity_display()}] {self.target} — {self.title}"


class CheckResult(models.Model):
    """Dernier état de chaque contrôle sur chaque cible : c'est la *couverture* de l'audit."""

    class Status(models.TextChoices):
        OK = "ok", "OK"
        FINDINGS = "findings", "Constats"
        UNAVAILABLE = "unavailable", "Indisponible"
        ERROR = "error", "Erreur"

    source = models.CharField(verbose_name="source", max_length=30)
    target = models.CharField(verbose_name="cible", max_length=200)
    check_name = models.CharField(verbose_name="contrôle", max_length=50)
    status = models.CharField(verbose_name="statut", max_length=15, choices=Status)
    detail = models.CharField(verbose_name="détail", max_length=300, blank=True)
    checked_at = models.DateTimeField(verbose_name="vérifié le", default=timezone.now)

    class Meta:
        verbose_name = "résultat de contrôle"
        verbose_name_plural = "couverture des contrôles"
        constraints = [
            models.UniqueConstraint(fields=["source", "target", "check_name"], name="uniq_check_per_target"),
        ]
        ordering = ["target", "check_name"]

    def __str__(self) -> str:
        return f"{self.target} / {self.check_name}: {self.status}"

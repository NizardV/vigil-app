from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


def all_days() -> list[int]:
    """Par défaut, un résumé chaque jour (0 = lundi … 6 = dimanche)."""
    return list(range(7))


class Theme(models.Model):
    """Un sujet de veille (ex. « IA », « Sécurité ») : ses sources, son score minimal et son salon Discord."""

    name = models.CharField(verbose_name="nom", max_length=100, unique=True)
    slug = models.SlugField(
        max_length=60, unique=True, help_text="Salon Discord : variable DISCORD_WEBHOOK_WATCH_<SLUG>"
    )
    description = models.TextField(verbose_name="description", blank=True)
    keywords = ArrayField(models.CharField(max_length=60), default=list, blank=True)
    digest_enabled = models.BooleanField(verbose_name="résumé actif", default=True)
    digest_hour = models.PositiveSmallIntegerField(
        verbose_name="heure du résumé",
        default=7,
        validators=[MaxValueValidator(23)],
        help_text="Heure d'envoi du résumé, heure de Paris",
    )
    digest_days = ArrayField(
        models.PositiveSmallIntegerField(validators=[MaxValueValidator(6)]),
        default=all_days,
        help_text="Jours d'envoi : 0 = lundi … 6 = dimanche (ex. [0] = chaque lundi, [0, 3] = lundi et jeudi)",
    )
    lookback_days = models.PositiveSmallIntegerField(
        verbose_name="jours couverts",
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="Ancienneté maximale (jours) des articles repris dans un résumé ; ~7 pour un résumé hebdomadaire",
    )
    min_score = models.FloatField(
        verbose_name="score minimal", default=5.0, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    discord_channel_id = models.CharField(
        verbose_name="salon Discord",
        max_length=25,
        blank=True,
        help_text="Identifiant du salon : nécessaire pour les boutons 👍/👎 (via le bot)",
    )
    last_digest_on = models.DateField(verbose_name="dernier résumé", null=True, blank=True)
    created_at = models.DateTimeField(verbose_name="créé le", default=timezone.now)

    class Meta:
        verbose_name = "thème"
        verbose_name_plural = "thèmes"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Source(models.Model):
    class Kind(models.TextChoices):
        RSS = "rss", "RSS / Atom"
        YOUTUBE = "youtube", "YouTube"
        REDDIT = "reddit", "Reddit"
        GITHUB = "github", "Releases GitHub"
        HACKERNEWS = "hackernews", "Hacker News"

    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name="sources", verbose_name="thème")
    name = models.CharField(verbose_name="nom", max_length=100, blank=True)
    url = models.CharField(
        verbose_name="URL", max_length=500, help_text="URL saisie (flux RSS, chaîne YouTube, subreddit, dépôt GitHub…)"
    )
    feed_url = models.CharField(
        verbose_name="URL du flux", max_length=700, blank=True, help_text="URL du flux, déduite automatiquement"
    )
    kind = models.CharField(verbose_name="type", max_length=20, choices=Kind, default=Kind.RSS)
    active = models.BooleanField(verbose_name="active", default=True)
    fetch_interval_hours = models.PositiveSmallIntegerField(
        verbose_name="intervalle (h)", default=6, validators=[MinValueValidator(1), MaxValueValidator(168)]
    )
    last_fetched_at = models.DateTimeField(verbose_name="dernière collecte", null=True, blank=True)
    last_error = models.CharField(verbose_name="dernière erreur", max_length=300, blank=True)
    failures = models.PositiveSmallIntegerField(
        verbose_name="échecs", default=0, help_text="Échecs consécutifs ; la source est suspendue à 10"
    )

    class Meta:
        verbose_name = "source"
        verbose_name_plural = "sources"
        ordering = ["theme__name", "name"]

    def __str__(self) -> str:
        return self.name or self.url


class Article(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="articles", verbose_name="source")
    title = models.CharField(verbose_name="titre", max_length=500)
    url = models.CharField(verbose_name="URL", max_length=1000, unique=True)
    content_raw = models.TextField(verbose_name="contenu brut", blank=True)
    published_at = models.DateTimeField(verbose_name="publié le", null=True, blank=True)
    fetched_at = models.DateTimeField(verbose_name="collecté le", default=timezone.now, db_index=True)
    processed = models.BooleanField(verbose_name="analysé", default=False)
    attempts = models.PositiveSmallIntegerField(verbose_name="tentatives", default=0)
    analysis_failed = models.BooleanField(
        verbose_name="analyse abandonnée", default=False, help_text="Abandonné après plusieurs échecs d'analyse"
    )
    digested_at = models.DateTimeField(
        verbose_name="envoyé le", null=True, blank=True, help_text="Date d'envoi dans un résumé : jamais renvoyé"
    )

    class Meta:
        verbose_name = "article"
        verbose_name_plural = "articles"
        ordering = ["-fetched_at"]
        indexes = [models.Index(fields=["processed", "analysis_failed"], name="watch_article_pending")]

    def __str__(self) -> str:
        return self.title[:80]


class Analysis(models.Model):
    article = models.OneToOneField(Article, on_delete=models.CASCADE, related_name="analysis", verbose_name="article")
    summary = models.TextField(verbose_name="résumé", blank=True)
    key_points = ArrayField(models.TextField(), default=list, blank=True)
    relevance_score = models.FloatField(
        verbose_name="score de pertinence", validators=[MinValueValidator(1), MaxValueValidator(10)]
    )
    theme_match = models.CharField(verbose_name="thème détecté", max_length=100, blank=True)
    prompt_version = models.CharField(verbose_name="version du prompt", max_length=20, blank=True)
    model = models.CharField(verbose_name="modèle", max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "analyse"
        verbose_name_plural = "analyses"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(relevance_score__gte=1) & models.Q(relevance_score__lte=10),
                name="watch_analysis_score_1_10",
            )
        ]

    def __str__(self) -> str:
        return f"{self.relevance_score:.1f} — {self.article}"


class Feedback(models.Model):
    """Un 👍/👎 sur un article. Un seul retour par auteur et par article (le dernier remplace le précédent)."""

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="feedbacks")
    rating = models.SmallIntegerField(choices=[(1, "👍 pertinent"), (-1, "👎 hors sujet")])
    author = models.CharField(max_length=100, default="admin")
    origin = models.CharField(max_length=20, default="discord")
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "retour"
        verbose_name_plural = "retours"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["article", "author"], name="watch_one_feedback_per_author"),
            models.CheckConstraint(condition=models.Q(rating__in=[-1, 1]), name="watch_feedback_rating"),
        ]

    def __str__(self) -> str:
        return f"{self.rating:+d} {self.article}"


class Digest(models.Model):
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name="digests")
    content = models.TextField(blank=True)
    article_count = models.PositiveSmallIntegerField(default=0)
    channel = models.CharField(max_length=50, default="discord")
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "résumé"
        verbose_name_plural = "résumés"
        ordering = ["-sent_at"]

    def __str__(self) -> str:
        return f"{self.theme} — {self.sent_at:%d/%m/%Y}"

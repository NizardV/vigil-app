"""Réglages Vigil. Tout ce qui varie selon l'environnement vient des variables d'environnement."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes"}


DEBUG = env_bool("DJANGO_DEBUG", False)

# Aucune valeur par defaut en production : un SECRET_KEY manquant doit faire echouer le demarrage.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or ("dev-only-insecure-key" if DEBUG else None)
if SECRET_KEY is None:
    raise RuntimeError("DJANGO_SECRET_KEY est obligatoire quand DJANGO_DEBUG est faux.")

ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # ArrayField et recherche plein texte (module watch)
    # Socle puis modules metier
    "procrastinate.contrib.django",
    "core",
    "modules.projects",
    "modules.security",
    "modules.system",
    "modules.watch",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # fichiers statiques de l'admin, sans serveur dédié
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# PostgreSQL partout (dev, tests, prod) : Procrastinate (ADR 0003) et pgvector l'exigent, et un seul
# moteur evite les divergences entre environnements. En dev : voir infra/dev/postgres.py.
if not os.environ.get("POSTGRES_DB"):
    raise RuntimeError(
        "POSTGRES_DB n'est pas defini. En dev, lance infra/dev/postgres.py puis charge .pgdev.env "
        "(voir .claude/CLAUDE.md, section Commandes)."
    )

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ.get("POSTGRES_USER", ""),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/admin/login/"

# Durcissement : actif des que DEBUG est faux (verifie par `manage.py check --deploy`).
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^health/$"]  # la sonde Docker interroge en HTTP, derrière NGINX
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Module projects ---
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "NizardV")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")  # optionnel : sans jeton, 60 requetes/h, repos publics

# --- Taches de fond (Procrastinate, ADR 0003) : PostgreSQL requis, y compris en dev ---
# Les taches sont decouvertes dans le fichier tasks.py de chaque app installee.
PROCRASTINATE_ON_APP_READY = None

# --- Webhooks Discord, un par salon (URL = secret). Variable DISCORD_WEBHOOK_<NOM> -> salon "<nom>" ---
# Ex. DISCORD_WEBHOOK_SECURITY_ALERTS -> "security-alerts". Ajouter un salon = ajouter une variable.
DISCORD_WEBHOOKS = {
    key.removeprefix("DISCORD_WEBHOOK_").lower().replace("_", "-"): value
    for key, value in os.environ.items()
    if key.startswith("DISCORD_WEBHOOK_") and value
}

# --- Module projects : bruit à écarter du résumé d'activité ---
# Un commit dont le message contient l'un de ces motifs (sans casse) n'est pas de l'activité humaine.
# (Le dépôt de profil publie chaque jour un « Deploy to GitHub pages » sur sa branche générée.)
PROJECTS_IGNORED_COMMIT_PATTERNS = ["deploy to github pages"]

# --- Module system : signal de vie vers un service EXTERNE (ex. healthchecks.io, Uptime Kuma). Optionnel. ---
# Vigil l'appelle après chaque vérification de santé réussie ; s'il se tait, c'est le service externe
# qui alerte
# (le salon Discord #système ne peut pas signaler que le worker qui l'alimente est lui-même arrêté).
# L'URL contient souvent un identifiant secret : variable d'environnement uniquement.
VIGIL_HEARTBEAT_URL = os.environ.get("VIGIL_HEARTBEAT_URL", "")

# --- Notion (synchro du module security). Jeton = SECRET : variable d'environnement uniquement. ---
# Créer une intégration interne sur notion.so/my-integrations, puis PARTAGER la page cible avec elle.
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_SECURITY_PAGE_ID = os.environ.get("NOTION_SECURITY_PAGE_ID", "")  # sous-page « Tableau de bord sécurité »

# --- Audit du VPS (module security) : SSH en LECTURE SEULE, clé restreinte (voir infra/audit/README.md) ---
VPS_AUDIT_HOST = os.environ.get("VPS_AUDIT_HOST", "")
VPS_AUDIT_USER = os.environ.get("VPS_AUDIT_USER", "vigil-audit")
VPS_AUDIT_PORT = int(os.environ.get("VPS_AUDIT_PORT", "22"))
VPS_AUDIT_SSH_KEY = os.environ.get(
    "VPS_AUDIT_SSH_KEY", ""
)  # CHEMIN d'une clé privée dédiée (jamais la clé de déploiement)
VPS_AUDIT_KNOWN_HOSTS = os.environ.get(
    "VPS_AUDIT_KNOWN_HOSTS", ""
)  # empreinte du serveur vérifiée : pas de confiance aveugle
VPS_AUDIT_TARGET = os.environ.get("VPS_AUDIT_TARGET", "")  # nom affiché (défaut : l'hôte)
VPS_AUDIT_PUBLIC_PORTS = [
    int(p) for p in os.environ.get("VPS_AUDIT_PUBLIC_PORTS", "22,80,443").split(",") if p.strip().isdigit()
]  # ports autorisés sur toutes les interfaces ; tout autre port ouvert est signalé

# --- Audit du code par SonarCloud (module security) : clés de projet séparées par des virgules, lecture publique ---
SONARCLOUD_PROJECTS = [
    p.strip() for p in os.environ.get("SONARCLOUD_PROJECTS", "NizardV_vigil-app").split(",") if p.strip()
]
SONARCLOUD_TOKEN = os.environ.get("SONARCLOUD_TOKEN", "")  # optionnel : projet privé uniquement (SECRET)

# --- Module watch : veille RSS analysée par Gemini, résumés Discord avec boutons 👍/👎 ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")  # SECRET
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# Plafond d'analyses par passage (toutes les 20 min) : borne le coût si une source se met à publier en masse.
WATCH_MAX_ANALYSES_PER_RUN = int(os.environ.get("WATCH_MAX_ANALYSES_PER_RUN", "30"))
# Bot Discord : nécessaire aux boutons 👍/👎. Le jeton est un SECRET ; la clé publique vérifie la signature des clics.
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_PUBLIC_KEY = os.environ.get("DISCORD_PUBLIC_KEY", "")

# Les tests ne doivent JAMAIS toucher le réseau (voir core/test_runner.py)
TEST_RUNNER = "core.test_runner.OfflineRunner"

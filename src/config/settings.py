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
    "unfold",  # thème de l'admin (ADR 0006) : doit précéder django.contrib.admin
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # ArrayField et recherche plein texte (module watch)
    # Authentification renforcée (ADR 0007) : double authentification TOTP, blocage après échecs
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    "django_otp_webauthn",  # passkeys, en second facteur (ADR 0007)
    "axes",
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
    "django_otp.middleware.OTPMiddleware",  # request.user.is_verified()
    "core.two_factor.Require2FAMiddleware",  # second facteur obligatoire pour toute session connectée
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",  # doit rester en dernier
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "config" / "templates"],  # surcharges de l'admin (prioritaires sur celles d'unfold)
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
# Après connexion sans « next » : l'admin (le formulaire de connexion d'unfold ne transmet pas de « next » par défaut).
LOGIN_REDIRECT_URL = "admin:index"

# --- Authentification renforcée (ADR 0007) ---
# Double authentification obligatoire pour toute session connectée (core/two_factor.py). Désactivable en dev local.
TWO_FACTOR_REQUIRED = env_bool("VIGIL_TWO_FACTOR_REQUIRED", True)
OTP_TOTP_ISSUER = "Vigil"
# Passkeys : second facteur au même titre que le code TOTP, jamais à la place du mot de passe.
OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN = False
OTP_WEBAUTHN_RP_NAME = "Vigil"
# Domaine des passkeys : le premier hôte autorisé (vigil.nizard.dev en prod) ; une passkey ne sert que sur ce domaine.
OTP_WEBAUTHN_RP_ID = os.environ.get("VIGIL_WEBAUTHN_RP_ID") or (ALLOWED_HOSTS[0] if ALLOWED_HOSTS else "localhost")
OTP_WEBAUTHN_ALLOWED_ORIGINS = [
    o for o in os.environ.get("VIGIL_WEBAUTHN_ORIGINS", f"https://{OTP_WEBAUTHN_RP_ID}").split(",") if o
]
# Blocage après échecs de connexion (django-axes) : 5 échecs depuis une même IP -> 1 h de blocage + alerte Discord.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",  # doit rester en premier
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # heures
AXES_LOCKOUT_PARAMETERS = ["ip_address"]  # par IP : un tiers ne peut pas bloquer le compte depuis ailleurs
AXES_RESET_ON_SUCCESS = True
AXES_CLIENT_IP_CALLABLE = "core.two_factor.client_ip"  # IP réelle derrière NGINX (dernière entrée X-Forwarded-For)
AXES_SENSITIVE_PARAMETERS = ["password", "token"]
# Session : 12 h maximum, puis reconnexion complète (mot de passe + second facteur).
SESSION_COOKIE_AGE = 12 * 3600

# --- Admin : thème django-unfold (ADR 0006) ; tableau de bord dans config/admin_dashboard.py ---
from config.admin_theme import UNFOLD  # noqa: E402, F401

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

# --- Module system : sites surveillés (disponibilité toutes les 5 min, alerte #système) ---
# Format : « Nom=https://url,Nom=https://url ». Vide = aucune surveillance. Vigil lui-même est couvert par le signal
# de vie externe (VIGIL_HEARTBEAT_URL), pas par cette liste.
_DEFAULT_UPTIME_SITES = (
    "Portfolio=https://nizard.dev,Cyna (site)=https://cyna.nizard.dev,Cyna (API)=https://api.cyna.nizard.dev/health"
)
VIGIL_UPTIME_SITES = [
    tuple(item.split("=", 1))
    for item in os.environ.get("VIGIL_UPTIME_SITES", _DEFAULT_UPTIME_SITES).split(",")
    if "=" in item
]

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

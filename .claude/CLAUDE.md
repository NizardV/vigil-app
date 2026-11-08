# Vigil — contexte projet

Plateforme perso d'automatisation et de veille : suivi des projets GitHub → Notion, veille RSS avec
scoring LLM (Gemini), notifications Discord. Un seul développeur, un seul VPS OVH.
Priorités : **stabilité, maintenabilité, sécurité**.

> Ce fichier est la source de vérité pour Claude. Si le code le contredit, corrige le fichier (ou
> signale-le), ne devine pas.

## Décisions d'architecture

- [ADR 0001](../docs/adr/0001-monolithe-modulaire-monorepo.md) — monolithe modulaire, monorepo
- [ADR 0002](../docs/adr/0002-django-comme-framework.md) — **Django** (remplace FastAPI + Streamlit)
- [ADR 0003](../docs/adr/0003-procrastinate-pour-les-taches-de-fond.md) — **Procrastinate** (tâches de fond, PostgreSQL requis)
- [ADR 0004](../docs/adr/0004-frontend-progressif-admin-puis-sveltekit-spa.md) — admin Django, puis **SvelteKit en SPA** statique

Plan et état de la migration : `docs/architecture.md`.

## Structure

```
src/
  manage.py
  config/            réglages Django (tout vient des variables d'environnement)
  core/              socle commun : santé, auth, notifications, LLM, événements — ne dépend d'aucun module
  modules/
    projects/        suivi GitHub → Notion        (existe, portée depuis le prototype)
    system/          santé de Vigil : tâches en échec/bloquées/en retard, signal de vie externe (voir specs/system.md)
    watch/           veille RSS analysée par Gemini, résumés Discord avec boutons 👍/👎 (voir specs/watch.md)
    security/        audits de sécurité (GitHub + VPS ; voir specs/security.md et infra/audit/README.md) (GitHub : fait ; infra/VPS, images, dépendances : à faire) — voir specs/security.md
    <futurs>         jobs, gaming, learn, social
infra/               docker-compose, NGINX, scripts d'audit VPS
docs/                architecture, ADR, runbooks
.claude/             ce contexte, skills, specs
```

## Commandes

Toutes depuis la racine du repo. Python 3.13 géré par `uv`.

```
# PostgreSQL local (obligatoire). Option recommandée : Docker (même image que la prod).
docker compose -f infra/dev/docker-compose.yml up -d
set -a; . infra/dev/dev.env; set +a            # charge POSTGRES_*

# Solution de secours sans Docker (PostgreSQL embarqué, terminal séparé, Ctrl-C pour arrêter) :
#   uv run --no-project --python 3.12 --with pgserver --with tzdata python -I infra/dev/postgres.py
#   set -a; . ./.pgdev.env; set +a              # le port change à chaque démarrage

export DJANGO_DEBUG=1                          # dev ; sans, DJANGO_SECRET_KEY est obligatoire
uv run python src/manage.py procrastinate worker   # exécute les tâches de fond (PostgreSQL requis)
uv run python src/manage.py migrate
uv run python src/manage.py runserver
uv run python src/manage.py test modules core
uv run python src/manage.py check --deploy     # à lancer avec DJANGO_DEBUG=0 pour voir le durcissement
uv run ruff check src && uv run ruff format src
PYTHONPATH=src uv run lint-imports             # frontières entre modules
```

## Règles non négociables

1. **Un module n'importe jamais un autre module** (modèles, vues, services). Il passe par un événement
   ou par une interface publique exposée dans `core`. Vérifié par `import-linter`.
2. **`core/` ne dépend d'aucun module.** Sens des dépendances : `config → modules → core`.
3. **Un module = une app Django** (`modules.<nom>`, label court `<nom>`) avec ses modèles,
   ses migrations, son admin, ses tests. Les tables ont le préfixe d'app (`projects_trackedrepo`).
4. **Logique métier dans `services.py`**, jamais dans les vues, l'admin ou les commandes.
5. **Les tâches de fond sont des fonctions de service** enveloppées par Procrastinate (ADR 0003) et par une commande de management ; elles doivent être **idempotentes**.
6. **Aucun secret dans le repo.** Variables d'environnement uniquement ; `.env.example` à jour.
7. **Ne désactive pas un contrôle (ruff, import-linter, `check --deploy`) pour faire passer un
   changement** : corrige la cause, ou écris une ADR.
8. Tout nouveau module est déclaré dans `INSTALLED_APPS` **et** dans le contrat « independence »
   de `pyproject.toml` (`[tool.importlinter]`).

## Stack

Django 6.1 · Python 3.13 · PostgreSQL 16 (sans pgvector, ADR 0005) partout (dev, tests, prod) ·
`uv` · ruff · import-linter · Docker Compose + NGINX · Gemini · Discord · Notion API · GitHub API.

## Conventions

- Typage sur les fonctions publiques ; `ruff` fait foi pour le style.
- Commits en Conventional Commits (`feat:`, `fix:`, `chore:`…) .
- Branches `feat/*`, `fix/*` → PR vers `main`.
- Les tests tournent sur PostgreSQL (le même moteur qu'en production) ; la CI devra démarrer un service PostgreSQL.

## Sécurité

- `DEBUG` faux par défaut ; un `DJANGO_SECRET_KEY` manquant fait échouer le démarrage (voulu).
- En production : cookies sécurisés, HSTS, redirection HTTPS (déjà dans `config/settings.py`).
- Cible : dashboard non exposé publiquement (Tailscale/WireGuard/Cloudflare Access) ; seuls les
  webhooks entrants restent publics, avec **vérification de signature**.
- Mots de passe : validateurs Django, 12 caractères minimum.

## Déploiement

CI GitHub Actions → image Docker → GHCR → SSH sur le VPS (secrets `VPS_HOST`, `VPS_USER`,
`VPS_SSH_KEY`). Le déploiement actuel vise l'ancien code : **à réécrire pour Django** (étape 5 du
plan de migration). Runbooks dans `docs/runbooks/`.

## Notion

Base « 🧠 Personal OS » : voir le skill `vigil-notion`. Les pages doivent refléter l'état réel du code ;
ne marque pas un projet « Terminé » sans vérifier.

## Skills et specs

- `skills/vigil-module/` — créer un module Django au bon gabarit
- `skills/vigil-notion/` — lire/écrire la base Notion sans casser son schéma
- `docs/discord.md` — structure du serveur Discord, un webhook par salon (`DISCORD_WEBHOOK_<NOM>`)
- `specs/` — une spec par chantier (`audit-vps`, `audit-github`). Lis la spec avant de coder.

## Pièges connus

- **Les tests n'ont pas le droit d'accéder au réseau** (`core/test_runner.py`) : toute connexion sortante les fait échouer.
  Injecter les fonctions d'envoi/téléchargement (`send=`, `fetch=`) ; ne JAMAIS lier un client réseau en valeur par défaut
  d'un paramètre (il ne serait pas remplaçable par `mock.patch`) — résoudre à l'appel.
- Django 6 : `django.utils.timezone.utc` n'existe plus, utiliser `datetime.UTC`.
- Ne nomme pas une méthode de test `client` (masque `self.client` de Django).
- Style : `ruff`, lignes de 120 caractères.

- **Les horaires des tâches planifiées sont en UTC** (Procrastinate), pas à l'heure de Paris : 05:00 UTC = 06:00
  en hiver. Toujours le préciser dans les messages et la documentation.

- `core` est aussi le nom d'un dossier sans `__init__` si on le crée avant `startapp` : crée l'app à la
  main ou via `startapp` sur un dossier inexistant.
- `PYTHONPATH=src` est nécessaire pour `lint-imports`.
- Le PostgreSQL de dev (`pgserver`) n'existe qu'en Python ≤ 3.12 : il tourne dans un environnement isolé
  (`--no-project`), jamais comme dépendance du projet. Son port change à chaque démarrage : toujours
  recharger `.pgdev.env`. Il n'a pas de base de fuseaux horaires : le script la fournit depuis `tzdata`.
- Un chemin d'import relatif au dossier du module (`from projects...`) passe `ruff` et les tests
  s'il n'est pas exécuté : chaque commande de management doit avoir un test qui l'appelle.
- Modèle Gemini : `gemini-2.5-flash` (validé en réel). Ses jetons de « réflexion » comptent dans `maxOutputTokens` :
  toujours passer `thinking_budget=0` pour une sortie JSON, sinon la réponse est coupée.
- Les connexions « épinglées » (`core/netfetch.py`) doivent annoncer ALPN `http/1.1`, sinon certains sites (schneier.com)
  répondent 403.
- Le résumé filtre l'ancienneté sur la date de **publication** (à défaut de collecte) : un flux neuf contient des mois d'archives.
- Déploiement : `Dockerfile` + `infra/prod/` (compose, NGINX, `vigil-deploy.sh`, `backup.sh`) ; CI dans `.github/workflows/`.
  La sonde de santé est `/health/` (exemptée de la redirection HTTPS).

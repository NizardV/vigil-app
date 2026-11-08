# Architecture Vigil

Décisions : [ADR 0001](adr/0001-monolithe-modulaire-monorepo.md) (monolithe modulaire, monorepo) et
[ADR 0002](adr/0002-django-comme-framework.md) (Django), [ADR 0003](adr/0003-procrastinate-pour-les-taches-de-fond.md) (tâches de fond), [ADR 0004](adr/0004-frontend-progressif-admin-puis-sveltekit-spa.md) (frontend).

## Vue d'ensemble

```
  Navigateur ───────► config/ (URLs, réglages)
  Discord / GitHub ─►   │
  (webhooks signés)     ▼
                  ┌───────────────────────────────────────────┐
                  │  modules/projects   modules/watch   …     │  une app Django par domaine
                  │   models · services · admin · commandes   │
                  └──────────────────┬────────────────────────┘
                                     ▼  (jamais l'inverse)
                  core/  santé · auth · notifications · LLM · événements
                                     ▼
                         PostgreSQL 16 (+ pgvector pour watch)
```

Les modules ne s'appellent pas entre eux : ils partagent uniquement `core`. Les contrats
`import-linter` de `pyproject.toml` font échouer le contrôle si une dépendance interdite apparaît.

## État du code

| Élément | État |
|---|---|
| Projet Django (`src/config`, réglages durcis, santé `/health/`, PostgreSQL obligatoire) | fait |
| `core` (sonde de santé, client GitHub partagé) | fait ; notifications, LLM, événements à extraire |
| `modules/projects` (suivi GitHub : modèle, service, admin, commande, page, tests) | porté depuis le prototype ; **Notion et webhooks GitHub à porter** |
| `modules/system` (tâches en échec / bloquées / en retard, alerte Discord, signal de vie externe optionnel) | **fait et testé** |
| Synchro Notion (`core/notion.py` + `modules/security/notion_sync.py`) | **fait et testé avec un faux Notion** ; écriture réelle à valider une fois le jeton créé |
| Audit du VPS (script `infra/audit/vigil-audit.sh`, SSH restreint, `collectors/vps.py`) | **code fait et testé sur des conteneurs ; installation sur le VPS à faire** |
| `modules/security` (audit GitHub : Dependabot, code scanning, secret scanning, protection de branche ; cycle de vie des constats, couverture, tâche quotidienne 05:00) | **MVP fait et vérifié sur les vrais dépôts** ; infra/VPS, Trivy, notifications Discord, Notion à faire |
| `modules/watch` (veille RSS, analyse Gemini, résumés Discord, boutons 👍/👎, import de l'ancienne base) | **fait et testé (359 tests)** ; envoi réel Discord, appels Gemini et import sur la vraie base à valider |
| Authentification | Django (admin) ; 2FA et inscription à décider |
| Frontend | admin Django maintenant ; SvelteKit SPA statique plus tard (ADR 0004) |
| Tâches de fond | **Procrastinate câblé** (ADR 0003) : `projects.sync_repos` quotidienne à 04:00, réessais x3, verrou anti-doublon ; vérifié de bout en bout (job `succeeded`, 0 → 16 repos) |
| PostgreSQL de dev | fait : `infra/dev/postgres.py` (PostgreSQL 16 + pgvector, sans Docker) |
| Docker / CI / NGINX | à réécrire pour Django (la CI doit démarrer un service PostgreSQL) |

## Correspondance avec l'ancien code (`vigil-backend`)

| Ancien | Nouveau |
|---|---|
| `Theme`, `Source`, `Article`, `Analysis`, `Feedback`, `Digest`, `Webhook` | `modules/watch/models.py` |
| `routes/{themes,sources,articles,feedback,digests,webhooks,discord}` | admin + pages de `modules/watch` ; interactions Discord en vue signée |
| `workers/tasks.py` | `modules/watch/services.py` + commandes de management |
| `TrackedRepo`, `RepoEvent`, `services/github.py`, `services/notion.py`, `tasks_projects.py`, `routes/{projects,github_webhooks}` | `modules/projects/` |
| `User`, `RefreshToken`, `EmailVerificationToken`, `routes/{login*,register,auth}` | auth Django (+ 2FA si besoin) |
| `services/{llm,embeddings,webhook,session}.py`, `config.py` | `core/` et `config/settings.py` |
| `streamlit/` | admin Django, puis pages dédiées si nécessaire |

## Points à traiter pendant le portage

- **Données existantes** : les tables actuelles sont dans les schémas `public` et `projects`. Le portage
  doit les renommer vers les tables préfixées d'app, sans perte (migration de données testée sur une
  copie de la base avant tout passage en production).
- **Sécurité héritée à ne pas reproduire** : CORS `*` avec credentials, `create_all` au démarrage,
  secrets lus sans validation.
- **PostgreSQL partout** (dev, tests, prod) : pas de mode SQLite, donc pas de divergence entre environnements.
- **Sauvegardes PostgreSQL** testées et envoyées hors VPS : aucune n'existe aujourd'hui, à créer avant
  la mise en production de la nouvelle version.
- **Observabilité** : logs structurés, métriques, sonde externe (voir spec `audit-vps`).

## Migration depuis les anciens repos

| Étape | Contenu | État |
|---|---|---|
| 0 | Clone neuf, branche `refonte/monorepo`, `.claude`, docs, ADR | fait |
| 1 | Projet Django, réglages durcis, contrôles (ruff, import-linter, tests) | fait |
| 2 | Port de `modules/projects` complet (Notion, webhooks GitHub) | à faire |
| 3 | Extraction de `core` (notifications Discord, LLM) | à faire |
| 4 | Port de `modules/watch` (modèles, scoring, digests) + migration des données | fait ; l'import des anciennes données n'a pas été exécuté (choix : repartir de zéro, ancienne base archivée) |
| 5 | Image Docker unique, compose et NGINX dans `infra/`, CI + déploiement, sauvegardes | fait (`Dockerfile`, `infra/prod/`, `.github/workflows/`) |
| 6 | Bascule en production (2026-11-08), archivage des anciens dépôts | fait (anciens conteneurs arrêtés, à supprimer après un mois) |
| 7 | Suppression de `backend/`, `streamlit/`, `prototypes/` | fait (restent consultables dans l'historique git) |

Chaque étape doit laisser la branche dans un état vérifiable (tests, ruff, import-linter au vert).
La production reste sur l'ancien code jusqu'à l'étape 6.

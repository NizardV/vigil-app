# Vigil

Plateforme personnelle d'automatisation et de veille, déployée sur https://vigil.nizard.dev.

- **Veille** : flux RSS de sources fiables, résumés et notation par Gemini, un résumé Discord par thème
  (Cybersécurité, Juridique, Technologique) chaque lundi à 9h (Paris), avec boutons 👍/👎.
- **Sécurité** : audits des dépôts GitHub et du VPS, alertes Discord dédupliquées, synchro Notion.
- **Projets** : suivi de l'activité des dépôts GitHub.
- **Système** : alerte quand une tâche de Vigil échoue, se bloque ou n'a pas tourné.

Monolithe modulaire Django 6 (Python 3.13) + PostgreSQL 16 + Procrastinate. Interface : l'admin Django.

## Pour commencer

Tout est décrit dans [`.claude/CLAUDE.md`](.claude/CLAUDE.md) (commandes, règles, pièges connus) et
[`docs/architecture.md`](docs/architecture.md). Les décisions sont dans [`docs/adr/`](docs/adr/), les procédures dans
[`docs/runbooks/`](docs/runbooks/), la structure du serveur Discord dans [`docs/discord.md`](docs/discord.md).

Développement local (PowerShell) : `.\infra\dev\manage.ps1 <commande>` démarre la base Docker, charge les variables et
lance `manage.py`.

## Déploiement

Un push sur `main` lance les tests (`.github/workflows/ci.yml`), construit l'image `ghcr.io/nizardv/vigil-app`, puis la
déploie sur le VPS par SSH (clé restreinte à `infra/prod/vigil-deploy.sh`, retour automatique si le site ne répond pas).
Sauvegarde PostgreSQL quotidienne : `infra/prod/backup.sh`.

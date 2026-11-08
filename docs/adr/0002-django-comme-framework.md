# ADR 0002 — Django comme framework applicatif

- Statut : acceptée (2026-11-07)
- Amende : [ADR 0001](0001-monolithe-modulaire-monorepo.md) (schémas Postgres par module, Streamlit, FastAPI)

## Contexte

Priorités du projet : **stabilité, maintenabilité, sécurité**, pour un outil perso porté par une
seule personne sur un seul VPS. L'ancienne stack (FastAPI + Streamlit + Celery + Alembic + auth maison)
obligeait à assembler et maintenir soi-même chaque brique.

Deux prototypes équivalents (module `projects`, synchro des repos GitHub) ont été comparés dans
`prototypes/` : Django (136 lignes écrites, admin générée) et ASP.NET Core (211 lignes, pas d'admin).
Les deux sont viables ; le choix s'est fait sur la quantité de code à maintenir et l'envie de rester
en Python.

## Décision

1. **Django** remplace FastAPI et Streamlit. Un module métier = une **app Django** dans `src/modules/`.
2. **Interface** : admin Django en premier (CRUD, filtres, actions gratuits), puis pages Django
   templates ; **HTMX** pourra être ajouté pour l'interactivité. SvelteKit n'est plus prévu.
3. **Authentification** : système d'auth Django (sessions, CSRF, hash de mots de passe). 2FA via un
   paquet éprouvé (ex. `django-otp`) le jour où c'est nécessaire — pas de re-développement maison.
4. **Migrations** : migrations Django, **une app = ses migrations**. Plus d'Alembic ni de `create_all`.
5. **Tables par module** : préfixe d'app par défaut de Django (`projects_trackedrepo`).
   Les schémas Postgres séparés de l'ADR 0001 sont abandonnés (peu naturels avec l'ORM Django).
6. **Frontières de modules** toujours imposées par `import-linter` (règles du `.claude/CLAUDE.md`).
7. **Tâches de fond** : **Procrastinate** (file dans PostgreSQL), décidé dans l'[ADR 0003](0003-procrastinate-pour-les-taches-de-fond.md).
8. **Base de données** : PostgreSQL 16 (+ pgvector pour `watch`) partout — dev, tests et production (amendé : un mode SQLite était prévu au départ mais Procrastinate, ADR 0003, ne le supporte pas).

## Conséquences

- (+) Moins de code et de dépendances à maintenir ; sécurité par défaut (CSRF, XSS, clickjacking,
  sessions, hash) et mises à jour de sécurité prévisibles.
- (+) L'admin remplace une grande partie du dashboard Streamlit.
- (−) Réécriture du backend existant (~3 400 lignes) : on porte les modèles et la logique, pas le code.
- (−) Migration des données existantes (tables en schéma `public` et schéma `projects`) à planifier.
- (−) PostgreSQL est requis même pour développer et tester : fourni en local par `infra/dev/postgres.py` ; la CI doit démarrer un service PostgreSQL.

## Alternatives écartées

- **Rester sur FastAPI** : beaucoup de briques à assembler et maintenir.
- **ASP.NET Core** : excellent et déjà maîtrisé (API Zabbit), mais plus de code à écrire pour un outil
  perso, et l'envie d'expérimenter en Python était un critère du projet.
- **Go / Elixir / Spring / Laravel / Rails** : aucun avantage décisif pour ce besoin, écosystème de plus.

## Sort des prototypes

(Les prototypes ont été supprimés du dépôt le 2026-11-08 ; ils restent dans l'historique git.)
`prototypes/django-projects` a servi de base au vrai projet (`src/`). `prototypes/dotnet-projects`
est conservé comme trace de la comparaison ; il peut être supprimé sans impact.

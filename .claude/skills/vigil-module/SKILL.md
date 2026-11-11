---
name: vigil-module
description: Crée ou modifie un module Vigil (app Django dans src/modules/<nom>) selon le gabarit du monolithe modulaire. À utiliser dès qu'on ajoute un domaine métier (jobs, gaming, learn, social, watch…) ou qu'on touche à la frontière entre deux modules.
---

# Créer un module Vigil (Django)

## Gabarit

```
src/modules/<nom>/
├── __init__.py
├── apps.py          # name = "modules.<nom>", label = "<nom>"
├── models.py        # tables préfixées automatiquement : <nom>_<modele>
├── services.py      # LOGIQUE MÉTIER — seule interface publique du module
├── admin.py         # interface d'administration (premier écran de gestion)
├── views.py, urls.py, templates/<nom>/   # seulement si une page dédiée est utile
├── management/commands/   # tâches de fond = commandes appelant services.py
├── migrations/
└── tests.py         # ou tests/ si ça grossit
```

Référence vivante : `src/modules/projects/`.

## Étapes

1. Lis `.claude/CLAUDE.md` (règles) et `docs/architecture.md`.
2. Écris la spec dans `.claude/specs/<nom>.md` (objectif, modèles, tâches, événements, critères).
3. Crée le dossier **sans** `startapp` sur un dossier existant :
   `uv run python src/manage.py startapp <nom> src/modules/<nom>` après `mkdir` du dossier, puis
   corrige `apps.py` (`name = "modules.<nom>"`, `label = "<nom>"`).
4. Ajoute `"modules.<nom>"` à `INSTALLED_APPS` (`src/config/settings.py`).
5. Ajoute `"modules.<nom>"` à la liste du contrat « modules independants » dans `pyproject.toml`.
6. Modèles, puis `uv run python src/manage.py makemigrations <nom>`.
7. Logique dans `services.py`, tâches de fond en commandes de management **idempotentes**.
8. Admin : `list_display`, `list_filter`, `search_fields` ; actions pour les opérations manuelles.
9. Tests dans le module (au moins : service nominal, idempotence, accès refusé sans connexion).
10. Contrôles : `check`, `test`, `ruff check`, `PYTHONPATH=src uv run lint-imports` — tout doit passer.
11. Mets à jour `infra/prod/vigil.env.example`, `docs/architecture.md` et la page Notion du projet.

## Communiquer avec un autre module

Interdit : importer ses modèles, services, vues. Autorisé :
- Réagir à un événement publié via l'interface d'événements de `core` (à créer le jour où un second
  module en a besoin — ne l'invente pas avant).
- Exposer un besoin commun dans `core` (ex. envoi Discord, appel LLM) que les deux modules utilisent.

Si une jointure SQL entre deux modules te paraît nécessaire, la frontière est mal placée : écris une ADR
avant de contourner la règle.

## À ne pas faire

- Logique métier dans une vue, l'admin ou une commande.
- Dépendance de `core` vers un module.
- Désactiver un contrôle (`noqa` large, contrat import-linter commenté) pour « faire passer ».
- Secrets ou URL de production en dur.

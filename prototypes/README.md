# Prototypes comparatifs — module « projects »

Même fonctionnalité dans deux frameworks : synchroniser les repos publics non-fork d'un compte
GitHub, les stocker, les afficher (connexion requise) et calculer un verdict d'activité
(actif / à relancer / à archiver, seuil 90 jours). Base SQLite, aucune dépendance externe à lancer.

Variables optionnelles : `GITHUB_OWNER` (défaut `NizardV`), `GITHUB_TOKEN` (sans jeton : repos
publics uniquement, 60 requêtes/h).

## Django (`django-projects/`)

```
cd django-projects
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py sync_repos          # synchro à la demande (cron)
uv run python manage.py runserver           # /admin/ (interface générée) et /projects/
uv run python manage.py test
```

## ASP.NET Core (`dotnet-projects/`)

```
cd dotnet-projects
Admin__User=moi Admin__Password=secret dotnet run   # /login puis /projects ; synchro auto toutes les 24 h
cd ../dotnet-projects.Tests && dotnet test
```

Le prototype .NET applique ses migrations au démarrage. Le compte de connexion .NET est lu dans la
configuration : c'est un raccourci de prototype (en vrai : ASP.NET Identity).

## Limites volontaires

- Pas de Notion, de Discord ni de Gemini : seulement la partie GitHub.
- Django : la planification est une commande à brancher sur cron/worker (non incluse).
- .NET : pas d'interface d'administration, la liste et le login sont écrits à la main.

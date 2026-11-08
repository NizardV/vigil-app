# ADR 0004 — Frontend progressif : admin Django, puis SvelteKit en SPA

- Statut : acceptée (2026-11-07)
- Complète : [ADR 0002](0002-django-comme-framework.md), point 2 (interface)

## Contexte

L'ancien dashboard était en Streamlit. L'objectif est une interface mieux structurée et plus réactive,
sans alourdir l'exploitation (stabilité, sécurité, maintenance solo). SvelteKit est le choix préféré.

## Décision

1. **Maintenant** : l'**admin Django** sert d'interface de gestion pour chaque module. Aucun front dédié
   tant qu'aucun écran riche n'est nécessaire.
2. **Ensuite** : un front **SvelteKit en mode SPA** (`adapter-static`, rendu serveur désactivé), dans
   `web/`. Il est compilé en fichiers statiques **servis par NGINX** : pas de serveur Node en production.
3. **API** : exposée **module par module**, au moment où l'écran correspondant est construit, avec
   `django-ninja` (schéma OpenAPI) ; les types TypeScript du front sont générés depuis ce schéma.
4. **Authentification** : cookie de session Django, front et API sur le **même domaine** (pas de CORS,
   protection CSRF native de Django). Aucun jeton stocké côté navigateur.
5. Pas d'API ni de front « pour tous les modules à l'avance ».

## Raisons

- Le SPA statique donne la réactivité et la structure voulues sans second runtime en production.
- Le contrat OpenAPI → types TypeScript rend les évolutions d'API détectables à la compilation.
- Même domaine + session Django : surface de sécurité minimale, pas de gestion de jetons.

## Conséquences

- (+) Backend et front évoluent indépendamment ; le backend n'est pas bloqué par le front.
- (−) Un second écosystème à maintenir (npm, TypeScript) et ses mises à jour de sécurité : verrouiller les
  dépendances (`package-lock.json`), activer Dependabot et l'audit npm en CI.
- (−) Une API à maintenir en plus des services.
- Réversible : si SvelteKit se révèle trop lourd, les composants Svelte se réutilisent dans un projet
  Svelte + Vite ; et HTMX reste une alternative sans API pour des pages simples.

## Alternatives écartées

- **Django + HTMX seul** : le plus simple, mais moins réactif pour les écrans riches visés.
- **SvelteKit en SSR (serveur Node)** : un second runtime à exploiter pour un gain inutile ici.
- **Garder Streamlit** : faible gestion de session et d'authentification, comportement plus difficile à tester.

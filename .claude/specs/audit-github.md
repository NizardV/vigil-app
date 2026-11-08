# Spec — Audit des dépôts GitHub

Statut : brouillon · S'appuie sur `modules/projects` (Django), déjà porté pour la partie GitHub.

## Objectif

Donner une vue d'ensemble de tous les repos `NizardV` pour décider quoi relancer, archiver ou
nettoyer avant d'ajouter des fonctionnalités, puis maintenir cette vue à jour automatiquement.

## Périmètre

Tous les repos du compte, privés compris, **forks exclus** (ex. `github-readme-stats`).
Repos archivés listés mais non analysés en détail.

## Relevés par repo

- Activité : dernier commit, nombre de commits sur 90 jours, jours d'inactivité.
- PR et issues ouvertes (et âge de la plus ancienne).
- CI : dernier statut du workflow principal.
- Sécurité : alertes Dependabot ouvertes, secret scanning, branches protégées.
- Hygiène : README, licence, description, `.gitignore`, fichiers sensibles évidents.
- Dépendances : PR Dependabot en attente (le repo `vigil` en accumule).

## Classement

Chaque repo reçoit un verdict : `actif`, `à relancer`, `à archiver` ou `à corriger` (sécurité/CI),
avec la raison en une ligne.

## Sorties

1. Rapport Markdown (tableau trié par verdict puis priorité).
2. Mise à jour de la base Notion : colonne `Repo GitHub` rapprochée par nom, statut proposé
   (jamais appliqué sans validation la première fois).
3. Réutilisation par le digest hebdo du module `projects` (via une notification `core`).

## Implémentation

- Réutiliser `modules/projects/services.py` (client GitHub) plutôt que de dupliquer l'accès API.
- Nouvelle fonction de service `audit_all_repos` + commande de management `audit_repos` (planification : voir ADR sur les tâches de fond).

## Critères d'acceptation

- 100 % des repos non-fork sont couverts, aucun appel n'échoue silencieusement.
- Un repo sans page Notion est signalé (pas créé automatiquement).
- Le rapport est reproductible : deux exécutions successives donnent le même verdict.

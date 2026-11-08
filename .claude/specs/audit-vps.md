# Spec — Audit automatisé du VPS OVH

Statut : **code fait et testé ; installation sur le VPS à faire par l'utilisateur** (voir `infra/audit/README.md`) ·
Lié à la page Notion « Audit VPS OVH ». Implémenté dans le module `security` (`collectors/vps.py`, `vps_runner.py`,
tâche `security.audit_vps` à 05:10 UTC, commande `audit_vps`).

## Objectif

Produire chaque semaine un rapport lisible de l'état du VPS et alimenter la checklist Notion,
sans intervention manuelle et **en lecture seule**.

## Relevés

- Conteneurs Docker : nom, image, état, uptime, healthcheck, limites mémoire.
- RAM et swap (total / utilisé / disponible) ; charge CPU moyenne.
- Disque : occupation par partition, volumes Docker, images non utilisées.
- Certificats SSL (Certbot) : domaine, date d'expiration, jours restants.
- Ports exposés : écoute réelle (`ss`) vs publiée par Docker ; alerte si un port est ouvert sur `0.0.0.0`
  sans raison (ex. PostgreSQL, Redis).
- NGINX : syntaxe valide, liste des `server_name` et upstreams.
- Mises à jour de sécurité en attente.

## Décisions d'implémentation (2026-11-07)

- **Pas de GitHub Actions** : l'audit est lancé par Vigil lui-même (tâche planifiée), ce qui évite d'exposer les
  secrets SSH à un service tiers et garde l'historique dans la base de Vigil.
- **Clé SSH dédiée, restreinte au script** (`restrict,command="sudo -n /usr/local/bin/vigil-audit"`) : elle ne permet pas
  d'ouvrir un shell. Le script est installé en `root:root 0755` par l'utilisateur ; Vigil ne l'envoie jamais.
- **Empreinte du serveur vérifiée** (`known_hosts` obligatoire) ; aucune commande construite avec des données externes.
- Le script (`infra/audit/vigil-audit.sh`) émet un JSON versionné (`schema: 1`) ; toute section illisible vaut `null` +
  une raison, et devient un contrôle **non couvert**, jamais « OK ».
- Testé dans des conteneurs Debian (lecture seule, sans réseau, sans capacités), dont un environnement volontairement mal
  configuré : les rapports réels servent de jeux de données (`src/modules/security/fixtures/`).
- Non vérifié : une exécution sur le vrai VPS (aucun accès à ce jour) et le comportement exact d'`ufw`, de `certbot` et
  de Docker sur ce serveur.
- Les alertes graves passent par le même circuit que GitHub (alerte Discord, résumé hebdomadaire, page Notion).

## Exécution (conception initiale)

- Script versionné dans `infra/audit/` (shell + sortie JSON), exécuté via SSH.
- Déclencheur : GitHub Actions `schedule` (hebdo) + `workflow_dispatch` manuel.
- Clé SSH **dédiée à l'audit**, utilisateur à droits limités (pas la clé de déploiement) — à créer.
- Aucune commande qui modifie l'état du serveur.

## Sorties

1. Rapport Markdown en artefact du workflow (historique, comparaison semaine à semaine).
2. Mise à jour de la page Notion « Audit VPS OVH » : cases cochées, rapport du jour.
3. Alerte Discord si seuil franchi : disque > 80 %, RAM > 85 %, certificat < 14 jours, port inattendu.

## Critères d'acceptation

- Le workflow s'exécute sans secret supplémentaire dans les logs.
- Le rapport couvre les 5 points de la checklist Notion.
- Une exécution à blanc sur le VPS ne change rien (vérifié par comparaison avant/après).

## Questions ouvertes

- SSH est-il joignable depuis les runners GitHub (restriction par IP ?).
- Coolify : l'audit doit-il aussi couvrir ses conteneurs si on l'adopte ?

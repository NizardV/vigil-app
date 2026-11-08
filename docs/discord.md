# Serveur Discord Vigil

Serveur personnel (un seul membre humain + le bot Vigil). Principe : **un salon = un type de message, avec une seule
règle de gravité.** Un salon d'alertes ne contient que des alertes : chaque message mérite alors l'attention.

## Structure cible

| Catégorie | Salon | Variable d'environnement | Contenu | Rythme (UTC) |
|---|---|---|---|---|
| 📌 VIGIL | `#lisez-moi` | — | À quoi sert chaque salon (ce document, en résumé) | manuel |
| 📌 VIGIL | `#changelog` | `DISCORD_WEBHOOK_CHANGELOG` *(à venir)* | Versions publiées (release-please) | à chaque version |
| 🛡️ SÉCURITÉ | `#sécurité-alertes` | `DISCORD_WEBHOOK_SECURITY_ALERTS` | Nouveaux constats **critiques/hauts**, une seule fois chacun (doublons regroupés en `×N`) | audit à 05:00 UTC, si nouveau |
| 🛡️ SÉCURITÉ | `#sécurité-résumé` | `DISCORD_WEBHOOK_SECURITY_DIGEST` | État d'ensemble + **santé de l'audit** | lundi 08:00 UTC |
| 📁 PROJETS | `#projets-résumé` | `DISCORD_WEBHOOK_PROJECTS_DIGEST` | Activité réelle de la semaine, par projet | lundi 09:00 UTC |
| 📰 VEILLE | `#veille-<thème>` | `DISCORD_WEBHOOK_WATCH_<SLUG>` (et `discord_channel_id` du thème pour les boutons) | Résumé du thème + un message par article noté par Gemini, boutons 👍/👎 | à l'heure du thème, heure de Paris |
| ⚙️ SYSTÈME | `#système` | `DISCORD_WEBHOOK_SYSTEM` | Tâche en échec, bloquée ou en retard (une seule fois par problème) | vérifié toutes les 15 min |
| ⚙️ SYSTÈME | `#déploiements` | `DISCORD_WEBHOOK_DEPLOYMENTS` *(à venir)* | Résultats de CI/CD | à chaque déploiement |

Les catégories **jobs, gaming, learn, social** s'ajoutent le jour où le module correspondant existe, pas avant.
Le salon vocal « Général » n'a pas d'usage et peut être supprimé.

## Heures : tout est en UTC

Les tâches planifiées (Procrastinate) interprètent leurs horaires en **UTC**, pas à l'heure de Paris : `05:00 UTC` = 06:00 en hiver, 07:00 en été. Les heures de ce document sont donc en UTC.

## Règles

1. **Un webhook par salon.** Si l'un fuite, on ne révoque que celui-là ; chaque module n'écrit que dans « son » salon.
2. **Les URL de webhook sont des secrets** : variables d'environnement uniquement, jamais dans le repo ni dans un log.
   Vigil ne les affiche jamais et n'accepte que des URL `https://discord.com/api/webhooks/…`.
3. **Pas de bruit.** Un message qui n'appelle aucune décision n'a rien à faire dans un salon d'alertes. L'activité
   courante va dans un résumé hebdomadaire, jamais message par message.
4. **Le bot n'a que « envoyer des messages » et « intégrer des liens »**, jamais de droits d'administrateur.
5. **Aucune mention.** Vigil envoie ses messages sans pouvoir mentionner qui que ce soit (`@everyone` neutralisé).
   Pour être prévenu d'une alerte, règle les notifications du **salon** `#sécurité-alertes` sur « tous les messages »
   et laisse les autres sur « rien » ou « mentions seulement ».

## Mise en place d'un salon

1. Créer le salon dans la bonne catégorie.
2. Paramètres du salon → **Intégrations → Webhooks → Nouveau webhook** → copier l'URL.
3. La mettre dans l'environnement, par exemple `DISCORD_WEBHOOK_SECURITY_ALERTS=https://discord.com/api/webhooks/…`.
4. Vérifier avec un aperçu puis un envoi réel :
   ```
   uv run python src/manage.py notify_security --dry-run      # aperçu, rien n'est envoyé ni marqué
   uv run python src/manage.py notify_security                # envoi
   uv run python src/manage.py notify_security --digest
   uv run python src/manage.py projects_digest --dry-run
   uv run python src/manage.py projects_digest
   ```

**Ajouter un nouveau salon** ne demande aucun changement de configuration : `DISCORD_WEBHOOK_<NOM>` crée
automatiquement le salon logique `<nom>` (en minuscules, `_` → `-`), que le code lit avec
`core.notifications.webhook_url("<nom>")`.

## Ancien salon `#vigil-projects`

Il recevait un message par push, sur n'importe quelle branche, d'où un « Deploy to GitHub pages » quotidien (commits
générés sur la branche `output` du dépôt de profil). Il est remplacé par `#projets-résumé`, alimenté par **interrogation
hebdomadaire de la branche par défaut uniquement**, avec écartement des bots, des déploiements et des fusions
(`PROJECTS_IGNORED_COMMIT_PATTERNS` dans `config/settings.py`). On peut l'archiver ou le renommer une fois la nouvelle
version en production : l'ancien backend continue de l'alimenter tant qu'il tourne.

## État

| Élément | État |
|---|---|
| Résumé sécurité + alertes | fait ; **envoi réel validé le 2026-11-07** (messages reçus dans les bons salons, notification sonore OK) |
| Résumé projets (filtre du bruit) | fait ; **envoi réel validé le 2026-11-07** |
| Salon `#système` | fait et testé ; **envoi réel à valider** (créer le salon et son webhook) |
| Salons veille | code fait et testé ; **à créer** (un par thème) avec webhook, et bot + URL d'interactions pour les boutons |
| Salon déploiements | à faire avec le déploiement |
| Boutons de feedback 👍/👎 (veille) | **portés** (`/watch/discord/interactions/`, signature Ed25519 obligatoire) ; URL à déclarer dans le portail Discord ; l'ancien gestionnaire était très probablement cassé |

# Spec — Module Vigil System (santé de Vigil lui-même)

Statut : MVP fait et testé · Module : `src/modules/system/` · Salon Discord : `#système` (`DISCORD_WEBHOOK_SYSTEM`)

## Objectif

Être prévenu quand **Vigil lui-même** ne fait plus son travail : une tâche échoue, se bloque, ou n'a pas tourné
quand elle le devait. Sans ça, une alerte de sécurité ou un audit pourrait ne jamais arriver sans que personne le sache.

## Détecteurs (vérifiés toutes les 15 minutes, `system.health_check`)

| Détecteur | Condition | Marge |
|---|---|---|
| Tâche en échec | statut `failed` après épuisement des réessais, dans les 7 derniers jours | — |
| Tâche bloquée | statut `doing` depuis plus d'une heure | 1 h |
| Tâche en retard | dernière exécution **prévue** d'une tâche périodique jamais terminée avec succès | 2 h |

- Les horaires des tâches périodiques sont des **cron en UTC** (comportement de Procrastinate, vérifié).
- Une exécution prévue **avant** la première activité enregistrée n'est pas comptée (évite une fausse alerte juste
  après la mise en route).
- Les échecs d'une même tâche sont **regroupés par jour** (clé `failed:<tâche>:<jour>`) : une tâche périodique qui échoue
  toutes les 20 minutes (API du modèle en panne) ne produit qu'une alerte par jour, pas 72.
- Chaque problème est signalé **une seule fois** (`SentAlert`, clé stable : `failed:<id>`, `overdue:<tâche>:<heure>`).
- Un échec d'envoi ne marque rien et fait échouer la tâche : le problème sera signalé au passage suivant.
- Webhook absent : rien n'est envoyé ni marqué (visible, pas silencieux).

## Ce que ce salon ne peut pas voir : un worker arrêté

C'est le worker qui exécute la vérification : s'il est mort, personne ne vérifie rien. D'où le **signal de vie** :
après chaque vérification **réussie**, Vigil appelle `VIGIL_HEARTBEAT_URL` (service externe : healthchecks.io,
Uptime Kuma…). Si Vigil cesse de répondre (worker arrêté, base inaccessible, VPS éteint), c'est ce service qui alerte.
Le signal n'est **pas** envoyé si la vérification échoue : un faux signal de vie masquerait la panne.

Configuration recommandée côté service externe : période 15 min, tolérance 20–30 min, notification par e-mail
ou autre canal **indépendant de Discord et du VPS**.

## Disponibilité des sites hébergés (`system.uptime_check`, toutes les 5 minutes)

Ajouté le 2026-11-11 après une panne de l'API Cyna (base arrêtée du 08 au 10/11) restée invisible deux jours.
Le signal de vie externe voit le **VPS ou Vigil** s'arrêter ; ce contrôle voit une **application** en panne sur un
VPS qui tourne. Les deux se complètent.

- Sites : `VIGIL_UPTIME_SITES` (« Nom=https://url,… » ; par défaut Portfolio, Cyna site, Cyna API `/health`, qui
  teste aussi la base). Vigil lui-même n'y est pas : c'est le rôle du signal de vie externe.
- Un site est en panne s'il ne répond pas **200** (après redirections) en 10 s. Requête via `core.netfetch` (adresses
  publiques uniquement).
- Alerte #système après **2 échecs d'affilée** (un raté isolé n'alerte pas), puis **une alerte au retour** avec la
  durée de la panne. Une alerte non envoyée n'est pas marquée : retentée au passage suivant.
- État visible dans l'admin : Système › Sites surveillés. Commande : `manage.py check_sites`.

## Hors périmètre (volontairement)

- Santé de l'infrastructure (RAM, disque, conteneurs, certificats) : module `security`, audit du VPS.
- Message « résolu » quand un problème disparaît : non prévu pour l'instant.

## Critères d'acceptation (vérifiés par les tests)

- Un échec récent est signalé ; un échec ancien ou une tâche réussie ne le sont pas.
- Une tâche en retard est signalée après sa marge, jamais avant, jamais si elle a réussi depuis l'heure prévue.
- Aucune alerte avant la première activité ; aucune répétition d'un même problème.
- Le signal de vie n'est jamais envoyé après un échec de vérification ; l'URL n'apparaît dans aucune erreur.

# Spec — Module Vigil Watch (veille RSS analysée par Gemini)

Statut : **code fait et testé (359 tests)** ; envoi réel Discord et appels Gemini **non vérifiés** (aucune clé / aucun salon
configuré à ce jour) · Module : `src/modules/watch/` · Remplace la veille de l'ancien `vigil-backend`.

## Fonctionnement

1. **Collecte** (`watch.fetch_sources`, toutes les heures à :07 UTC) : télécharge les sources dont l'intervalle est écoulé.
   Intervalle par défaut : **84 h (deux collectes par semaine)**. Chaque nouvel article coûte une analyse Gemini : on
   ignore ceux publiés avant la période du résumé du thème (`lookback_days`), et on garde **au plus 10 nouveaux
   articles par source et par collecte** (les plus récents). Avant le 2026-11-13 : 6 h, ~770 analyses par semaine.
   Types : RSS/Atom, YouTube, Reddit, releases GitHub, Hacker News (`hn:mot-clé`).
2. **Analyse** (`watch.analyze_pending`, toutes les 20 min) : Gemini produit résumé, points clés et score 1–10 ;
   plafond `WATCH_MAX_ANALYSES_PER_RUN` (30) par passage pour borner le coût.
3. **Résumé** (`watch.send_digests`, toutes les heures à :03 UTC) : envoyé à l'heure choisie du thème, **heure de Paris**.
   Avec le bot : **un seul message** (Discord « Components V2 ») : le résumé, puis chaque article avec ses boutons
   👍/👎/Lire juste en dessous, **6 articles au plus** (limites Discord : 40 éléments, 4 000 caractères ; un article
   qui ne tient pas reste candidat pour le résumé suivant). Sans bot (webhook) : en-tête + un message par article, sans
   boutons. Le 👍/👎 oriente les analyses suivantes.

## Différences volontaires avec l'ancienne version

| Ancien comportement | Nouveau |
|---|---|
| Résumé = 10 meilleurs articles de **tous les temps** (aucun filtre de date) | Articles des 48 dernières heures, **jamais envoyés** (`digested_at`), au-dessus du score du thème |
| Boutons 👍/👎 : le gestionnaire insérait un retour sans utilisateur alors que la colonne est obligatoire (très probablement en échec) | Retour par auteur Discord, un seul par article, le dernier remplace le précédent |
| Flux téléchargés sans contrôle (SSRF) | `core/netfetch.py` : adresses privées refusées, connexion épinglée sur l'IP validée, 3 redirections revalidées, taille bornée |
| Une page web quelconque aurait passé pour un flux valide vide | Contenu non reconnu comme flux = **échec enregistré** sur la source |
| `sentence-transformers` (PyTorch, ~400 Mo, alerte critique `trust_remote_code`) pour la recherche sémantique | Supprimé. Recherche **plein texte PostgreSQL** (titre + résumé). Perte temporaire : la recherche « par sens » (ADR 0005) |
| URL des webhooks Discord stockées **en clair en base** | Salons nommés en variables d'environnement `DISCORD_WEBHOOK_WATCH_<SLUG>` |
| Heure d'envoi en UTC | Heure de Paris |
| Sources par « heure modulo intervalle » | Intervalle réel depuis la dernière collecte (`last_fetched_at`) |
| SDK `google-generativeai` (abandonné) | Appel REST direct (`core/llm.py`), clé en en-tête, consignes séparées du contenu |
| Auth maison (JWT, TOTP, bcrypt…) pour l'API | Admin Django ; plus d'API publique, seul `/watch/discord/interactions/` est exposé |

## Sécurité

- **Contenu d'article = donnée non fiable** : transmis comme contenu (jamais dans les consignes), réponse du modèle validée
  strictement (types, bornes 1–10, longueurs), texte généré privé de ses liens, mentions neutralisées.
- **Seul point public : `POST /watch/discord/interactions/`**. Signature Ed25519 obligatoire (rejeu > 5 min refusé) ; sans
  `DISCORD_PUBLIC_KEY`, la vue répond 503 et n'accepte rien. `custom_id` strictement validé (`watch:fb:<id>:<1|-1>`).
- Sources en échec : suspendues après 10 échecs consécutifs (réactivation dans l'admin).
- Les tests tournent **sans réseau** (`core/test_runner.py`) : toute connexion sortante les fait échouer.

## Configuration

`GEMINI_API_KEY` (secret), `GEMINI_MODEL` (défaut `gemini-2.5-flash`, non vérifié sans clé), `WATCH_MAX_ANALYSES_PER_RUN`,
`DISCORD_BOT_TOKEN` (secret) et `DISCORD_PUBLIC_KEY` pour les boutons, `DISCORD_WEBHOOK_WATCH_<SLUG>` par thème.
Boutons : renseigner `discord_channel_id` du thème **et** définir l'URL d'interactions du bot dans le portail Discord :
`https://vigil.nizard.dev/watch/discord/interactions/`. Sans bot, le résumé part par webhook, **sans boutons**.

## Reprise des données de l'ancien Vigil (`import_legacy_watch`)

Idempotent, simulable (`--dry-run`). Procédure à la bascule :
1. **Sauvegarder** l'ancienne base (`pg_dump`) — première sauvegarde de la production, à conserver hors du VPS.
2. Charger ce dump dans un schéma `legacy` de la nouvelle base.
3. `import_legacy_watch --dry-run` puis `import_legacy_watch`.
4. Reconfigurer les salons (variables `DISCORD_WEBHOOK_WATCH_*`) : les anciens webhooks stockés en base ne sont **pas** repris.
5. Les anciens articles sont marqués « déjà envoyés » (pas de résumé géant au premier passage), leurs URL sont normalisées,
   l'heure d'envoi UTC est convertie en heure de Paris (attention : elle dérive d'une heure entre été et hiver si elle était fixée en UTC).

## Limites connues

- Recherche par le sens non reprise (voir ADR 0005) ; un retour 👍/👎 n'influence que le thème concerné.
- YouTube : la résolution d'une chaîne `@nom` lit la page publique (peut casser si YouTube change son format).
- Reddit peut refuser les flux RSS non authentifiés ; Hacker News (`hnrss.org`) est un service tiers, parfois en panne
  (502 constaté le 2026-11-07).
- Non testé : appel réel à Gemini, envoi réel Discord (webhook et bot), clic réel sur un bouton.

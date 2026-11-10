# Spec — Module Vigil Security

Statut : MVP GitHub fait et vérifié sur les vrais dépôts · Module : `src/modules/security/`

## Objectif

Surveiller en continu la sécurité des **projets** (dépôts GitHub) et des **infrastructures** (VPS, conteneurs,
certificats), centraliser les constats dans un seul endroit, suivre leur cycle de vie (ouvert → résolu) et
prévenir quand quelque chose de grave apparaît.

## Principes (non négociables)

1. **Un contrôle qui n'a pas pu s'exécuter n'est jamais « OK ».** Statuts : `ok`, `findings`, `unavailable`
   (droits/fonction absents), `error` (réseau, 5xx, quota). Seuls `ok` et `findings` peuvent *résoudre* des
   constats ; `unavailable` et `error` laissent l'état précédent intact et restent visibles comme « non couvert ».
2. **Lecture seule.** Aucun contrôle ne modifie la cible.
3. **Aucun secret stocké.** Pour un secret détecté, on garde le type et le lien de l'alerte, jamais la valeur.
4. **Idempotent.** Relancer un audit ne duplique rien (empreinte stable par constat).
5. **Un constat ignoré le reste** (avec une note), même si le scan le revoit.
6. Le module n'importe aucun autre module ; l'accès GitHub passe par `core/github.py`.

## Modèle

- `Finding` : un constat (`source`, `target`, `check_name`, `severity` 0–4, `title`, `detail`, `url`,
  `status` open/resolved/ignored, `note`, `first_seen`, `last_seen`, `resolved_at`), clé = `fingerprint`.
- `CheckResult` : dernier état de chaque contrôle par cible (`ok/findings/unavailable/error`) = la **couverture**.

## Catalogue des audits

### Phase 1 — GitHub (implémentée)

| Contrôle | Source | Gravité |
|---|---|---|
| Alertes Dependabot ouvertes | `/dependabot/alerts` | celle de l'advisory |
| Alertes code scanning ouvertes | `/code-scanning/alerts` | `security_severity_level` sinon niveau de la règle |
| Alertes secret scanning ouvertes | `/secret-scanning/alerts` | critique |
| Branche par défaut protégée | `/branches/{b}/protection` | faible |

Périmètre : dépôts du compte configuré (`GITHUB_OWNER`), forks et archivés exclus. Planification : tous les
jours à 05:00 UTC (après la synchro projets de 04:00).

### Phase 2 — Infrastructure (spec `audit-vps` ; **implémentée**, installation sur le VPS à faire)

| Contrôle | Gravité indicative |
|---|---|
| Certificats TLS : expiration < 30 j (moyenne), < 14 j (haute), expiré (critique) | selon délai |
| Ports en écoute exposés sur `0.0.0.0` (PostgreSQL, Redis, Docker API…) | haute |
| Configuration SSH : mot de passe actif, root autorisé | haute |
| Mises à jour de sécurité système en attente | moyenne |
| Conteneurs : tournent en root, images non épinglées, sans limites mémoire | faible/moyenne |
| Espace disque / RAM au-delà des seuils | moyenne |
| Sauvegardes PostgreSQL absentes ou trop anciennes | haute |
| Pare-feu (UFW/iptables) inactif | haute |

### Phase 3 — Chaîne d'approvisionnement et surface externe

| Contrôle | Outil |
|---|---|
| Vulnérabilités des images Docker | Trivy |
| Vulnérabilités des dépendances Python / npm | `pip-audit`, `npm audit` (en CI) |
| Secrets dans l'historique git | gitleaks |
| En-têtes HTTP de sécurité des sites publics | requêtes HTTP |
| Domaines : expiration, DNS inattendu | whois / DNS |

## Sorties

1. Admin Django : liste des constats (filtres gravité/statut/cible) et tableau de couverture.
2. Commande `scan_security` (résumé en texte) et tâche planifiée Procrastinate.
3. ~~Notification Discord~~ **fait** (voir « Notifications » ci-dessous). Ancien libellé : constat critique ou haut (nécessitait l'extraction des
   notifications dans `core` — à faire).
4. Page Notion « Vigil Security » alimentée par le résumé (module de synchro Notion — à faire).

## Limites connues du MVP

- Un dépôt supprimé ou archivé voit ses constats ouverts résolus (et ses contrôles retirés de la
  couverture) à la fin d'un audit GitHub **complet** : jamais si le quota a interrompu l'audit ou si
  aucun dépôt n'a été audité. Limite : si le jeton perdait l'accès aux dépôts privés (listage public
  de secours), leurs constats seraient résolus, puis rouverts et re-notifiés au retour de l'accès.
- Pas encore de notification ni de synchro Notion.

## Critères d'acceptation (MVP)

- Un contrôle impossible (droits, quota) apparaît comme `unavailable`/`error`, jamais comme `ok`.
- Une alerte disparue est marquée résolue ; réapparue, elle est rouverte ; ignorée, elle reste ignorée.
- Aucune valeur de secret n'est présente en base.
- Relancer l'audit deux fois de suite ne crée aucun doublon.

## Phase 1b — Remédiation automatique des dépendances (décision du 2026-11-07, mécanisme A)

Constat de départ : Dependabot et ses correctifs de sécurité sont déjà actifs sur tous les dépôts et ouvrent des
PR de correction ; ce qui manquait, c'était la **fusion**. Exemple : `Portfolio` #38 (`next` 16.2.6 → 16.4.1)
a corrigé 5 alertes critiques une fois fusionnée (vérifié : alertes critiques 5 → 0, déploiement Vercel réussi).

**Mécanisme retenu : auto-merge natif de GitHub**, Vigil jouant le rôle de contrôleur. Pas de jeton d'écriture
longue durée dans Vigil (un VPS compromis ne doit pas pouvoir pousser dans tous les dépôts).

### Politique de fusion automatique

Une PR Dependabot est fusionnée automatiquement si **toutes** les conditions sont vraies :
1. mise à jour **patch ou mineure** (jamais majeure) ;
2. le dépôt a une **CI exécutée sur les PR** et elle est verte ;
3. pas de conflit, aucun contrôle en échec.
Sinon : fusion manuelle, signalée par Vigil.

### Prérequis par dépôt (sans quoi l'auto-merge fusionne SANS attendre la CI)

1. Protection de la branche par défaut avec **contrôle d'état obligatoire** (la CI).
2. `allow_auto_merge` activé sur le dépôt.
3. Workflow d'auto-merge (`dependabot/fetch-metadata` + `gh pr merge --auto`), ajouté **par PR**, jamais par push direct.

### État des dépôts au 2026-11-07

- CI sur les PR : `Portfolio`, `vigil-backend`, `vigil-streamlit`, `blind-e`, `AgeOfCode` (5 sur 20).
- Sans CI sur les PR, donc **non éligibles tant qu'une CI minimale n'est pas ajoutée** : `cyna-web` (14 PR en
  attente), `cyna-api`, `cyna-app`, `Cybedu`, `SkyByDns`, `Webzine`, `WitcherBoard`, etc.
- Protection de branche : absente presque partout. Limite à confirmer : sur l'offre gratuite, la protection
  de branche des dépôts **privés** peut être indisponible, donc auto-merge impossible pour eux.

### Secret scanning (8 dépôts sans)

- Dépôts publics (`JikanAnimeExplorer`, `vigil`) : activable par l'API, gratuit.
- Dépôts privés (`animob`, `blind-e`, `cyna-infra`, `readme-stats`, `vigil-infra`, `Projects`) : payant →
  remplacer par `gitleaks` dans la CI.

### Contrôles Vigil à ajouter (lecture seule)

- `automerge_ready` : par dépôt, CI sur PR ? protection avec contrôle obligatoire ? auto-merge autorisé ?
  workflow présent ? Résultat = constat tant que la politique n'est pas respectée.
- `stale_dependabot_prs` : PR de correction ouvertes depuis plus de N jours (le vrai symptôme observé).
- Chaque contrôle impossible reste `unavailable`, jamais `ok`.

### Ordre de déploiement

1. ~~Contrôles `automerge_ready` et `stale_dependabot_prs`~~ **fait** (2026-11-07, vérifié sur les 22 dépôts, lecture seule).
2. Pilote d'auto-merge : reporté, à intégrer dans le hub Vigil. Dépôts déjà dotés d'une CI : `Portfolio` d'abord (pilote), puis `vigil-backend`, `vigil-streamlit`, `blind-e`, `AgeOfCode`.
3. Ajout d'une CI minimale aux autres dépôts, puis activation.

### Détails d'implémentation des contrôles de posture (`collectors/posture.py`)

- `stale_dependabot_prs` : PR Dependabot ouvertes ≥ 14 jours (faible), ≥ 30 jours (moyenne). Se résout toute seule
  quand la PR est fusionnée ou fermée.
- `automerge_ready` : un seul constat par dépôt listant les manques (CI sur PR, contrôle obligatoire, auto-merge
  autorisé, workflow d'auto-merge), de gravité faible si des PR attendent, info sinon. **Haute gravité** si
  l'auto-merge est actif sans contrôle obligatoire (fusion sans attendre la CI).
- Contrôle obligatoire = protection classique **ou** ruleset. Indéterminable (droits, offre) → `unavailable`.
- Limite connue : « CI sur les PR » est détecté par la présence du mot `pull_request` dans un workflow ; un
  commentaire ou une condition peut donc donner un faux positif.

## Synchro Notion (fait, 2026-11-07)

Page cible : sous-page **« Tableau de bord sécurité »** (`NOTION_SECURITY_PAGE_ID`), enfant de la page « Vigil Security » (feuille de route, créée le 2026-07-05), pour que la feuille de route reste propre. Partager la page parente avec l'intégration Notion donne accès à la sous-page. Client partagé : `core/notion.py` ; logique : `modules/security/notion_sync.py` ; tâche `security.sync_notion` à 05:30 UTC ; commande `sync_notion [--dry-run]`.

- **Vigil ne modifie que la zone située sous le titre repère** « Généré par Vigil — ne pas éditer en dessous ». Tout ce qui est au-dessus (écrit à la main) n'est jamais modifié. Repère absent : il est ajouté en fin de page, rien n'est supprimé.
- Ordre « ajouter les nouveaux blocs puis supprimer les anciens » : une panne ne laisse jamais la page vide.
- Garde-fou : refus de supprimer plus de 300 blocs sous le repère.
- Contenu : dernier audit (et alerte s'il a plus de 48 h), constats par gravité, priorités (doublons en `×N`, liens https uniquement), PR Dependabot qui traînent, contrôles non couverts.
- **Prérequis pour l'automatisation** : créer une intégration interne sur notion.so/my-integrations, **partager la page « Vigil Security » avec elle** (menu ··· → Connexions), puis définir `NOTION_API_KEY` (secret). Sans jeton, la tâche ne fait rien et le dit (`not_configured`).
- La première version a été écrite à la main le 2026-11-07 avec le même repère ; l'automatisation la reprendra telle quelle.
- Non vérifié : les appels réels à l'API Notion (aucun jeton disponible) ; seul un faux Notion avec état est testé.

## Notifications Discord (fait, 2026-11-07)

Brique commune `core/notifications.py` (envoi) + `modules/security/notifications.py` (contenu). Variable
Webhooks `DISCORD_WEBHOOK_SECURITY_ALERTS` (alertes) et `DISCORD_WEBHOOK_SECURITY_DIGEST` (résumé), un par salon (secrets).

- **Alerte** : un message groupé pour les constats **critiques/hauts** jamais notifiés. Un constat n'est notifié qu'une
  fois (`notified_at`) ; il l'est de nouveau s'il se **rouvre** ou **s'aggrave**. Au plus 10 lignes détaillées,
  le reste est compté — même au premier envoi (123 constats = un seul message, tous marqués notifiés).
- **Résumé hebdomadaire** (lundi 08:00 UTC) : constats par gravité, priorités, PR Dependabot qui traînent, contrôles non
  couverts, et **santé de l'audit** : alerte si le dernier audit date de plus de 48 h. Son absence est elle-même un signal.
- Garanties : un échec d'envoi ne marque rien comme notifié et fait **échouer la tâche** pour qu'elle soit réessayée ;
  webhook absent = rien n'est envoyé ni marqué (visible, pas silencieux) ; aucune mention possible (`allowed_mentions`
  vide + neutralisation du texte externe) ; l'URL n'apparaît jamais dans une erreur ou un log ; seuls les hôtes
  Discord en https sont acceptés.
- Commandes : `notify_security [--digest] [--dry-run]` (aperçu sans envoi ni marquage).

Améliorations possibles : regrouper les doublons dans l'alerte (même faille signalée sur plusieurs fichiers de
dépendances) ; seuil de gravité et salon configurables ; accusé de réception côté Discord (boutons).

## Source « code » : SonarCloud (ajoutée le 2026-11-08)

Collecteur `collectors/sonarcloud.py` (client partagé `core/sonarcloud.py`, **lecture publique sans jeton**), tâche
`security.audit_sonarcloud` (05:20 UTC), commande `audit_sonarcloud`. Projets : `SONARCLOUD_PROJECTS` (défaut
`NizardV_vigil-app`). Deux contrôles par projet : `sonar_quality_gate` (échec = constat Haute) et `sonar_issues`.
- Sécurité et fiabilité : tous les problèmes, avec la gravité de SonarCloud (BLOCKER = critique).
- Maintenabilité : seulement les problèmes Haute/Bloquants, **plafonnés à Moyenne** : comptés dans le résumé, jamais
  d'alerte Discord (ce n'est pas de la sécurité).
- Projet injoignable ou introuvable : « non couvert », aucun constat n'est refermé.
- Pour marquer un faux positif revu : commentaire `# NOSONAR - raison` dans le code (puis le constat se résout seul).

## Résumé hebdomadaire unifié

Un message `#sécurité-résumé` (lundi 08:00 UTC) regroupe les sources (serveur, GitHub, code) : ouverts / nouveaux /
résolus **de la semaine**, gravités, 3 points prioritaires par source, PR Dependabot qui traînent, santé de l'audit.
Les constats CYNA de l'audit du VPS sont volontairement conservés dans le résumé.

---
name: vigil-notion
description: Lire et écrire la base Notion « 🧠 Personal OS » (suivi des projets de Nizard) sans casser son schéma. À utiliser pour synchroniser l'état des projets, remplir « Repo GitHub » ou mettre à jour statut/priorité.
---

# Base Notion « Personal OS »

- Data source : `collection://38ee0621-1f6f-8016-b56a-000b8bb4a0a1`
- Re-fetch la collection avant d'écrire : le schéma complet fait foi, pas cette copie.

## Colonnes et valeurs autorisées (état au 2026-11-07)

| Colonne | Type | Valeurs |
|---|---|---|
| Name | titre | libre |
| Statut | status (pas select) | `💡 Idée`, `⏸️ En pause`, `🔨 En cours`, `✅ Terminé` |
| Priorité | select | `🔴 Court terme`, `🟠 Moyen terme`, `🟡 Dans la foulée`, `🟢 Long terme` |
| Catégorie | multi-select | Desktop, Organisation, Infrastructure, Portfolio, Vigil, Web, Data, Game Dev, Sécurité |
| Stack | multi-select | liste fermée et **plus longue qu'à l'origine** (ex. GitHub, GitHub Actions, Trivy, Nuclei, SonarCloud, Grafana, RabbitMQ…) : relire le schéma avant d'écrire |
| Plateforme | select | Web, Desktop, Mobile, Multi |
| Repo GitHub | **url** | `https://github.com/NizardV/<repo>` (URL complète, pas `NizardV/<repo>`) |
| Vigil lié | case | `__YES__` / `__NO__` |
| Date début, Deadline (optionnel) | dates | propriétés étendues `date:<nom>:start` |

## Pages gérées par Vigil (zone générée)

- **Tableau de bord sécurité** : sous-page `411e0621-1f6f-8198-8984-e8df265e2b2d` de « Vigil Security » (`394e0621-1f6f-80b5-9cc0-edddfab1ecaa`, qui reste une feuille de route à la main). Vigil réécrit automatiquement tout ce qui est **sous** le titre `Généré par Vigil — ne pas éditer en dessous`. Ne jamais modifier ni supprimer cette zone à la main ; ne jamais renommer ce titre (c'est le repère). Le contenu au-dessus est libre et jamais touché.
- Pour une nouvelle page synchronisée, reprendre la même convention (repère `heading_2` + zone générée dessous) et voir `modules/security/notion_sync.py`.
- Écriture depuis Vigil : jeton d'**intégration interne** (`NOTION_API_KEY`), page partagée avec elle. Écriture depuis Claude : outils MCP Notion ; vérifier avant d'écrire qu'une page n'existe pas déjà (ex. « Vigil Security » existait vide depuis juillet).

## Règles

1. **Vérifie avant de marquer « Terminé »** : confronte au code/repo réel. Constat du 2026-11-07 :
   plusieurs pages ont un statut de propriété différent du statut écrit dans le corps de la page.
2. Les pages contiennent parfois des blocs dupliqués — ne supprime rien sans le dire à l'utilisateur.
3. Une valeur de select/multi-select absente du schéma fait échouer l'écriture : utilise exactement
   les libellés ci-dessus (emoji compris).
4. Remplir `Repo GitHub` avec l'URL complète `https://github.com/NizardV/<repo>` (la colonne est de type url) :
   c'est la clé de rapprochement avec l'audit GitHub (on en déduit `NizardV/<repo>` en retirant le préfixe).
5. Écris de façon incrémentale (une page à la fois) et rends compte de ce qui a changé.

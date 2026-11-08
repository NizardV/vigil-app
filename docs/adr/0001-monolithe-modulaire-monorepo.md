# ADR 0001 — Monolithe modulaire dans un monorepo

- Statut : acceptée (2026-11-07) — amendée par l'[ADR 0002](0002-django-comme-framework.md)
  (Django, tables préfixées au lieu de schémas Postgres, plus de Streamlit)
- Remplace : le plan « Modular Monolith → Microservices » (RabbitMQ, 8 services, une BDD par service)

## Contexte

Vigil est réparti sur quatre dépôts : `vigil` (archivé), `vigil-backend`, `vigil-streamlit`,
`vigil-infra`. Cette séparation oblige à synchroniser les versions à la main et disperse le contexte.
Le projet est porté par une seule personne et déployé sur un seul VPS à ressources limitées
(des limites mémoire ont déjà dû être posées sur les conteneurs).

## Décision

1. **Un seul dépôt (`vigil`)** regroupant apps, modules, core, infra et docs.
2. **Un monolithe modulaire** : un module par domaine, frontières imposées par `import-linter`,
   un schéma Postgres par module, communication par `service.py` public ou événements.
3. **Une seule image Docker**, lancée avec des commandes différentes (api, worker, beat).
4. **Bus d'événements Redis pub/sub** derrière une interface (`core/events`), remplaçable.
5. Les microservices restent possibles plus tard, **module par module**, si un besoin mesuré
   apparaît (charge, cycle de déploiement, équipe). Ce n'est pas un objectif.

## Pourquoi pas des microservices maintenant

- Chaque service ajoute un conteneur, un déploiement, une config, des pannes réseau et une BDD :
  coût fixe élevé pour un projet solo sur un VPS unique.
- Les frontières de module donnent déjà l'essentiel du bénéfice (couplage faible, ajout facile).
- RabbitMQ et une passerelle API ajoutent de l'exploitation sans problème concret à résoudre.

## Conséquences

- (+) Un clone, un contexte `.claude`, une CI, une version.
- (+) Ajouter un domaine = ajouter un dossier.
- (−) Discipline nécessaire : sans `import-linter` en CI, les frontières se dégradent.
- (−) Les trois repos actuels doivent être migrés (historique à conserver via `git subtree`).
- (−) Une panne ou un déploiement concerne tout le monolithe.

## Alternatives écartées

- **Garder 3–4 repos** : ne résout ni la dérive de versions ni la dispersion du contexte.
- **Microservices d'emblée** : voir ci-dessus.

# ADR 0003 — Procrastinate pour les tâches de fond

- Statut : acceptée (2026-11-07)
- Complète : [ADR 0002](0002-django-comme-framework.md), point 7 (planificateur à trancher)

## Contexte

Vigil a besoin de tâches planifiées (synchro GitHub, veille RSS, digests) et de traitements en arrière-plan
déclenchés par des webhooks. L'ancienne version utilisait Celery + Redis + beat. Contraintes : un seul
VPS à RAM limitée, un seul mainteneur, priorité à la stabilité.

## Décision

Utiliser **Procrastinate** (file de tâches stockée dans PostgreSQL, intégration Django).

## Raisons

- **Un composant en moins** : pas de Redis ni de beat séparé ; PostgreSQL est déjà indispensable.
- **Cohérence transactionnelle** : une tâche peut être enregistrée dans la même transaction que les
  données qui la motivent — pas de tâche orpheline ni de tâche perdue.
- **Visibilité** : les tâches sont des lignes SQL, consultables et filtrables.
- Le volume attendu (quelques milliers de tâches par jour au plus) est très en deçà des limites.

## Conséquences

- (+) Moins d'exploitation, sauvegardes uniques (la file est dans la base sauvegardée).
- (−) **PostgreSQL requis aussi en développement et en CI** pour tout test de bout en bout de la file.
  Les services restent de simples fonctions Python : leurs tests unitaires n'ont pas besoin de PostgreSQL.
- (−) Écosystème plus petit que Celery (moins d'exemples, moins d'outils tiers).

## Règles d'usage

- La logique reste dans `services.py` ; une tâche Procrastinate est une **enveloppe fine** qui appelle un
  service. Remplacer le planificateur doit rester peu coûteux.
- Les tâches sont **idempotentes**, avec réessais bornés et échecs visibles.
- Les commandes de management restent disponibles pour lancer un service à la main.

## Alternative écartée

**Celery + Redis** : plus mature et plus répandu, mais deux composants de plus à exploiter et aucune garantie
transactionnelle avec la base. À reconsidérer seulement en cas de volume ou de besoins avancés réels.

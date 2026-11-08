# ADR 0005 — Veille : pas d'embeddings locaux, recherche plein texte

- Statut : acceptée (2026-11-07)

## Contexte

L'ancien module de veille calculait, pour chaque article, un embedding avec `sentence-transformers`
(`paraphrase-multilingual-MiniLM-L12-v2`) stocké en pgvector, et s'en servait pour une recherche sémantique.

Coûts constatés :
- dépendance énorme (PyTorch, modèle d'environ 400 Mo chargé en mémoire) sur un VPS de 7,7 Go dont 2,7 Go disponibles ;
- image Docker de plusieurs gigaoctets ;
- alerte **critique** Dependabot sur `sentence-transformers` (chargement de modèle avec `trust_remote_code`) ;
- fonction rarement exploitée (un seul endpoint de recherche).

## Décision

1. Pas d'embeddings locaux. `sentence-transformers` et la colonne `embedding` ne sont pas repris.
2. Recherche **plein texte PostgreSQL** (`SearchVector` sur le titre, pondéré, et le résumé).
3. Si la recherche par le sens redevient utile : embeddings via l'API Gemini (appel HTTP, sans modèle local), stockés en
   pgvector (l'extension reste disponible) — à décider alors, avec mesure du coût.

## Conséquences

- (+) Image Docker légère, démarrage rapide, moins de RAM, une alerte critique de moins, pas de modèle à mettre à jour.
- (+) Recherche testée, sans appel externe, sans coût.
- (−) La recherche ne comprend pas les synonymes ni les reformulations (« faille » ne trouve pas « vulnérabilité »).
- (−) Les vecteurs existants ne sont pas migrés.

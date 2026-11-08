# Runbook — Fermer les ports exposés du VPS

Statut au 2026-11-08 : **appliqué** pour n8n (supprimé, inutilisé), CYNA staging et CYNA prod (ports 3000/3001/4000/4001
publiés sur `127.0.0.1`, vérifié depuis Internet : connexions refusées). **Reste** : 8000 et 8501 (ancien Vigil), qui
disparaissent à la bascule vers le nouveau Vigil. Sauvegardes des fichiers modifiés : `*.bak-2026-11-07` à côté de chacun.

**Commande exacte pour relancer CYNA** (chaque environnement a son nom de projet et son fichier d'environnement) :
`docker compose -p cyna-<env> --env-file .env.<env> -f docker-compose.<env>.yml up -d`

Plan établi le 2026-11-07 par inspection en lecture seule du VPS et confirmé par des tentatives de connexion TCP depuis
Internet. Le détail ci-dessous est conservé comme référence.

## Constat

Docker publie ces ports sur **toutes les interfaces** (`0.0.0.0`) et ils sont **atteignables depuis Internet**, en contournant
NGINX (donc ses certificats, ses redirections et tout ce que tu pourrais y ajouter) :

| Port | Service | Fichier (sur le VPS) | Ligne actuelle |
|---|---|---|---|
| 5678 | **n8n** (automatisation) | `/home/debian/vigil-infra/docker-compose.yml` l.87 | `- "5678:5678"` |
| 8000 | ancien Vigil (API) | idem, l.39 | `- "8000:8000"` |
| 8501 | ancien Vigil (Streamlit) | idem, l.73 | `- "8501:8501"` |
| 4000 / 3000 | CYNA prod (API / web) | `/home/debian/cyna-infra/docker-compose/docker-compose.prod.yml` l.25 / l.48 | `- "${API_PORT:-4000}:8080"` / `- "${WEB_PORT:-3000}:80"` |
| 4001 / 3001 | CYNA staging (API / web) | `…/docker-compose.staging.yml` l.23 / l.44 | idem avec 4001 / 3001 |

PostgreSQL (`127.0.0.1:5433`) et Redis sont déjà correctement limités. Il n'y a **pas de pare-feu hôte** (`ufw` absent).
**NGINX proxifie déjà vers `127.0.0.1:<port>`** pour tous ces services : les publier sur le bouclage ne casse donc rien.

## Correction : publier sur 127.0.0.1 seulement

Le principe est de préfixer chaque mapping par `127.0.0.1:` — le service reste joignable par NGINX, plus par Internet.

| Fichier | Avant | Après |
|---|---|---|
| vigil-infra (l.39) | `"8000:8000"` | `"127.0.0.1:8000:8000"` |
| vigil-infra (l.73) | `"8501:8501"` | `"127.0.0.1:8501:8501"` |
| vigil-infra (l.87) | `"5678:5678"` | `"127.0.0.1:5678:5678"` — **ou retirer n8n s'il n'est plus utilisé** |
| cyna prod (l.25, l.48) | `"${API_PORT:-4000}:8080"`, `"${WEB_PORT:-3000}:80"` | `"127.0.0.1:${API_PORT:-4000}:8080"`, `"127.0.0.1:${WEB_PORT:-3000}:80"` |
| cyna staging (l.23, l.44) | `"${API_PORT:-4001}:8080"`, `"${WEB_PORT:-3001}:80"` | `"127.0.0.1:${API_PORT:-4001}:8080"`, `"127.0.0.1:${WEB_PORT:-3001}:80"` |

> **L'ancien Vigil disparaît à la bascule** : si elle a lieu rapidement, inutile de corriger `vigil-infra` (les lignes 39 et 73),
> mais **n8n** (l.87) est le plus exposé et peut être traité tout de suite. Le **nouveau** Vigil publiera ses ports sur
> `127.0.0.1` dès le départ.

### Procédure (un fichier à la fois, en commençant par le moins critique : staging)

```
cd /home/debian/cyna-infra/docker-compose
cp -a docker-compose.staging.yml docker-compose.staging.yml.bak-$(date +%F)          # 1. sauvegarde
sed -i 's|- "\${API_PORT:-4001}:8080"|- "127.0.0.1:${API_PORT:-4001}:8080"|; s|- "\${WEB_PORT:-3001}:80"|- "127.0.0.1:${WEB_PORT:-3001}:80"|' docker-compose.staging.yml
diff docker-compose.staging.yml.bak-* docker-compose.staging.yml                      # 2. relire : 2 lignes changées, rien d'autre
docker compose -f docker-compose.staging.yml config -q && echo "syntaxe OK"            # 3. valider
docker compose -f docker-compose.staging.yml up -d                                    # 4. recréer (quelques secondes d'interruption)
```

Répéter pour `docker-compose.prod.yml` (**interruption de quelques secondes de CYNA prod** : choisir le moment) puis `vigil-infra`.

### Vérification (à faire après chaque fichier)

1. Sur le VPS : `ss -tln | grep -E ':(3000|3001|4000|4001|5678|8000|8501)\b'` → chaque ligne doit afficher `127.0.0.1:<port>`.
2. Depuis ton PC, un test de connexion TCP doit **échouer** sur ces ports (PowerShell) :
   `Test-NetConnection <IP-DU-VPS> -Port 4001` → `TcpTestSucceeded : False`.
3. Les sites répondent toujours : `curl -sI https://cyna.nizard.dev`, `https://api.cyna.nizard.dev`, `https://staging.cyna.nizard.dev`,
   `https://vigil.nizard.dev` (code 200 ou 302, jamais 502).

### Retour arrière

```
cp -a docker-compose.staging.yml.bak-<date> docker-compose.staging.yml && docker compose -f docker-compose.staging.yml up -d
```

## Ensuite (facultatif) : pare-feu hôte

Une fois les ports publiés sur le bouclage, un pare-feu devient une **seconde ligne de défense** (et non la première : Docker
contourne `ufw` pour les ports qu'il publie, d'où la correction ci-dessus d'abord).

```
sudo apt install -y ufw
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable
```

⚠️ **Risque de te verrouiller hors du serveur.** Garde ta session actuelle ouverte, ouvre une seconde connexion SSH pour vérifier que
l'accès fonctionne **avant** de fermer la première, et vérifie que le pare-feu OVH (côté réseau) ne conflit pas avec ces règles.
L'audit du VPS signalera alors le pare-feu comme actif au lieu de « non couvert ».

## Autres constats de l'inspection (hors périmètre de ce runbook)

- **Aucune sauvegarde PostgreSQL** (`/var/backups` ne contient que des fichiers du système) : à créer avant la bascule.
- Allumé depuis **17 semaines sans redémarrage** : mises à jour du noyau probablement en attente.
- Cache de build Docker : 13 Go dont 9,6 Go récupérables (`docker builder prune`).
- Dossiers inutiles dans `/home/debian` : `vigil-old`, `nuclei-templates`, une archive `nuclei_*.zip`, `cleanup_webhooks.py`.
- Utilisateur `debian` : `sudo` sans mot de passe (pratique, mais à garder en tête pour la sécurité).

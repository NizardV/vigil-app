#!/bin/sh
# Déploie une version de Vigil. Installé sur le VPS dans /usr/local/bin/vigil-deploy.
# La clé SSH du CI est restreinte à cette commande (authorized_keys) :
#   restrict,command="/usr/local/bin/vigil-deploy" ssh-ed25519 AAAA... vigil-ci
# La version arrive dans $SSH_ORIGINAL_COMMAND ; elle est validée (aucune injection possible).
set -eu
TAG="${SSH_ORIGINAL_COMMAND:-latest}"
case "$TAG" in
  latest|sha-[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;;
  *) echo "version refusée" >&2; exit 2 ;;
esac
case "$TAG" in *[!A-Za-z0-9._-]*) echo "version refusée" >&2; exit 2 ;; esac

cd /home/debian/vigil
export VIGIL_TAG="$TAG"
PREVIOUS="$(cat .current-tag 2>/dev/null || echo latest)"

# L'image est publique : on la télécharge sans identifiant (un ancien identifiant ghcr.io périmé du serveur la ferait refuser).
DOCKER_CONFIG=/home/debian/vigil/.docker-anon docker compose --env-file vigil.env pull web worker migrate
docker compose --env-file vigil.env up -d --remove-orphans

# Attend que le site réponde ; sinon, retour automatique à la version précédente.
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if docker compose --env-file vigil.env exec -T web python -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=4).status==200 else 1)"; then
    echo "$TAG" > .current-tag
    echo "déployé : $TAG"
    exit 0
  fi
  sleep 5
done

echo "échec : retour à $PREVIOUS" >&2
export VIGIL_TAG="$PREVIOUS"
docker compose --env-file vigil.env up -d --remove-orphans
exit 1

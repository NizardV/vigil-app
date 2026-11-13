#!/bin/sh
# Copie chiffrée des sauvegardes du VPS vers Google Drive (rclone, remote chiffré « gdrive-chiffre »).
# Lancé chaque nuit par systemd/vigil-offsite.timer, après les sauvegardes locales (Vigil 03:30, Cyna 03:45 UTC).
# Voir docs/runbooks/sauvegardes.md. En cas d'échec : alerte dans #système (webhook lu dans vigil.env).
set -u
REMOTE="${VIGIL_OFFSITE_REMOTE:-gdrive-chiffre:}"
KEEP_DAYS=30
ENV_FILE=/home/debian/vigil/vigil.env

alert() {
  url=$(grep -E '^DISCORD_WEBHOOK_SYSTEM=' "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
  [ -n "$url" ] || return 0
  curl -fsS -m 10 -H "Content-Type: application/json" \
    -d "{\"embeds\":[{\"title\":\"⚠️ Copie hors VPS des sauvegardes en échec\",\"description\":\"$1\",\"color\":12000284}],\"allowed_mentions\":{\"parse\":[]}}" \
    "$url" >/dev/null || true
}

status=0
for src in /home/debian/vigil/backups /home/debian/cyna-backups; do
  name=$(basename "$src")
  # Seulement les sauvegardes récentes : les anciennes sont déjà là-bas.
  if ! rclone copy --max-age 3d --include '*.sql.gz' --include '*.dump' "$src" "$REMOTE$name"; then
    alert "rclone copy a échoué pour $name (voir journalctl -u vigil-offsite)."
    status=1
  fi
  # Rétention distante : 30 jours.
  rclone delete --min-age "${KEEP_DAYS}d" "$REMOTE$name" || status=1
done
[ $status -eq 0 ] && echo "ok $(date -u +%FT%TZ)"
exit $status

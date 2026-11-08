#!/bin/sh
# Sauvegarde quotidienne de la base Vigil (cron de l'utilisateur debian, voir le runbook de déploiement) :
#   30 3 * * * /home/debian/vigil/backup.sh >> /home/debian/vigil/backups/backup.log 2>&1
# Garde 14 jours. Un fichier vide ou une erreur de pg_dump fait échouer le script (et ne remplace rien).
set -eu
cd /home/debian/vigil
mkdir -p backups
OUT="backups/vigil-$(date +%F).sql.gz"
docker compose --env-file vigil.env exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$OUT.tmp"
[ -s "$OUT.tmp" ] || { echo "sauvegarde vide" >&2; rm -f "$OUT.tmp"; exit 1; }
mv "$OUT.tmp" "$OUT"
find backups -name 'vigil-*.sql.gz' -mtime +14 -delete
echo "ok $OUT $(du -h "$OUT" | cut -f1)"

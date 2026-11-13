# Runbook — Sauvegardes (Vigil + Cyna)

## Ce qui tourne

| Quoi | Quand (UTC) | Où | Rétention |
|---|---|---|---|
| Base Vigil (`backup.sh`, `vigil-backup.timer`) | 03:30 | `~/vigil/backups/*.sql.gz` | 14 jours |
| Bases Cyna prod + staging (`cyna-infra/scripts/backup-db.sh`, `cyna-backup.timer`) | 03:45 | `~/cyna-backups/*.dump` | 14 jours |
| **Copie chiffrée hors du VPS** (`offsite-backup.sh`, `vigil-offsite.timer`) | 04:15 | Google Drive, dossier `vigil-sauvegardes` (chiffré par rclone) | 30 jours |

Un échec de la copie hors VPS envoie une alerte dans #système.

## Mise en place de la copie hors VPS (une fois)

1. `sudo apt install -y rclone`
2. Sur un PC avec navigateur : `rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"` (accès limité aux fichiers créés
   par rclone), puis copier le jeton affiché.
3. Sur le VPS, `rclone config` :
   - remote `gdrive`, type `drive`, scope `drive.file`, « Use auto config » → `n`, coller le jeton ;
   - remote `gdrive-chiffre`, type `crypt`, cible `gdrive:vigil-sauvegardes`, mots de passe générés.
   - **Ranger les deux mots de passe du remote `crypt` dans Bitwarden** : sans eux, les copies sont illisibles.
4. Installer le script et le minuteur (fichiers copiés à la main, comme le reste de `infra/prod/`) :
   ```bash
   cp ~/vigil-app/infra/prod/offsite-backup.sh ~/vigil/ && chmod +x ~/vigil/offsite-backup.sh   # ou depuis le dépôt
   sudo cp vigil-offsite.service vigil-offsite.timer /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now vigil-offsite.timer
   sudo systemctl start vigil-offsite.service && journalctl -u vigil-offsite -n 5 --no-pager
   ```
5. Vérifier : `rclone ls gdrive-chiffre:` liste les sauvegardes (noms en clair côté rclone, chiffrés côté Drive).

## Restaurer

- **Depuis le VPS** : `gunzip -c ~/vigil/backups/vigil-AAAA-MM-JJ.sql.gz | docker compose --env-file vigil.env exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'`
  ; Cyna : `docker exec -i cyna_postgres pg_restore -U postgres -d cyna_prod --clean --if-exists < ~/cyna-backups/cyna_prod-AAAA-MM-JJ.dump`
- **VPS perdu** : sur une nouvelle machine, recréer les remotes `gdrive` et `gdrive-chiffre` **avec les mêmes mots de
  passe** (Bitwarden), puis `rclone copy gdrive-chiffre:backups ./backups` et `rclone copy gdrive-chiffre:cyna-backups ./cyna-backups`.

## Test de restauration

Fait le 2026-11-11 dans un PostgreSQL jetable : Cyna identique ligne pour ligne à la base en service ; Vigil complet
(24 tables). À refaire après tout changement de format de sauvegarde.

#!/usr/bin/env bash
# vigil-audit — audit de sécurité du serveur, en LECTURE SEULE. Émet un unique objet JSON sur la sortie standard.
#
# À installer en root:root, mode 0755, NON modifiable par l'utilisateur d'audit (voir infra/audit/README.md).
# Ce script ne modifie rien : il lit /proc, des fichiers de configuration et la sortie de commandes d'inspection.
# Toute section impossible à collecter vaut `null` et sa raison est dans `errors` : Vigil la traite comme
# « non couverte », jamais comme « sans problème ».
#
# Chaque section_* écrit son résultat dans la variable OUT (et non sur la sortie standard) : un appel dans un
# sous-shell $(...) perdrait les notes d'erreur qu'elle enregistre.
#
# Variable optionnelle : VIGIL_BACKUP_DIRS (dossiers séparés par « : » où chercher des sauvegardes).

set -u
export LC_ALL=C
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ERRORS=()
OUT=null

have() { command -v "$1" >/dev/null 2>&1; }

# Exécute une commande avec un délai maximal si `timeout` existe ; n'affiche jamais les erreurs.
run() {
  if have timeout; then timeout 15 "$@" 2>/dev/null; else "$@" 2>/dev/null; fi
}

note() { ERRORS+=("$1"); }

# Échappe une chaîne pour JSON (guillemets, antislash, retours ligne, tabulations ; retire les autres caractères de contrôle).
jstr() {
  local s=$1
  s=$(printf '%s' "$s" | tr -d '\000-\010\013\014\016-\037')
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}

# Nombre JSON, ou null si la valeur n'est pas numérique.
jnum() { if [[ ${1:-} =~ ^-?[0-9]+(\.[0-9]+)?$ ]]; then printf '%s' "$1"; else printf 'null'; fi; }

# Booléen JSON à partir de « true »/« false », sinon null.
jbool() { case "${1:-}" in true) printf 'true' ;; false) printf 'false' ;; *) printf 'null' ;; esac; }

join_by_comma() { local IFS=,; printf '%s' "$*"; }

# ---------------------------------------------------------------------------------------------------------------
section_memory() {
  OUT=null
  [ -r /proc/meminfo ] || { note "memory: /proc/meminfo illisible"; return; }
  local total avail swap_total swap_free
  total=$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)
  avail=$(awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo)
  swap_total=$(awk '/^SwapTotal:/ {print int($2/1024)}' /proc/meminfo)
  swap_free=$(awk '/^SwapFree:/ {print int($2/1024)}' /proc/meminfo)
  printf -v OUT '{"total_mb":%s,"available_mb":%s,"swap_total_mb":%s,"swap_used_mb":%s}' \
    "$(jnum "$total")" "$(jnum "$avail")" "$(jnum "$swap_total")" "$(jnum "$(( ${swap_total:-0} - ${swap_free:-0} ))")"
}

section_disks() {
  OUT=null
  have df || { note "disks: df absent"; return; }
  local items=() mount pct avail
  while read -r _ _ _ avail pct mount; do
    [[ $pct == *% ]] || continue
    items+=("{\"mount\":$(jstr "$mount"),\"used_pct\":$(jnum "${pct%\%}"),\"avail_mb\":$(jnum "$((avail / 1024))")}")
  done < <(run df -P -k -x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs | tail -n +2)
  printf -v OUT '[%s]' "$(join_by_comma "${items[@]+"${items[@]}"}")"
}

section_docker() {
  OUT=null
  if ! have docker; then OUT='{"available":false,"containers":[]}'; return; fi
  if ! run docker info >/dev/null; then note "docker: démon inaccessible (droits ou arrêté)"; return; fi
  local items=() name image state status detail user mem health restart privileged mode mounts has_sock
  while IFS='|' read -r name image state status; do
    [ -n "$name" ] || continue
    detail=$(run docker inspect --format '{{.Config.User}}|{{.HostConfig.Memory}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.HostConfig.RestartPolicy.Name}}|{{.HostConfig.Privileged}}|{{.HostConfig.NetworkMode}}|{{range .Mounts}}{{.Source}} {{end}}' "$name")
    IFS='|' read -r user mem health restart privileged mode mounts <<<"$detail"
    has_sock=false
    [[ ${mounts:-} == *docker.sock* ]] && has_sock=true
    items+=("{\"name\":$(jstr "$name"),\"image\":$(jstr "$image"),\"state\":$(jstr "$state"),\"status\":$(jstr "$status"),\"user\":$(jstr "${user:-}"),\"mem_limit_mb\":$(jnum "$(( ${mem:-0} / 1048576 ))"),\"health\":$(jstr "${health:-none}"),\"restart\":$(jstr "${restart:-}"),\"privileged\":$(jbool "${privileged:-}"),\"host_network\":$([ "${mode:-}" = host ] && printf true || printf false),\"mounts_docker_sock\":$has_sock}")
  done < <(run docker ps -a --format '{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}')
  printf -v OUT '{"available":true,"containers":[%s]}' "$(join_by_comma "${items[@]+"${items[@]}"}")"
}

section_listening() {
  OUT=null
  have ss || { note "listening: ss absent"; return; }
  local items=() proto local_addr proc
  while read -r proto _ _ _ local_addr _ proc; do
    [ -n "$local_addr" ] || continue
    proc=${proc#users:((\"}
    proc=${proc%%\"*}
    items+=("{\"proto\":$(jstr "${proto:0:3}"),\"local\":$(jstr "$local_addr"),\"process\":$(jstr "${proc:-}")}")
  done < <(run ss -H -tulnp)
  printf -v OUT '[%s]' "$(join_by_comma "${items[@]+"${items[@]}"}")"
}

section_ssh() {
  OUT=null
  local out password root port
  out=$(run sshd -T)
  if [ -z "$out" ]; then note "ssh: « sshd -T » indisponible (droits ou sshd absent)"; return; fi
  password=$(awk '$1=="passwordauthentication" {print $2}' <<<"$out")
  root=$(awk '$1=="permitrootlogin" {print $2}' <<<"$out")
  port=$(awk '$1=="port" {print $2; exit}' <<<"$out")
  printf -v OUT '{"password_auth":%s,"permit_root":%s,"port":%s}' "$(jstr "${password:-unknown}")" "$(jstr "${root:-unknown}")" "$(jnum "$port")"
}

section_firewall() {
  OUT=null
  local ufw="absent" rules=null
  if have ufw; then
    ufw=$(run ufw status | awk -F': ' '/^Status:/ {print $2; exit}')
    if [ -z "$ufw" ]; then ufw="unknown"; note "firewall: « ufw status » illisible (droits)"; fi
  fi
  if have iptables; then rules=$(jnum "$(run iptables -S | wc -l)"); fi
  printf -v OUT '{"ufw":%s,"iptables_rules":%s}' "$(jstr "$ufw")" "$rules"
}

section_updates() {
  OUT=null
  local pending=null reboot=false index_age=null stamp mtime now
  if have apt-get; then
    pending=$(jnum "$(run apt-get -s dist-upgrade | grep -E '^Inst ' | grep -ci 'security')")
    # Un « 0 mise à jour » calculé sur un index de paquets périmé ne prouve rien : on remonte l'âge de l'index.
    stamp=/var/lib/apt/periodic/update-success-stamp
    [ -e "$stamp" ] || stamp=/var/lib/apt/lists
    mtime=$(stat -c %Y "$stamp" 2>/dev/null)
    now=$(date +%s)
    [ -n "$mtime" ] && index_age=$(( (now - mtime) / 3600 ))
  else
    note "updates: apt-get absent (distribution non Debian/Ubuntu)"
  fi
  [ -e /var/run/reboot-required ] && reboot=true
  printf -v OUT '{"security_pending":%s,"reboot_required":%s,"apt_index_age_h":%s}' "$pending" "$reboot" "$(jnum "$index_age")"
}

section_certs() {
  OUT='[]'
  local items=() file domain end epoch
  if ! have openssl || [ ! -d /etc/letsencrypt/live ]; then return; fi
  for file in /etc/letsencrypt/live/*/fullchain.pem; do
    [ -e "$file" ] || continue
    if [ ! -r "$file" ]; then note "certs: $file illisible (droits)"; continue; fi
    domain=$(basename "$(dirname "$file")")
    end=$(run openssl x509 -enddate -noout -in "$file" | sed 's/^notAfter=//')
    epoch=$(date -d "$end" +%s 2>/dev/null)
    items+=("{\"domain\":$(jstr "$domain"),\"expires_epoch\":$(jnum "$epoch")}")
  done
  printf -v OUT '[%s]' "$(join_by_comma "${items[@]+"${items[@]}"}")"
}

section_backups() {
  OUT=null
  local dirs=${VIGIL_BACKUP_DIRS:-/var/backups:/srv/backups:/opt/backups:/home/*/backups}
  local newest="" epoch="" path="" d expanded names=() searched=()
  local IFS=:
  for d in $dirs; do searched+=("$d"); done
  unset IFS
  newest=$(
    for d in "${searched[@]}"; do
      # shellcheck disable=SC2086
      for expanded in $d; do
        [ -d "$expanded" ] || continue
        run find "$expanded" -maxdepth 3 -type f \
          \( -name '*.sql' -o -name '*.sql.gz' -o -name '*.dump' -o -name '*.dump.gz' -o -name '*.tar.gz' -o -name '*.tgz' -o -name '*.bak' \) \
          -printf '%T@ %p\n'
      done
    done | sort -rn | head -1
  )
  if [ -n "$newest" ]; then
    epoch=${newest%% *}
    epoch=${epoch%.*}
    path=${newest#* }
  fi
  for d in "${searched[@]}"; do names+=("$(jstr "$d")"); done
  printf -v OUT '{"latest_epoch":%s,"latest_path":%s,"searched":[%s]}' \
    "$(jnum "$epoch")" "$([ -n "$path" ] && jstr "$path" || printf null)" "$(join_by_comma "${names[@]}")"
}

section_nginx() {
  OUT=null
  if ! have nginx; then OUT='{"installed":false,"config_ok":null}'; return; fi
  local out
  out=$(nginx -t 2>&1)
  if [[ $out == *"syntax is ok"* ]]; then
    OUT='{"installed":true,"config_ok":true}'
  elif [[ $out == *"Permission denied"* || -z $out ]]; then
    note "nginx: « nginx -t » non exécutable (droits)"
    OUT='{"installed":true,"config_ok":null}'
  else
    OUT='{"installed":true,"config_ok":false}'
  fi
}

# --- Assemblage ----------------------------------------------------------------------------------------------
hostname_value=$(hostname 2>/dev/null)
os_value=""
if [ -r /etc/os-release ]; then os_value=$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-}"); fi
uptime_s=$(awk '{print int($1)}' /proc/uptime 2>/dev/null)

section_memory;    memory=$OUT
section_disks;     disks=$OUT
section_docker;    docker_json=$OUT
section_listening; listening=$OUT
section_ssh;       ssh_json=$OUT
section_firewall;  firewall=$OUT
section_updates;   updates=$OUT
section_certs;     certs=$OUT
section_backups;   backups=$OUT
section_nginx;     nginx_json=$OUT

err_items=()
for e in "${ERRORS[@]+"${ERRORS[@]}"}"; do err_items+=("$(jstr "$e")"); done

printf '{"schema":1,"collected_epoch":%s,"hostname":%s,"os":%s,"uptime_s":%s,"memory":%s,"disks":%s,"docker":%s,"listening":%s,"ssh":%s,"firewall":%s,"updates":%s,"certs":%s,"backups":%s,"nginx":%s,"errors":[%s]}\n' \
  "$(jnum "$(date +%s)")" "$(jstr "${hostname_value:-}")" "$(jstr "${os_value:-}")" "$(jnum "$uptime_s")" \
  "$memory" "$disks" "$docker_json" "$listening" "$ssh_json" "$firewall" "$updates" "$certs" "$backups" "$nginx_json" \
  "$(join_by_comma "${err_items[@]+"${err_items[@]}"}")"

"""Contrôles du serveur (VPS) à partir du rapport JSON produit par `infra/audit/vigil-audit.sh`.

Règle d'or, comme pour GitHub : ce qu'on n'a pas pu lire n'est JAMAIS « sans problème ». Une section
absente ou
`null` donne un contrôle `unavailable` (avec la raison fournie par le script), pas un `ok`.
"""

import json
from datetime import UTC, datetime
from functools import partial
from typing import Any

from django.conf import settings

from ..models import Finding
from ..outcomes import FINDINGS, OK, UNAVAILABLE, CheckOutcome, Observation

SOURCE = "vps"
SCHEMA = 1
MAX_REPORT_BYTES = 500_000
WILDCARD = {"0.0.0.0", "::", "*", "[::]"}  # noqa: S104 - on DÉTECTE une écoute sur toutes les interfaces
SENSITIVE_PORTS = {
    2375: "API Docker non chiffrée",
    2376: "API Docker",
    3306: "MySQL",
    5432: "PostgreSQL",
    5433: "PostgreSQL",
    6379: "Redis",
    9200: "Elasticsearch",
    11211: "Memcached",
    27017: "MongoDB",
}
S = Finding.Severity


class ReportError(Exception):
    """Rapport illisible ou d'un format inattendu."""


def parse_report(raw: str) -> dict[str, Any]:
    """Valide le rapport JSON (taille, forme, version de schéma). Ne fait confiance à aucune valeur."""
    if len(raw.encode("utf-8", errors="replace")) > MAX_REPORT_BYTES:
        raise ReportError("rapport trop volumineux")
    try:
        report = json.loads(raw.lstrip("﻿"))
    except ValueError:
        raise ReportError("le serveur n'a pas renvoyé du JSON (clé non restreinte au script ?)") from None
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ReportError(f"version de schéma inattendue (attendu : {SCHEMA})")
    if not isinstance(report.get("collected_epoch"), int):
        raise ReportError("date de collecte absente")
    return report


def _text(value: Any, limit: int = 120) -> str:
    return str(value if value is not None else "")[:limit]


def _result(target: str, name: str, observations: list[Observation]) -> CheckOutcome:
    return CheckOutcome(SOURCE, target, name, FINDINGS if observations else OK, observations)


def _unknown(report: dict, target: str, name: str, section: str) -> CheckOutcome:
    reasons = [e for e in report.get("errors") or [] if str(e).startswith(f"{section}:")]
    detail = _text(" ; ".join(reasons) or f"{section} : donnée absente du rapport", 300)
    return CheckOutcome(SOURCE, target, name, UNAVAILABLE, detail=detail)


def _obs(key: str, severity: int, title: str, detail: dict | None = None) -> Observation:
    return Observation(key=key, severity=int(severity), title=_text(title, 300), detail=detail or {})


# --- Contrôles ---------------------------------------------------------------------------------------------


def check_disks(report: dict, target: str) -> CheckOutcome:
    disks = report.get("disks")
    if disks is None:
        return _unknown(report, target, "vps_disk", "disks")
    found = []
    for disk in disks:
        pct = disk.get("used_pct")
        if isinstance(pct, int | float) and pct >= 80:
            severity = S.HIGH if pct >= 90 else S.MEDIUM
            mount = _text(disk.get("mount"), 80)
            found.append(_obs(f"disk:{mount}", severity, f"Disque {mount} plein à {pct:.0f} %", {"used_pct": pct}))
    return _result(target, "vps_disk", found)


def check_memory(report: dict, target: str) -> CheckOutcome:
    memory = report.get("memory")
    if not memory or not memory.get("total_mb") or memory.get("available_mb") is None:
        return _unknown(report, target, "vps_memory", "memory")
    found = []
    available = memory["available_mb"] / memory["total_mb"] * 100
    if available < 20:
        severity = S.HIGH if available < 10 else S.MEDIUM
        found.append(_obs("memory:low", severity, f"Mémoire disponible faible : {available:.0f} % seulement"))
    swap_total = memory.get("swap_total_mb") or 0
    if swap_total and (memory.get("swap_used_mb") or 0) / swap_total > 0.5:
        found.append(_obs("memory:swap", S.MEDIUM, "Swap utilisé à plus de 50 % : le serveur manque de mémoire"))
    return _result(target, "vps_memory", found)


def _split_listen(local: str) -> tuple[str, int | None]:
    address, _, port = local.rpartition(":")
    address = address.split("%")[0]  # « 127.0.0.53%lo » -> « 127.0.0.53 »
    return address, int(port) if port.isdigit() else None


def check_ports(report: dict, target: str) -> CheckOutcome:
    listening = report.get("listening")
    if listening is None:
        return _unknown(report, target, "vps_ports", "listening")
    allowed = set(settings.VPS_AUDIT_PUBLIC_PORTS)
    found: dict[str, Observation] = {}  # IPv4 + IPv6 sur le même port = un seul constat
    for entry in listening:
        address, port = _split_listen(_text(entry.get("local"), 80))
        if address not in WILDCARD or port is None:
            continue  # joignable seulement en local : sans risque réseau
        proto, process = _text(entry.get("proto"), 8), _text(entry.get("process"), 40) or "processus inconnu"
        key = f"port:{proto}:{port}"
        if port in SENSITIVE_PORTS:
            severity = S.CRITICAL if port == 2375 else S.HIGH
            title = f"{SENSITIVE_PORTS[port]} (port {port}) exposé sur toutes les interfaces"
        elif port not in allowed:
            severity, title = S.MEDIUM, f"Port {port}/{proto} ({process}) exposé sur toutes les interfaces"
        else:
            continue
        found[key] = _obs(key, severity, title, {"port": port, "process": process})
    return _result(target, "vps_ports", list(found.values()))


def check_ssh(report: dict, target: str) -> CheckOutcome:
    ssh = report.get("ssh")
    if not ssh:
        return _unknown(report, target, "vps_ssh", "ssh")
    found = []
    if ssh.get("password_auth") == "yes":
        found.append(_obs("ssh:password", S.HIGH, "SSH : l'authentification par mot de passe est activée"))
    if ssh.get("permit_root") == "yes":
        found.append(_obs("ssh:root", S.HIGH, "SSH : la connexion directe en root est autorisée"))
    unknown = "unknown" in (ssh.get("password_auth"), ssh.get("permit_root"))
    if unknown and not found:
        return CheckOutcome(SOURCE, target, "vps_ssh", UNAVAILABLE, detail="Configuration SSH partiellement illisible")
    return _result(target, "vps_ssh", found)


def check_firewall(report: dict, target: str) -> CheckOutcome:
    firewall = report.get("firewall")
    if not firewall:
        return _unknown(report, target, "vps_firewall", "firewall")
    ufw = firewall.get("ufw")
    if ufw == "active":
        return _result(target, "vps_firewall", [])
    if ufw == "inactive":
        title = "Pare-feu hôte (ufw) inactif : Docker publie des ports sans passer par ufw de toute façon"
        return _result(target, "vps_firewall", [_obs("firewall:ufw", S.MEDIUM, title)])
    detail = "ufw absent : un autre pare-feu (nftables, pare-feu OVH) n'est pas évalué"
    if ufw == "unknown":
        detail = "état d'ufw illisible (droits)"
    return CheckOutcome(SOURCE, target, "vps_firewall", UNAVAILABLE, detail=detail)


def check_updates(report: dict, target: str) -> CheckOutcome:
    updates = report.get("updates")
    if not updates or updates.get("security_pending") is None:
        return _unknown(report, target, "vps_updates", "updates")
    found = []
    pending = updates["security_pending"]
    if pending:
        severity = S.HIGH if pending >= 10 else S.MEDIUM
        found.append(_obs("updates:pending", severity, f"{pending} mise(s) à jour de sécurité en attente"))
    age = updates.get("apt_index_age_h")
    if isinstance(age, int) and age > 24 * 7:
        title = f"Index des paquets vieux de {age // 24} j : le décompte des mises à jour n'est pas fiable"
        found.append(_obs("updates:stale_index", S.LOW, title))
    if updates.get("reboot_required"):
        found.append(_obs("updates:reboot", S.LOW, "Un redémarrage est nécessaire pour appliquer des mises à jour"))
    return _result(target, "vps_updates", found)


def check_certs(report: dict, target: str) -> CheckOutcome:
    certs = report.get("certs")
    if certs is None or any(str(e).startswith("certs:") for e in report.get("errors") or []):
        return _unknown(report, target, "vps_certs", "certs")
    collected = report["collected_epoch"]
    found = []
    for cert in certs:
        domain, expires = _text(cert.get("domain"), 100), cert.get("expires_epoch")
        if not isinstance(expires, int):
            return CheckOutcome(SOURCE, target, "vps_certs", UNAVAILABLE, detail=f"date de {domain} illisible")
        days = (expires - collected) / 86400
        if days < 0:
            severity, title = S.CRITICAL, f"Certificat de {domain} EXPIRÉ depuis {-days:.0f} j"
        elif days < 14:
            severity, title = S.HIGH, f"Certificat de {domain} : expire dans {days:.0f} j"
        elif days < 30:
            severity, title = S.MEDIUM, f"Certificat de {domain} : expire dans {days:.0f} j"
        else:
            continue
        found.append(_obs(f"cert:{domain}", severity, title, {"days_left": round(days, 1)}))
    return _result(target, "vps_certs", found)


def check_backups(report: dict, target: str) -> CheckOutcome:
    backups = report.get("backups")
    if backups is None:
        return _unknown(report, target, "vps_backups", "backups")
    searched = ", ".join(_text(p, 60) for p in backups.get("searched") or [])
    latest = backups.get("latest_epoch")
    if not isinstance(latest, int):
        title = f"Aucune sauvegarde trouvée sur le serveur (dossiers examinés : {searched})"
        return _result(target, "vps_backups", [_obs("backup:none", S.HIGH, title)])
    age_days = (report["collected_epoch"] - latest) / 86400
    if age_days > 7:
        return _result(
            target,
            "vps_backups",
            [_obs("backup:old", S.HIGH, f"Dernière sauvegarde vieille de {age_days:.0f} j")],
        )
    if age_days > 2:
        return _result(
            target,
            "vps_backups",
            [_obs("backup:old", S.MEDIUM, f"Dernière sauvegarde vieille de {age_days:.0f} j")],
        )
    return _result(target, "vps_backups", [])


def _container_issue(found: list, name: str, issue: str, severity: int, title: str) -> None:
    found.append(_obs(f"container:{name}:{issue}", severity, title))


def check_containers(report: dict, target: str) -> CheckOutcome:
    docker = report.get("docker")
    if docker is None:
        return _unknown(report, target, "vps_containers", "docker")
    found = []
    for c in docker.get("containers") or []:
        name = _text(c.get("name"), 60)
        add = partial(_container_issue, found, name)
        if c.get("privileged"):
            add("privileged", S.HIGH, f"Conteneur {name} en mode privilégié (équivalent root sur l'hôte)")
        if c.get("mounts_docker_sock"):
            add("docker_sock", S.HIGH, f"Conteneur {name} monte le socket Docker (équivalent root sur l'hôte)")
        if c.get("state") in ("restarting", "dead") or c.get("health") == "unhealthy":
            add("unhealthy", S.MEDIUM, f"Conteneur {name} en mauvais état ({_text(c.get('status'), 60)})")
        if c.get("host_network"):
            add("host_network", S.MEDIUM, f"Conteneur {name} sur le réseau de l'hôte (aucun isolement réseau)")
        if c.get("state") != "running":
            continue
        if _text(c.get("user")) in ("", "root", "0"):
            add("root", S.LOW, f"Conteneur {name} s'exécute en root")
        if c.get("mem_limit_mb") == 0:
            add("no_mem_limit", S.LOW, f"Conteneur {name} sans limite de mémoire")
        image = _text(c.get("image"), 100)
        if image.endswith(":latest") or ":" not in image.rsplit("/", 1)[-1]:
            add("floating_tag", S.INFO, f"Conteneur {name} : image non épinglée ({image})")
    return _result(target, "vps_containers", found)


def check_nginx(report: dict, target: str) -> CheckOutcome:
    nginx = report.get("nginx")
    if nginx is None:
        return _unknown(report, target, "vps_nginx", "nginx")
    if not nginx.get("installed"):
        return _result(target, "vps_nginx", [])
    if nginx.get("config_ok") is None:
        return _unknown(report, target, "vps_nginx", "nginx")
    if nginx["config_ok"] is False:
        return _result(
            target,
            "vps_nginx",
            [_obs("nginx:config", S.HIGH, "Configuration NGINX invalide (« nginx -t » échoue)")],
        )
    return _result(target, "vps_nginx", [])


CHECKS = (
    check_disks,
    check_memory,
    check_ports,
    check_ssh,
    check_firewall,
    check_updates,
    check_certs,
    check_backups,
    check_containers,
    check_nginx,
)


def evaluate(report: dict, target: str) -> list[CheckOutcome]:
    return [check(report, target) for check in CHECKS]


def collected_at(report: dict) -> datetime:
    return datetime.fromtimestamp(report["collected_epoch"], tz=UTC)

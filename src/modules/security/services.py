"""Logique métier du module security (seule porte d'entrée pour les autres modules)."""

import hashlib
from collections import Counter

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.github import GitHubClient
from core.sonarcloud import SonarCloudClient

from . import vps_runner
from .collectors import vps as vps_collector
from .collectors.github import audit_github
from .collectors.sonarcloud import audit_sonarcloud
from .models import CheckResult, Finding
from .outcomes import ERROR, FINDINGS, OK, UNAVAILABLE, CheckOutcome


def fingerprint(source: str, target: str, check_name: str, key: str) -> str:
    """Empreinte stable d'un constat : la même alerte donne toujours la même empreinte."""
    return hashlib.sha256(f"{source}|{target}|{check_name}|{key}".encode()).hexdigest()


@transaction.atomic
def record_outcome(outcome: CheckOutcome) -> None:
    """Applique le résultat d'un contrôle au cycle de vie des constats.

    - constat revu : mis à jour ; rouvert s'il était résolu ; laissé tel quel s'il est ignoré ;
    - constat disparu : résolu, **uniquement si le contrôle a réellement abouti** (ok / findings) ;
    - contrôle indisponible ou en erreur : on ne touche à aucun constat (on ne sait pas).
    """
    now = timezone.now()
    CheckResult.objects.update_or_create(
        source=outcome.source,
        target=outcome.target,
        check_name=outcome.check_name,
        defaults={"status": outcome.status, "detail": outcome.detail[:300], "checked_at": now},
    )
    if outcome.status in (UNAVAILABLE, ERROR):
        return

    seen: set[str] = set()
    for obs in outcome.observations:
        fp = fingerprint(outcome.source, outcome.target, outcome.check_name, obs.key)
        seen.add(fp)
        finding, created = Finding.objects.get_or_create(
            fingerprint=fp,
            defaults={
                "source": outcome.source,
                "target": outcome.target,
                "check_name": outcome.check_name,
                "severity": obs.severity,
                "title": obs.title,
                "detail": obs.detail,
                "url": obs.url,
                "first_seen": now,
                "last_seen": now,
            },
        )
        if created:
            continue
        if obs.severity > finding.severity:
            finding.notified_at = None  # aggravé : à notifier de nouveau
        finding.severity, finding.title, finding.detail, finding.url = (
            obs.severity,
            obs.title,
            obs.detail,
            obs.url,
        )
        finding.last_seen = now
        if finding.status == Finding.Status.RESOLVED:
            # Revenu après avoir été résolu : c'est un nouvel événement, on re-notifie.
            finding.status, finding.resolved_at, finding.notified_at = Finding.Status.OPEN, None, None
        finding.save()

    if outcome.status in (OK, FINDINGS):
        Finding.objects.filter(
            source=outcome.source,
            target=outcome.target,
            check_name=outcome.check_name,
            status=Finding.Status.OPEN,
        ).exclude(fingerprint__in=seen).update(status=Finding.Status.RESOLVED, resolved_at=now)


def run_github_audit(client: GitHubClient | None = None) -> dict:
    client = client or GitHubClient(settings.GITHUB_TOKEN)
    outcomes = audit_github(client, settings.GITHUB_OWNER)
    for outcome in outcomes:
        record_outcome(outcome)
    return {
        "checks": len(outcomes),
        "by_status": dict(Counter(o.status for o in outcomes)),
        "rate_limited": any(o.rate_limited for o in outcomes),
    }


def run_vps_audit(*, fetch=None, report: dict | None = None) -> dict:
    """Audit du VPS. `report` évalue un rapport déjà obtenu (hors SSH) ; sinon il est récupéré par SSH.

    Un serveur injoignable est enregistré comme contrôle « vps_connection » en erreur : il apparaît comme non
    couvert et NE referme AUCUN constat existant (on ne sait pas, ce n'est pas « réglé »).
    """
    if report is None and not vps_runner.configured():
        return {"status": "not_configured"}
    target = settings.VPS_AUDIT_TARGET or settings.VPS_AUDIT_HOST or "vps"
    try:
        if report is None:
            report = vps_collector.parse_report((fetch or vps_runner.fetch_report)())
    except (vps_runner.VpsError, vps_collector.ReportError) as exc:
        record_outcome(CheckOutcome(vps_collector.SOURCE, target, "vps_connection", ERROR, detail=str(exc)[:300]))
        return {"status": "error", "error": str(exc)}
    outcomes = vps_collector.evaluate(report, target)
    outcomes.append(CheckOutcome(vps_collector.SOURCE, target, "vps_connection", OK))
    for outcome in outcomes:
        record_outcome(outcome)
    return {"status": "ok", "checks": len(outcomes), "by_status": dict(Counter(o.status for o in outcomes))}


def run_sonarcloud_audit(client: SonarCloudClient | None = None) -> dict:
    """Audit du code par SonarCloud. Un projet injoignable est « non couvert » et ne referme aucun constat."""
    projects = settings.SONARCLOUD_PROJECTS
    if not projects:
        return {"status": "not_configured"}
    client = client or SonarCloudClient(settings.SONARCLOUD_TOKEN)
    outcomes = audit_sonarcloud(client, projects)
    for outcome in outcomes:
        record_outcome(outcome)
    return {"status": "ok", "checks": len(outcomes), "by_status": dict(Counter(o.status for o in outcomes))}


def summary() -> dict:
    """Vue d'ensemble : constats ouverts par gravité + couverture des contrôles."""
    open_findings = Finding.objects.filter(status=Finding.Status.OPEN)
    by_severity = Counter(open_findings.values_list("severity", flat=True))
    coverage = Counter(CheckResult.objects.values_list("status", flat=True))
    return {
        "open_by_severity": {Finding.Severity(s).label: by_severity[s] for s in sorted(by_severity, reverse=True)},
        "open_total": sum(by_severity.values()),
        "coverage": dict(coverage),
        "not_covered": coverage[CheckResult.Status.UNAVAILABLE] + coverage[CheckResult.Status.ERROR],
    }

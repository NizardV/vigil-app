"""Collecteur GitHub : alertes (Dependabot, code scanning, secret scanning), protection de branche, posture.

Chaque contrôle renvoie un `CheckOutcome`. Un contrôle impossible (droits, quota, fonction désactivée)
est `unavailable` ou `error`, jamais `ok` : on ne déclare pas « rien à signaler » sans avoir regardé.
"""

import re
from collections.abc import Callable

from core.github import GitHubClient, GitHubError

from ..models import Finding
from ..outcomes import CheckOutcome, Observation
from .common import result_outcome, unavailable_outcome
from .posture import check_automerge_ready, check_stale_dependabot_prs

SAFE_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")  # évite d'injecter autre chose dans une URL

_SEVERITY = {
    "critical": Finding.Severity.CRITICAL,
    "high": Finding.Severity.HIGH,
    "error": Finding.Severity.HIGH,
    "medium": Finding.Severity.MEDIUM,
    "moderate": Finding.Severity.MEDIUM,
    "warning": Finding.Severity.MEDIUM,
    "low": Finding.Severity.LOW,
    "note": Finding.Severity.LOW,
}


def _severity(*levels: str | None) -> int:
    for level in levels:
        if level and level.lower() in _SEVERITY:
            return int(_SEVERITY[level.lower()])
    return int(Finding.Severity.MEDIUM)  # niveau inconnu : on préfère sur-estimer que masquer


def check_dependabot(client: GitHubClient, repo: dict) -> CheckOutcome:
    name = repo["full_name"]
    try:
        alerts = client.paginate(f"/repos/{name}/dependabot/alerts", {"state": "open"})
    except GitHubError as exc:
        return unavailable_outcome(name, "dependabot", exc)
    observations = []
    for alert in alerts:
        advisory = alert.get("security_advisory") or {}
        package = (alert.get("dependency") or {}).get("package") or {}
        observations.append(
            Observation(
                key=str(alert["number"]),
                severity=_severity(advisory.get("severity")),
                title=f"{package.get('name', 'dépendance')} : {advisory.get('summary', 'vulnérabilité')}"[:300],
                detail={
                    "package": package.get("name"),
                    "ecosystem": package.get("ecosystem"),
                    "cve": advisory.get("cve_id"),
                    "ghsa": advisory.get("ghsa_id"),
                    "manifest": (alert.get("dependency") or {}).get("manifest_path"),
                },
                url=alert.get("html_url", ""),
            )
        )
    return result_outcome(name, "dependabot", observations)


def check_code_scanning(client: GitHubClient, repo: dict) -> CheckOutcome:
    name = repo["full_name"]
    try:
        alerts = client.paginate(f"/repos/{name}/code-scanning/alerts", {"state": "open"})
    except GitHubError as exc:
        return unavailable_outcome(name, "code_scanning", exc)
    observations = []
    for alert in alerts:
        rule = alert.get("rule") or {}
        observations.append(
            Observation(
                key=str(alert["number"]),
                severity=_severity(rule.get("security_severity_level"), rule.get("severity")),
                title=(rule.get("description") or rule.get("id") or "alerte code scanning")[:300],
                detail={"rule": rule.get("id"), "tool": (alert.get("tool") or {}).get("name")},
                url=alert.get("html_url", ""),
            )
        )
    return result_outcome(name, "code_scanning", observations)


def check_secret_scanning(client: GitHubClient, repo: dict) -> CheckOutcome:
    name = repo["full_name"]
    try:
        alerts = client.paginate(f"/repos/{name}/secret-scanning/alerts", {"state": "open"})
    except GitHubError as exc:
        return unavailable_outcome(name, "secret_scanning", exc)
    observations = []
    for alert in alerts:
        # IMPORTANT : GitHub renvoie la valeur du secret dans `secret`. On ne la copie jamais.
        kind = alert.get("secret_type_display_name") or alert.get("secret_type") or "?"
        observations.append(
            Observation(
                key=str(alert["number"]),
                severity=int(Finding.Severity.CRITICAL),
                title=f"Secret exposé : {kind}"[:300],
                detail={"secret_type": alert.get("secret_type")},
                url=alert.get("html_url", ""),
            )
        )
    return result_outcome(name, "secret_scanning", observations)


def check_branch_protection(client: GitHubClient, repo: dict) -> CheckOutcome:
    name = repo["full_name"]
    branch = repo.get("default_branch") or "main"
    try:
        client.get(f"/repos/{name}/branches/{branch}/protection")
    except GitHubError as exc:
        if exc.status == 404 and "not protected" in exc.message.lower():
            return result_outcome(
                name,
                "branch_protection",
                [
                    Observation(
                        key=branch,
                        severity=int(Finding.Severity.LOW),
                        title=f"Branche par défaut « {branch} » non protégée",
                        detail={"branch": branch},
                        url=f"https://github.com/{name}/settings/branches",
                    )
                ],
            )
        return unavailable_outcome(name, "branch_protection", exc)
    return result_outcome(name, "branch_protection", [])


CHECKS: tuple[Callable[[GitHubClient, dict], CheckOutcome], ...] = (
    check_dependabot,
    check_code_scanning,
    check_secret_scanning,
    check_branch_protection,
    check_stale_dependabot_prs,
    check_automerge_ready,
)


def audit_github(client: GitHubClient, owner: str) -> list[CheckOutcome]:
    """Audite les dépôts du compte. S'arrête proprement si le quota est épuisé."""
    outcomes: list[CheckOutcome] = []
    for repo in client.list_owner_repos(owner):
        if repo.get("archived") or not SAFE_REPO.match(repo["full_name"]):
            continue
        for check in CHECKS:
            outcome = check(client, repo)
            outcomes.append(outcome)
            if outcome.rate_limited:
                return outcomes
    return outcomes

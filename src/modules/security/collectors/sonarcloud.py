"""Audit du code par SonarCloud : Quality Gate et problèmes de sécurité / fiabilité (lecture seule)."""

import urllib.parse
from typing import Any, Protocol

from core.sonarcloud import SonarCloudError

from ..models import Finding
from ..outcomes import ERROR, FINDINGS, OK, UNAVAILABLE, CheckOutcome, Observation

SOURCE = "sonarcloud"
S = Finding.Severity
SEVERITY = {"BLOCKER": S.CRITICAL, "HIGH": S.HIGH, "MEDIUM": S.MEDIUM, "LOW": S.LOW, "INFO": S.INFO}
QUALITIES = {"SECURITY", "RELIABILITY"}  # signalés quelle que soit la gravité
BROWSE = "https://sonarcloud.io/project/issues"
TITLE_LIMIT = 190


class Client(Protocol):
    def quality_gate(self, project: str) -> dict[str, Any]: ...
    def issues(self, project: str) -> list[dict[str, Any]]: ...


def _failure(project: str, check_name: str, exc: SonarCloudError) -> CheckOutcome:
    """Contrôle impossible : jamais « ok ». 404 = projet introuvable ou privé (indisponible), le reste = erreur."""
    status = UNAVAILABLE if exc.status in (401, 403, 404) else ERROR
    return CheckOutcome(SOURCE, project, check_name, status, detail=f"{exc.status}: {exc.message}"[:300])


def _worst_impact(issue: dict[str, Any]) -> tuple[str, int] | None:
    """(qualité, gravité) de l'impact le plus grave du problème, ou None s'il n'en a pas."""
    best: tuple[str, int] | None = None
    for impact in issue.get("impacts") or []:
        severity = SEVERITY.get(str(impact.get("severity")))
        if severity is not None and (best is None or severity > best[1]):
            best = (str(impact.get("softwareQuality")), int(severity))
    return best


def _title(issue: dict[str, Any], project: str) -> str:
    path = str(issue.get("component", "")).removeprefix(f"{project}:")
    line = issue.get("line")
    where = f"{path}:{line}" if line else path
    return f"{where} · {issue.get('message', '')}".strip(" ·")[:TITLE_LIMIT]


def check_quality_gate(client: Client, project: str) -> CheckOutcome:
    try:
        gate = client.quality_gate(project)
    except SonarCloudError as exc:
        return _failure(project, "sonar_quality_gate", exc)
    status = gate.get("status")
    if status == "ERROR":
        failing = sorted({str(c.get("metricKey")) for c in gate.get("conditions", []) if c.get("status") == "ERROR"})
        title = "Quality Gate SonarCloud en échec" + (f" ({', '.join(failing)})" if failing else "")
        obs = Observation(
            "gate", S.HIGH, title[:TITLE_LIMIT], url=f"https://sonarcloud.io/summary/overall?id={project}"
        )
        return CheckOutcome(SOURCE, project, "sonar_quality_gate", FINDINGS, [obs])
    if status in ("OK", "WARN"):
        return CheckOutcome(SOURCE, project, "sonar_quality_gate", OK)
    return CheckOutcome(SOURCE, project, "sonar_quality_gate", UNAVAILABLE, detail="aucune analyse disponible")


def check_issues(client: Client, project: str) -> CheckOutcome:
    try:
        issues = client.issues(project)
    except SonarCloudError as exc:
        return _failure(project, "sonar_issues", exc)
    observations = []
    for issue in issues:
        impact = _worst_impact(issue)
        if impact is None:
            continue
        quality, severity = impact
        if quality not in QUALITIES:
            if severity < S.HIGH:
                continue  # la maintenabilité mineure reste visible dans SonarCloud, pas dans Vigil
            severity = S.MEDIUM  # comptée dans le résumé, mais jamais d'alerte : ce n'est pas de la sécurité
        key = str(issue["key"])
        query = urllib.parse.urlencode({"id": project, "issues": key, "open": key})
        observations.append(
            Observation(
                key,
                S(severity),
                _title(issue, project),
                detail={"rule": issue.get("rule"), "quality": quality},
                url=f"{BROWSE}?{query}",
            )
        )
    return CheckOutcome(SOURCE, project, "sonar_issues", FINDINGS if observations else OK, observations)


def audit_sonarcloud(client: Client, projects: list[str]) -> list[CheckOutcome]:
    return [check(client, project) for project in projects for check in (check_quality_gate, check_issues)]

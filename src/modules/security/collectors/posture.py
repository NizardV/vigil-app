"""Contrôles de « posture » de remédiation Dependabot : PR de correction qui traînent, auto-merge prêt ou non.

Contexte : Dependabot ouvre déjà les PR de correction ; le risque est qu'elles ne soient jamais fusionnées
(des failles critiques sont restées ouvertes ainsi). Ces contrôles sont en lecture seule.

Règle d'or : si on ne peut pas établir un fait (droits, plan gratuit, erreur), le contrôle est `unavailable`,
jamais « tout va bien ».
"""

import base64
import re
import urllib.parse
from datetime import UTC, datetime

from core.github import GitHubClient, GitHubError

from ..models import Finding
from ..outcomes import UNAVAILABLE, CheckOutcome, Observation
from .common import SOURCE, result_outcome, unavailable_outcome

DEPENDABOT = "dependabot[bot]"
STALE_DAYS = 14  # au-delà, une PR de correction est considérée comme « qui traîne »
VERY_STALE_DAYS = 30
WORKFLOWS_DIR = ".github/workflows/"
# Imprécision connue : un `pull_request` cité dans un commentaire ou une condition compte comme CI sur PR.
_PR_TRIGGER = re.compile(r"\bpull_request(_target)?\b")


def _age_days(iso: str) -> int:
    created = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(UTC) - created).days


def _open_dependabot_prs(client: GitHubClient, name: str) -> list[dict]:
    pulls = client.paginate(f"/repos/{name}/pulls", {"state": "open"})
    return [p for p in pulls if (p.get("user") or {}).get("login") == DEPENDABOT]


def check_stale_dependabot_prs(client: GitHubClient, repo: dict) -> CheckOutcome:
    """PR Dependabot ouvertes depuis trop longtemps : le symptôme observé sur Portfolio et cyna-web."""
    name = repo["full_name"]
    try:
        prs = _open_dependabot_prs(client, name)
    except GitHubError as exc:
        return unavailable_outcome(name, "stale_dependabot_prs", exc)
    observations = []
    for pr in prs:
        age = _age_days(pr["created_at"])
        if age < STALE_DAYS:
            continue
        severity = Finding.Severity.MEDIUM if age >= VERY_STALE_DAYS else Finding.Severity.LOW
        observations.append(
            Observation(
                key=str(pr["number"]),
                severity=int(severity),
                title=f"PR Dependabot #{pr['number']} en attente depuis {age} j : {pr.get('title', '')}"[:300],
                detail={"number": pr["number"], "age_days": age},
                url=pr.get("html_url", ""),
            )
        )
    return result_outcome(name, "stale_dependabot_prs", observations)


def _workflow_texts(client: GitHubClient, name: str) -> list[str]:
    """Contenu des workflows du dépôt ([] si le dossier n'existe pas). Lève GitHubError sinon."""
    try:
        entries = client.get(f"/repos/{name}/contents/.github/workflows")
    except GitHubError as exc:
        if exc.status == 404:  # pas de dossier de workflows : aucune CI
            return []
        raise
    texts = []
    for entry in entries if isinstance(entries, list) else []:
        path = entry.get("path", "")
        if entry.get("type") != "file" or not path.startswith(WORKFLOWS_DIR):
            continue
        data = client.get(f"/repos/{name}/contents/{urllib.parse.quote(path)}")
        if data.get("encoding") == "base64":
            texts.append(base64.b64decode(data["content"]).decode("utf-8", errors="replace"))
    return texts


def _required_checks(client: GitHubClient, name: str, branch: str) -> bool | None:
    """La branche exige-t-elle des contrôles d'état (protection ou ruleset) ? None = indéterminé."""
    verdicts: list[bool | None] = []
    try:
        data = client.get(f"/repos/{name}/branches/{branch}/protection/required_status_checks")
        if data.get("contexts") or data.get("checks"):
            return True
        verdicts.append(False)
    except GitHubError as exc:
        verdicts.append(False if exc.status == 404 else None)
    try:
        rules = client.get(f"/repos/{name}/rules/branches/{branch}")
        if any(rule.get("type") == "required_status_checks" for rule in rules):
            return True
        verdicts.append(False)
    except GitHubError as exc:
        verdicts.append(False if exc.status == 404 else None)
    return False if all(v is False for v in verdicts) else None


def check_automerge_ready(client: GitHubClient, repo: dict) -> CheckOutcome:
    """L'auto-merge des PR Dependabot est-il sûr à activer, et l'est-il déjà de façon sûre ?

    Danger principal : l'auto-merge de GitHub fusionne IMMÉDIATEMENT si la branche n'exige pas de contrôle
    d'état. Auto-merge actif sans contrôle obligatoire = fusion sans attendre la CI.
    """
    name = repo["full_name"]
    branch = repo.get("default_branch") or "main"
    try:
        details = client.get(f"/repos/{name}")
        texts = _workflow_texts(client, name)
        has_open_prs = bool(_open_dependabot_prs(client, name))
        required = _required_checks(client, name, branch)
    except GitHubError as exc:
        return unavailable_outcome(name, "automerge_ready", exc)

    allow_auto_merge = details.get("allow_auto_merge")
    if required is None or allow_auto_merge is None:
        unknown = [
            label
            for label, v in (("contrôles obligatoires", required), ("option auto-merge", allow_auto_merge))
            if v is None
        ]
        return CheckOutcome(
            SOURCE,
            name,
            "automerge_ready",
            UNAVAILABLE,
            detail=f"Indéterminé : {', '.join(unknown)} (droits ou offre)",
        )

    has_pr_ci = any(_PR_TRIGGER.search(text) for text in texts)
    has_workflow = any("dependabot/fetch-metadata" in text and "--auto" in text for text in texts)

    observations = []
    if allow_auto_merge and has_workflow and not required:
        observations.append(
            Observation(
                key="unsafe_automerge",
                severity=int(Finding.Severity.HIGH),
                title="Auto-merge actif SANS contrôle obligatoire : les PR fusionnent sans attendre la CI",
                detail={"branch": branch},
                url=f"https://github.com/{name}/settings/branches",
            )
        )
    gaps = [
        label
        for ok, label in (
            (has_pr_ci, "pas de CI sur les PR"),
            (required, "branche sans contrôle obligatoire"),
            (allow_auto_merge, "auto-merge non autorisé"),
            (has_workflow, "pas de workflow d'auto-merge"),
        )
        if not ok
    ]
    if gaps:
        severity = Finding.Severity.LOW if has_open_prs else Finding.Severity.INFO
        observations.append(
            Observation(
                key="readiness",
                severity=int(severity),
                title=f"Auto-merge Dependabot non prêt : {', '.join(gaps)}"[:300],
                detail={"gaps": gaps, "open_dependabot_prs": has_open_prs},
                url=f"https://github.com/{name}/settings",
            )
        )
    return result_outcome(name, "automerge_ready", observations)

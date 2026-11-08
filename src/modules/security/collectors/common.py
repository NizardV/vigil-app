"""Éléments partagés par les collecteurs GitHub (évite les imports circulaires)."""

from core.github import GitHubError

from ..outcomes import ERROR, FINDINGS, OK, UNAVAILABLE, CheckOutcome, Observation

SOURCE = "github"


def unavailable_outcome(repo: str, check_name: str, exc: GitHubError) -> CheckOutcome:
    """Contrôle impossible : jamais « ok ». Erreur dure (réseau, 5xx, quota) ou simple indisponibilité."""
    hard_failure = exc.status == 0 or exc.status >= 500 or exc.rate_limited
    return CheckOutcome(
        SOURCE,
        repo,
        check_name,
        ERROR if hard_failure else UNAVAILABLE,
        detail=f"{exc.status}: {exc.message}"[:300],
        rate_limited=exc.rate_limited,
    )


def result_outcome(repo: str, check_name: str, observations: list[Observation]) -> CheckOutcome:
    return CheckOutcome(SOURCE, repo, check_name, FINDINGS if observations else OK, observations)

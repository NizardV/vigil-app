"""Structures échangées entre les collecteurs et le service (aucune dépendance Django)."""

from dataclasses import dataclass, field

# Statuts d'un contrôle. Seuls OK et FINDINGS prouvent que la cible a bien été examinée.
OK = "ok"
FINDINGS = "findings"
UNAVAILABLE = "unavailable"  # droits insuffisants ou fonction désactivée : on ne sait pas
ERROR = "error"  # réseau, 5xx, quota : on ne sait pas


@dataclass(frozen=True)
class Observation:
    """Un constat brut. `key` identifie le constat de façon stable (ex. numéro d'alerte)."""

    key: str
    severity: int
    title: str
    detail: dict = field(default_factory=dict)
    url: str = ""


@dataclass
class CheckOutcome:
    source: str
    target: str
    check_name: str
    status: str
    observations: list[Observation] = field(default_factory=list)
    detail: str = ""
    rate_limited: bool = False

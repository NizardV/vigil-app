"""Tableau de bord de l'admin (page d'accueil) : vue d'ensemble de la sécurité.

`config` peut dépendre des modules (sens config → modules → core) ; on ne passe que par leurs services publics.
"""

import json

from django.urls import reverse

from modules.security import services as security

# Couleurs des gravités, de la plus grave à la moins grave (mêmes teintes que les pastilles des listes).
SEVERITY_COLORS = {
    "Critique": "#dc2626",
    "Haute": "#f97316",
    "Moyenne": "#eab308",
    "Faible": "#22c55e",
    "Info": "#64748b",
}
SOURCE_LABELS = {"github": "GitHub", "vps": "Serveur (VPS)", "sonarcloud": "Code (SonarCloud)"}


def _bar_chart(labels: list[str], values: list[int], colors: list[str]) -> str:
    # unfold dessine des barres de 4 px par défaut (graphes de tendance) : ici, des barres pleines.
    dataset = {
        "label": "Constats ouverts",
        "data": values,
        "backgroundColor": colors,
        "maxBarThickness": 56,
        "borderRadius": 6,
    }
    return json.dumps({"labels": labels, "datasets": [dataset]})


def dashboard_callback(request, context: dict) -> dict:
    report = security.summary()
    findings_url = reverse("admin:security_finding_changelist")
    severity = report["open_by_severity"]
    by_source = report["open_by_source"]
    critical_high = severity.get("Critique", 0) + severity.get("Haute", 0)

    context.update(
        {
            "kpis": [
                {
                    "title": "Constats ouverts",
                    "value": report["open_total"],
                    "href": f"{findings_url}?status__exact=open",
                },
                {
                    "title": "Critiques et hauts",
                    "value": critical_high,
                    "href": f"{findings_url}?status__exact=open&severity__gte=3",
                    "alert": critical_high > 0,
                },
                {
                    "title": "Contrôles non couverts",
                    "value": report["not_covered"],
                    "href": reverse("admin:security_checkresult_changelist") + "?status__in=unavailable,error",
                },
            ],
            "severity_chart": _bar_chart(
                list(severity), list(severity.values()), [SEVERITY_COLORS.get(s, "#64748b") for s in severity]
            ),
            "source_chart": _bar_chart(
                [SOURCE_LABELS.get(s, s) for s in by_source], list(by_source.values()), ["#22d3ee"] * len(by_source)
            ),
            "has_findings": report["open_total"] > 0,
        }
    )
    return context

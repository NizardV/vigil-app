"""Notifications Discord du module security.

Deux messages :
- **alerte** : envoyée une seule fois par constat critique/haut (nouveau, rouvert ou aggravé) ;
- **résumé hebdomadaire** : état d'ensemble, PR Dependabot qui traînent, et santé de l'audit lui-même.

Garanties : un échec d'envoi ne marque rien comme notifié (on réessaiera) ; aucun message n'est envoyé
deux fois ; le texte venant de l'extérieur (titres de PR, advisories) est neutralisé ; `dry_run` permet
de prévisualiser.
"""

from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from django.db.models import Max
from django.utils import timezone

from core.notifications import NotificationError, escape, send_discord, webhook_url

from .models import CheckResult, Finding

ALERT_MIN_SEVERITY = Finding.Severity.HIGH
MAX_LISTED = 10  # lignes détaillées par message ; le reste est résumé par un compteur
STALE_AUDIT = timedelta(hours=48)
COLOR = {Finding.Severity.CRITICAL: 0xB91C1C, Finding.Severity.HIGH: 0xEA580C}
NEUTRAL_COLOR = 0x2563EB
DESCRIPTION_LIMIT = 3800  # l'API Discord plafonne la description d'un embed à 4096 caractères

Sender = Callable[[str, dict[str, Any]], None]


def group_findings(findings: list[Finding]) -> list[tuple[Finding, int]]:
    """Regroupe les constats identiques (même gravité, même projet, même titre), en gardant l'ordre.

    Typiquement : une même faille signalée dans plusieurs fichiers de dépendances d'un dépôt. Le lien
    affiché est celui du premier ; le compteur dit combien d'alertes GitHub se cachent derrière.
    """
    groups: dict[tuple[int, str, str], list[Finding]] = {}
    for finding in findings:
        groups.setdefault((finding.severity, finding.target, finding.title), []).append(finding)
    return [(members[0], len(members)) for members in groups.values()]


def _line(finding: Finding, count: int = 1) -> str:
    title = escape(finding.title, 110)
    target = escape(finding.target.split("/")[-1], 40)
    text = f"[{title}]({finding.url})" if finding.url.startswith("https://") else title
    times = f" ×{count}" if count > 1 else ""
    return f"**{finding.get_severity_display()}** · `{target}` · {text}{times}"


def _cap(lines: list[str]) -> str:
    out, size = [], 0
    for line in lines:
        if size + len(line) + 1 > DESCRIPTION_LIMIT:
            break
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def pending_alerts():
    return Finding.objects.filter(
        status=Finding.Status.OPEN, severity__gte=ALERT_MIN_SEVERITY, notified_at__isnull=True
    ).order_by("-severity", "target", "title")


def alert_payload(findings: list[Finding]) -> dict[str, Any]:
    worst = max(f.severity for f in findings)
    counts = Counter(f.get_severity_display() for f in findings)
    summary = ", ".join(f"{n} {label.lower()}" for label, n in counts.most_common())
    groups = group_findings(findings)
    lines = [_line(f, n) for f, n in groups[:MAX_LISTED]]
    hidden = sum(n for _, n in groups[MAX_LISTED:])  # en constats réels, pas en lignes
    if hidden:
        lines.append(f"… et **{hidden}** autre(s) constat(s) : voir l'admin Vigil.")
    return {
        "embeds": [
            {
                "title": f"🚨 {len(findings)} constat(s) de sécurité à traiter",
                "description": _cap([f"_{summary}_", "", *lines]),
                "color": COLOR.get(worst, NEUTRAL_COLOR),
                "footer": {"text": "Vigil Security"},
            }
        ]
    }


def notify_new_findings(*, send: Sender = send_discord, dry_run: bool = False) -> dict[str, Any]:
    """Envoie une alerte groupée pour les constats critiques/hauts jamais notifiés."""
    pending = list(pending_alerts())
    if not pending:
        return {"status": "nothing", "count": 0}
    payload = alert_payload(pending)
    if dry_run:
        return {"status": "dry_run", "count": len(pending), "payload": payload}
    url = webhook_url("security-alerts")
    if not url:
        return {
            "status": "not_configured",
            "count": len(pending),
        }  # rien n'est marqué : ce sera envoyé plus tard
    try:
        send(url, payload)
    except NotificationError as exc:
        return {"status": "error", "count": len(pending), "error": str(exc)}
    Finding.objects.filter(pk__in=[f.pk for f in pending]).update(notified_at=timezone.now())
    return {"status": "sent", "count": len(pending)}


def digest_payload(now=None) -> dict[str, Any]:
    now = now or timezone.now()
    open_findings = Finding.objects.filter(status=Finding.Status.OPEN)
    by_severity = Counter(open_findings.values_list("severity", flat=True))
    serious = list(open_findings.filter(severity__gte=ALERT_MIN_SEVERITY).order_by("-severity", "target", "title"))
    top = group_findings(serious)[:5]
    stale = Counter(
        open_findings.filter(check_name="stale_dependabot_prs").values_list("target", flat=True)
    ).most_common(5)
    not_covered = CheckResult.objects.filter(
        status__in=[CheckResult.Status.UNAVAILABLE, CheckResult.Status.ERROR]
    ).count()
    last_audit = CheckResult.objects.aggregate(last=Max("checked_at"))["last"]

    audit_stale = last_audit is None or now - last_audit > STALE_AUDIT
    health = (
        "⚠️ **L'audit n'a pas tourné depuis plus de 48 h** (ou jamais). Les chiffres peuvent être périmés."
        if audit_stale
        else f"Dernier audit : {timezone.localtime(last_audit):%d/%m %H:%M}"
    )
    severities = " · ".join(
        f"{Finding.Severity(s).label} **{by_severity[s]}**" for s in sorted(by_severity, reverse=True)
    )
    lines = [health, "", f"**Constats ouverts : {sum(by_severity.values())}**", severities or "_aucun_"]
    if top:
        lines += ["", "**À traiter en priorité**", *[_line(f, n) for f, n in top]]
    if stale:
        lines += ["", "**PR Dependabot qui traînent**"]
        lines += [f"`{escape(t.split('/')[-1], 40)}` · {n} PR en attente" for t, n in stale]
    if not_covered:
        lines += ["", f"_{not_covered} contrôle(s) non couvert(s) (droits, fonction désactivée ou erreur)._"]
    return {
        "embeds": [
            {
                "title": "🛡️ Vigil Security — résumé hebdomadaire",
                "description": _cap(lines),
                "color": 0xEA580C if audit_stale else NEUTRAL_COLOR,
                "footer": {"text": "Vigil Security"},
            }
        ]
    }


def send_digest(*, send: Sender = send_discord, dry_run: bool = False) -> dict[str, Any]:
    payload = digest_payload()
    if dry_run:
        return {"status": "dry_run", "payload": payload}
    url = webhook_url("security-digest")
    if not url:
        return {"status": "not_configured"}
    try:
        send(url, payload)
    except NotificationError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "sent"}

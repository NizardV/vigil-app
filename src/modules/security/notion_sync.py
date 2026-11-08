"""Synchro de l'état de sécurité vers la page Notion « Vigil Security ».

Règle de sécurité : Vigil ne modifie QUE la zone située sous un titre repère. Tout ce qui est
au-dessus (écrit à la main) n'est jamais modifié ni supprimé. Si le repère est absent, il est ajouté
en fin de page sans rien supprimer. L'ordre « ajouter les nouveaux blocs puis supprimer les anciens »
garantit qu'une panne en cours de route
ne laisse jamais la page vide ni n'efface la version précédente.
"""

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any

from django.conf import settings
from django.utils import timezone

from core.notion import NotionClient, NotionError, valid_id

from .models import CheckResult, Finding
from .notifications import ALERT_MIN_SEVERITY, STALE_AUDIT, group_findings

MARKER = "Généré par Vigil — ne pas éditer en dessous"
MAX_TEXT = 1900  # l'API Notion limite un segment de texte à 2 000 caractères
MAX_DELETE = 300  # garde-fou : au-delà, on suspecte une erreur de repérage et on refuse de supprimer
MAX_LISTED = 10


# --- Fabrication des blocs Notion -------------------------------------------------------------------------


def _text(content: str, *, url: str = "", bold: bool = False) -> dict[str, Any]:
    text: dict[str, Any] = {"content": content[:MAX_TEXT]}
    if url.startswith("https://"):  # jamais de lien non https issu de données externes
        text["link"] = {"url": url}
    item: dict[str, Any] = {"type": "text", "text": text}
    if bold:
        item["annotations"] = {"bold": True}
    return item


def _block(kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"object": "block", "type": kind, kind: {"rich_text": items}}


def heading(text: str, level: int = 3) -> dict[str, Any]:
    return _block(f"heading_{level}", [_text(text)])


def paragraph(*items: dict[str, Any]) -> dict[str, Any]:
    return _block("paragraph", list(items))


def bullet(*items: dict[str, Any]) -> dict[str, Any]:
    return _block("bulleted_list_item", list(items))


# --- Contenu ----------------------------------------------------------------------------------------------


def build_blocks(now: datetime | None = None) -> list[dict[str, Any]]:
    """Blocs de la zone générée, repère compris (premier bloc)."""
    now = now or timezone.now()
    open_findings = Finding.objects.filter(status=Finding.Status.OPEN)
    by_severity = Counter(open_findings.values_list("severity", flat=True))
    serious = list(open_findings.filter(severity__gte=ALERT_MIN_SEVERITY).order_by("-severity", "target", "title"))
    stale = Counter(open_findings.filter(check_name="stale_dependabot_prs").values_list("target", flat=True))
    uncovered = CheckResult.objects.filter(status__in=[CheckResult.Status.UNAVAILABLE, CheckResult.Status.ERROR])
    reasons = Counter(uncovered.values_list("detail", flat=True))
    last_audit = CheckResult.objects.order_by("-checked_at").values_list("checked_at", flat=True).first()

    stale_audit = last_audit is None or now - last_audit > STALE_AUDIT
    audit = (
        "⚠️ L'audit n'a pas tourné depuis plus de 48 h (ou jamais) : ces chiffres peuvent être périmés."
        if stale_audit
        else f"Dernier audit : {timezone.localtime(last_audit):%d/%m/%Y %H:%M} (heure de Paris)"
    )
    blocks = [
        heading(MARKER, level=2),
        paragraph(_text(audit)),
        paragraph(_text(f"Synchronisé le {timezone.localtime(now):%d/%m/%Y à %H:%M}.")),
        heading("Constats ouverts"),
    ]
    if by_severity:
        blocks += [
            bullet(_text(f"{Finding.Severity(s).label} : ", bold=True), _text(str(by_severity[s])))
            for s in sorted(by_severity, reverse=True)
        ]
    else:
        blocks.append(paragraph(_text("Aucun constat ouvert.")))

    if serious:
        groups = group_findings(serious)
        blocks.append(heading("À traiter en priorité"))
        for finding, count in groups[:MAX_LISTED]:
            times = f" ×{count}" if count > 1 else ""
            blocks.append(
                bullet(
                    _text(f"{finding.get_severity_display()} · {finding.target.split('/')[-1]} · ", bold=True),
                    _text(f"{finding.title}{times}", url=finding.url),
                )
            )
        hidden = sum(n for _, n in groups[MAX_LISTED:])
        if hidden:
            blocks.append(paragraph(_text(f"… et {hidden} autre(s) constat(s) critique(s) ou haut(s).")))

    if stale:
        blocks.append(heading("PR Dependabot qui traînent"))
        blocks += [
            bullet(_text(f"{target.split('/')[-1]} : ", bold=True), _text(f"{n} PR en attente"))
            for target, n in stale.most_common(MAX_LISTED)
        ]

    if uncovered.exists():
        blocks.append(heading("Contrôles non couverts"))
        blocks.append(
            paragraph(_text(f"{uncovered.count()} contrôle(s) n'ont pas pu être exécutés (pas « sans problème »)."))
        )
        blocks += [bullet(_text(f"{n} × {reason or 'raison inconnue'}")) for reason, n in reasons.most_common(5)]
    return blocks


def render_text(blocks: list[dict[str, Any]]) -> str:
    """Rendu texte d'un lot de blocs (aperçu en ligne de commande)."""
    lines = []
    for block in blocks:
        kind = block["type"]
        text = "".join(item["text"]["content"] for item in block[kind]["rich_text"])
        prefix = {"heading_2": "## ", "heading_3": "### ", "bulleted_list_item": "  • "}.get(kind, "")
        lines.append(f"{prefix}{text}")
    return "\n".join(lines)


# --- Écriture dans Notion ---------------------------------------------------------------------------------


def _plain_text(block: dict[str, Any]) -> str:
    content = block.get(block.get("type", ""), {})
    return "".join(item.get("plain_text", "") for item in content.get("rich_text", []))


def _is_marker(block: dict[str, Any]) -> bool:
    return block.get("type") == "heading_2" and _plain_text(block) == MARKER


def sync_page(client: NotionClient, page_id: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """Écrit `blocks` (repère en tête) dans la page sans toucher au contenu situé au-dessus du repère."""
    children = client.list_children(page_id)
    marker_index = next((i for i, block in enumerate(children) if _is_marker(block)), None)
    if marker_index is None:
        client.append_children(page_id, blocks)  # repère absent : on ajoute tout en fin de page, rien n'est supprimé
        return {"status": "synced", "created_marker": True, "added": len(blocks), "deleted": 0}

    old = children[marker_index + 1 :]
    if len(old) > MAX_DELETE:
        raise NotionError(0, f"refus de supprimer {len(old)} blocs sous le repère (garde-fou à {MAX_DELETE})")
    client.append_children(page_id, blocks[1:])  # d'abord ajouter (le repère existe déjà)…
    for block in old:  # …puis seulement retirer l'ancienne version
        client.delete_block(block["id"])
    return {"status": "synced", "created_marker": False, "added": len(blocks) - 1, "deleted": len(old)}


def run_notion_sync(*, client: NotionClient | None = None, dry_run: bool = False) -> dict[str, Any]:
    page_id = settings.NOTION_SECURITY_PAGE_ID
    if not dry_run and not (settings.NOTION_API_KEY and valid_id(page_id)):
        return {"status": "not_configured"}
    blocks = build_blocks()
    if dry_run:
        return {"status": "dry_run", "blocks": blocks}
    try:
        return sync_page(client or NotionClient(settings.NOTION_API_KEY), page_id, blocks)
    except NotionError as exc:
        return {"status": "error", "error": str(exc)}


Sender = Callable[..., Any]

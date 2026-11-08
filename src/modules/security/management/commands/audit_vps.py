import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from modules.security import services
from modules.security.collectors import vps as vps_collector
from modules.security.models import Finding


class Command(BaseCommand):
    help = "Audit du VPS (lecture seule) : par SSH, ou --from-file pour évaluer un rapport JSON déjà obtenu."

    def add_arguments(self, parser):
        parser.add_argument("--from-file", help="évalue un rapport JSON local au lieu de se connecter en SSH")
        parser.add_argument("--dry-run", action="store_true", help="affiche le résultat sans rien enregistrer")

    def handle(self, *args, **options):
        report = None
        if options["from_file"]:
            try:
                report = vps_collector.parse_report(Path(options["from_file"]).read_text(encoding="utf-8"))
            except (OSError, vps_collector.ReportError) as exc:
                raise CommandError(f"Rapport illisible : {exc}") from exc
        if options["dry_run"]:
            if report is None:
                raise CommandError("--dry-run demande --from-file (aucune connexion SSH n'est faite en simulation)")
            self._print(report)
            return
        result = services.run_vps_audit(report=report)
        if result["status"] == "not_configured":
            self.stdout.write(self.style.WARNING("VPS_AUDIT_HOST / _SSH_KEY / _KNOWN_HOSTS non définis : rien fait."))
        elif result["status"] == "error":
            raise CommandError(f"Audit impossible, aucun constat modifié : {result['error']}")
        else:
            self.stdout.write(self.style.SUCCESS(f"{result['checks']} contrôles : {result['by_status']}"))

    def _print(self, report):
        """Aperçu sans écriture : un rollback garantit qu'aucun constat n'est enregistré."""
        encoding = sys.stdout.encoding or "ascii"

        def out(text):
            self.stdout.write(text.encode(encoding, "replace").decode(encoding))

        outcomes = vps_collector.evaluate(report, "apercu")
        for outcome in outcomes:
            if outcome.status == "unavailable":
                out(f"  NON COUVERT  {outcome.check_name}: {outcome.detail}")
            for obs in outcome.observations:
                out(f"  [{Finding.Severity(obs.severity).label:8}] {outcome.check_name}: {obs.title}")
            if outcome.status == "ok":
                out(f"  ok           {outcome.check_name}")
        with transaction.atomic():
            transaction.set_rollback(True)
        out(json.dumps({"checks": len(outcomes)}))

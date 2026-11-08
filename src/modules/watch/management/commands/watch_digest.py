import json
import sys

from django.core.management.base import BaseCommand, CommandError

from modules.watch import services


class Command(BaseCommand):
    help = "Envoie les résumés de veille dus (heure de Paris)."

    def add_arguments(self, parser):
        parser.add_argument("--theme", help="slug d'un thème précis")
        parser.add_argument("--force", action="store_true", help="avec --theme : ignore l'heure et le « déjà envoyé »")
        parser.add_argument("--dry-run", action="store_true", help="affiche les messages sans rien envoyer ni marquer")

    def handle(self, *args, **options):
        result = services.run_digests(only=options["theme"], force=options["force"], dry_run=options["dry_run"])
        encoding = sys.stdout.encoding or "ascii"
        text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        self.stdout.write(text.encode(encoding, "replace").decode(encoding))
        if result["status"] == "error":
            raise CommandError("Au moins un résumé n'a pas pu être envoyé.")

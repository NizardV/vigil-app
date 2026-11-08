import sys

from django.core.management.base import BaseCommand, CommandError

from modules.security import notion_sync


class Command(BaseCommand):
    help = "Synchronise l'état de sécurité vers la page Notion « Vigil Security » (zone générée uniquement)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="affiche ce qui serait écrit, sans appeler Notion")

    def handle(self, *args, **options):
        result = notion_sync.run_notion_sync(dry_run=options["dry_run"])
        status = result["status"]
        if status == "dry_run":
            # On ne remplace que ce que la console ne sait pas afficher (emojis sur cp1252).
            text = notion_sync.render_text(result["blocks"])
            encoding = sys.stdout.encoding or "ascii"
            self.stdout.write(text.encode(encoding, "replace").decode(encoding))
        elif status == "error":
            raise CommandError(f"Synchro impossible, la page n'a pas été vidée : {result['error']}")
        elif status == "not_configured":
            self.stdout.write(self.style.WARNING("NOTION_API_KEY ou NOTION_SECURITY_PAGE_ID manquant : rien écrit."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Page Notion mise à jour (+{result['added']} / -{result['deleted']} blocs).")
            )

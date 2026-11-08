import json

from django.core.management.base import BaseCommand, CommandError

from modules.projects import notifications


class Command(BaseCommand):
    help = "Envoie le résumé d'activité des projets (commits et PR fusionnées, bruit écarté) sur Discord."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7, help="durée de la période (défaut : 7 jours)")
        parser.add_argument("--dry-run", action="store_true", help="affiche le message sans l'envoyer")

    def handle(self, *args, **options):
        result = notifications.send_weekly_digest(days=options["days"], dry_run=options["dry_run"])
        status = result["status"]
        if status == "dry_run":
            # ASCII échappé : la console Windows (cp1252) ne sait pas afficher les emojis du message.
            self.stdout.write(json.dumps(result["payload"], ensure_ascii=True, indent=2))
        elif status == "error":
            raise CommandError(f"Envoi impossible : {result['error']}")
        elif status == "not_configured":
            self.stdout.write(self.style.WARNING("DISCORD_WEBHOOK_PROJECTS_DIGEST non défini : rien envoyé."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Résumé envoyé ({result['active']} projet(s) actif(s))."))

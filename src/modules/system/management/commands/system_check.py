import json

from django.core.management.base import BaseCommand, CommandError

from modules.system import services


class Command(BaseCommand):
    help = "Vérifie la santé de Vigil (tâches en échec, bloquées, en retard) et alerte sur Discord."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="affiche le message sans l'envoyer ni rien marquer")

    def handle(self, *args, **options):
        result = services.check_and_notify(dry_run=options["dry_run"])
        status = result["status"]
        if status == "dry_run":
            # ASCII échappé : la console Windows (cp1252) ne sait pas afficher les emojis du message.
            self.stdout.write(json.dumps(result["payload"], ensure_ascii=True, indent=2))
        elif status == "error":
            raise CommandError(f"Envoi impossible, rien n'est marqué : {result['error']}")
        elif status == "not_configured":
            self.stdout.write(self.style.WARNING("DISCORD_WEBHOOK_SYSTEM non défini : rien envoyé."))
        elif status == "nothing":
            self.stdout.write(
                self.style.SUCCESS(f"Rien de nouveau à signaler ({result['problems']} problème(s) connus).")
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"{result['new']} problème(s) signalé(s)."))

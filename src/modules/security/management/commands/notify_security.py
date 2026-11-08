import json

from django.core.management.base import BaseCommand, CommandError

from modules.security import notifications


class Command(BaseCommand):
    help = "Envoie l'alerte (nouveaux constats critiques/hauts) ou, avec --digest, le résumé hebdomadaire."

    def add_arguments(self, parser):
        parser.add_argument("--digest", action="store_true", help="résumé hebdomadaire au lieu de l'alerte")
        parser.add_argument(
            "--dry-run", action="store_true", help="affiche le message sans l'envoyer ni rien marquer notifié"
        )

    def handle(self, *args, **options):
        action = notifications.send_digest if options["digest"] else notifications.notify_new_findings
        result = action(dry_run=options["dry_run"])
        status = result["status"]
        if status == "dry_run":
            # ASCII échappé : la console Windows (cp1252) ne sait pas afficher les emojis du message.
            self.stdout.write(json.dumps(result["payload"], ensure_ascii=True, indent=2))
        elif status == "error":
            raise CommandError(f"Envoi impossible, rien n'est marqué notifié : {result['error']}")
        elif status == "not_configured":
            self.stdout.write(
                self.style.WARNING("Webhook du salon non configuré (DISCORD_WEBHOOK_SECURITY_*) : rien envoyé.")
            )
        elif status == "nothing":
            self.stdout.write("Aucun nouveau constat critique ou haut à notifier.")
        else:
            self.stdout.write(self.style.SUCCESS(f"Notification envoyée ({result.get('count', 'résumé')})."))

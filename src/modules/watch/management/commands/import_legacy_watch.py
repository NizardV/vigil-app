from django.core.management.base import BaseCommand, CommandError

from modules.watch.legacy import LegacyError, import_legacy


class Command(BaseCommand):
    help = "Importe les données de veille de l'ancien Vigil depuis un schéma PostgreSQL (dump chargé au préalable)."

    def add_arguments(self, parser):
        parser.add_argument("--schema", default="legacy", help="schéma contenant l'ancienne base (défaut : legacy)")
        parser.add_argument("--dry-run", action="store_true", help="simule l'import : rien n'est conservé")

    def handle(self, *args, **options):
        try:
            counts = import_legacy(options["schema"], dry_run=options["dry_run"])
        except LegacyError as exc:
            raise CommandError(str(exc)) from exc
        label = "Simulation" if options["dry_run"] else "Import terminé"
        self.stdout.write(self.style.SUCCESS(f"{label} : " + ", ".join(f"{k}={v}" for k, v in counts.items())))
        self.stdout.write(
            "Rappel : les salons Discord se configurent par variables d'environnement "
            "(DISCORD_WEBHOOK_WATCH_<THEME>) ; les anciens webhooks stockés en base ne sont PAS repris."
        )

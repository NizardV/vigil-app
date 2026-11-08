from django.core.management.base import BaseCommand

from modules.projects.services import run_sync


class Command(BaseCommand):
    help = "Synchronise les repos GitHub du compte configuré (utilisable depuis un cron / un planificateur)."

    def handle(self, *args, **options):
        count = run_sync()
        self.stdout.write(self.style.SUCCESS(f"{count} repos synchronisés."))

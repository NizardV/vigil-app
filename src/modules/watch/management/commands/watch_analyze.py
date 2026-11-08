from django.core.management.base import BaseCommand, CommandError

from modules.watch import services


class Command(BaseCommand):
    help = "Analyse les articles en attente avec Gemini (plafonné par WATCH_MAX_ANALYSES_PER_RUN)."

    def handle(self, *args, **options):
        result = services.run_analysis()
        if result["status"] == "not_configured":
            self.stdout.write(self.style.WARNING("GEMINI_API_KEY non défini : rien analysé."))
            return
        if result["stopped"]:
            raise CommandError(f"Interrompu : {result['stopped']} ({result['analyzed']} analysé(s) avant l'arrêt)")
        self.stdout.write(self.style.SUCCESS(f"{result['analyzed']} analysé(s), {result['failed']} échec(s)."))

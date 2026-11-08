from django.core.management.base import BaseCommand

from modules.watch import services


class Command(BaseCommand):
    help = "Collecte les sources de veille dont l'intervalle est écoulé."

    def handle(self, *args, **options):
        result = services.run_fetch()
        self.stdout.write(
            self.style.SUCCESS(
                f"{result['fetched']}/{result['due']} sources collectées, "
                f"{result['new_articles']} nouveaux articles, {result['failed']} échec(s)."
            )
        )

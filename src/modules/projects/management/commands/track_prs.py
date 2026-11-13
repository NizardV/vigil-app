from django.core.management.base import BaseCommand, CommandError

from modules.projects.pull_requests import track_pull_requests


class Command(BaseCommand):
    help = (
        "Met à jour le suivi des PR en direct dans Discord (#projets-pr). Lancé toutes les minutes par Procrastinate."
    )

    def handle(self, *args, **options):
        result = track_pull_requests()
        if result["status"] == "not_configured":
            self.stdout.write(self.style.WARNING("DISCORD_WEBHOOK_PROJECTS_PR non défini : rien envoyé."))
        elif result["status"] == "error":
            raise CommandError(f"Suivi des PR impossible : {result['error']}")
        else:
            self.stdout.write(
                f"{result['seen']} PR suivie(s) : {result['posted']} nouvelle(s), {result['edited']} mise(s) à jour."
            )

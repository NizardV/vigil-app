from django.core.management.base import BaseCommand

from modules.security.services import run_sonarcloud_audit, summary


class Command(BaseCommand):
    help = "Audit du code par SonarCloud (lecture publique) : Quality Gate, problèmes de sécurité et de fiabilité."

    def handle(self, *args, **options):
        run = run_sonarcloud_audit()
        if run["status"] == "not_configured":
            self.stdout.write(self.style.WARNING("Aucun projet SonarCloud configuré (SONARCLOUD_PROJECTS)."))
            return
        self.stdout.write(f"{run['checks']} contrôles exécutés : {run['by_status']}")
        report = summary()
        self.stdout.write(f"Constats ouverts : {report['open_total']} {report['open_by_severity']}")

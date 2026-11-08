from django.core.management.base import BaseCommand, CommandError

from core.github import GitHubError
from modules.security.services import run_github_audit, summary


class Command(BaseCommand):
    help = "Lance l'audit de sécurité GitHub et affiche un résumé (lecture seule côté GitHub)."

    def handle(self, *args, **options):
        try:
            run = run_github_audit()
        except GitHubError as exc:
            # Liste des dépôts impossible : rien n'a été audité, l'état existant est intact.
            raise CommandError(f"Audit impossible, aucun constat modifié : {exc}") from exc
        self.stdout.write(f"{run['checks']} contrôles exécutés : {run['by_status']}")
        if run["rate_limited"]:
            self.stdout.write(self.style.WARNING("Quota GitHub épuisé : audit incomplet."))
        report = summary()
        self.stdout.write(f"Constats ouverts : {report['open_total']} {report['open_by_severity']}")
        if report["not_covered"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{report['not_covered']} contrôles non couverts (droits, fonction absente ou erreur)."
                )
            )

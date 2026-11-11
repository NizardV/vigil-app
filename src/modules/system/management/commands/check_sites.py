from django.core.management.base import BaseCommand

from modules.system.uptime import check_sites


class Command(BaseCommand):
    help = "Vérifie la disponibilité des sites surveillés (VIGIL_UPTIME_SITES) et alerte #système aux changements."

    def handle(self, *args, **options):
        result = check_sites()
        down = ", ".join(result["down"]) or "aucun"
        self.stdout.write(f"{result['checked']} site(s) vérifié(s) ; en échec : {down}.")
        if result["alerted"]:
            self.stdout.write(f"Alerte(s) envoyée(s) : {', '.join(result['alerted'])}.")

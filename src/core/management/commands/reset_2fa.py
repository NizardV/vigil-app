"""Supprime les appareils de double authentification d'un compte (téléphone perdu, appareil compromis).

Seul moyen d'enrôler un nouvel appareil une fois la 2FA active : il faut un accès au serveur.
    cd ~/vigil && docker compose --env-file vigil.env exec web python manage.py reset_2fa <identifiant>
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Réinitialise la double authentification d'un compte : il devra en enrôler une nouvelle à la connexion."

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, username: str, **options):
        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"Aucun compte « {username} ».")
        totp, _ = TOTPDevice.objects.filter(user=user).delete()
        static, _ = StaticDevice.objects.filter(user=user).delete()
        self.stdout.write(
            f"2FA réinitialisée pour {username} ({totp} appareil(s) TOTP, {static} jeu(x) de codes de secours "
            "supprimés). Le prochain accès demandera un nouvel enrôlement."
        )

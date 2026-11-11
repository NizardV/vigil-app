"""Authentification renforcée (ADR 0007) : second facteur obligatoire, enrôlement, codes de secours, blocage."""

from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.testing import login_verified
from core.two_factor import client_ip

WEBHOOKS = {"security-alerts": "https://discord.com/api/webhooks/1/secret"}
PASSWORD = "mot-de-passe-solide-1"  # noqa: S105 - compte de test


def code(device: TOTPDevice) -> str:
    return f"{totp(device.bin_key, device.step, device.t0, device.digits, device.drift):06d}"


class MiddlewareTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("root", "root@example.com", PASSWORD)

    def test_sans_appareil_envoie_vers_l_enrolement(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin:index"))
        self.assertRedirects(response, f"{reverse('two_factor_setup')}?next=%2Fadmin%2F", fetch_redirect_response=False)

    def test_avec_appareil_non_valide_envoie_vers_la_verification(self):
        TOTPDevice.objects.create(user=self.user, confirmed=True)
        self.client.force_login(self.user)
        response = self.client.get(reverse("admin:security_finding_changelist"))
        self.assertTrue(response["Location"].startswith(reverse("two_factor_verify")))

    def test_pages_hors_admin_aussi_protegees(self):
        """Les vues connectées hors /admin/ (ex. /projects/) exigent aussi le second facteur."""
        TOTPDevice.objects.create(user=self.user, confirmed=True)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("repo_list")).status_code, 302)

    def test_chemins_publics_et_techniques_restent_accessibles(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("health")).status_code, 200)
        self.assertEqual(self.client.get(reverse("two_factor_setup")).status_code, 200)

    def test_session_validee_accede_a_l_admin(self):
        login_verified(self.client, self.user)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    @override_settings(TWO_FACTOR_REQUIRED=False)
    def test_desactivable_en_dev(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_pages_2fa_exigent_une_connexion(self):
        response = self.client.get(reverse("two_factor_verify"))
        self.assertTrue(response["Location"].startswith(reverse("admin:login")))


@override_settings(DISCORD_WEBHOOKS=WEBHOOKS)
@mock.patch("core.notifications.send_discord")
class EnrolmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("root", "root@example.com", PASSWORD)
        self.client.force_login(self.user)

    def test_qr_code_puis_confirmation_puis_codes_de_secours(self, send):
        page = self.client.get(reverse("two_factor_setup"))
        self.assertContains(page, "<svg")
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)

        response = self.client.post(reverse("two_factor_setup"), {"token": code(device), "next": "/admin/"})

        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        self.assertEqual(len(response.context["codes"]), 10)
        self.assertEqual(StaticDevice.objects.get(user=self.user).token_set.count(), 10)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)  # session validée
        send.assert_called_once()
        self.assertIn("Nouvel appareil", send.call_args.args[1]["embeds"][0]["title"])

    def test_recharger_la_page_garde_la_meme_cle(self, send):
        self.client.get(reverse("two_factor_setup"))
        self.client.get(reverse("two_factor_setup"))
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)

    def test_mauvais_code_refuse(self, send):
        self.client.get(reverse("two_factor_setup"))
        response = self.client.post(reverse("two_factor_setup"), {"token": "000000"})
        self.assertContains(response, "Code invalide")
        self.assertFalse(TOTPDevice.objects.get(user=self.user).confirmed)
        send.assert_not_called()

    def test_impossible_d_enroler_un_second_appareil_par_le_web(self, send):
        """Avec un mot de passe volé, on ne peut pas ajouter son propre appareil à un compte déjà protégé."""
        TOTPDevice.objects.create(user=self.user, confirmed=True)
        response = self.client.get(reverse("two_factor_setup"))
        self.assertTrue(response["Location"].startswith(reverse("two_factor_verify")))
        self.assertEqual(TOTPDevice.objects.filter(user=self.user).count(), 1)


@override_settings(DISCORD_WEBHOOKS=WEBHOOKS)
@mock.patch("core.notifications.send_discord")
class VerifyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("root", "root@example.com", PASSWORD)
        self.device = TOTPDevice.objects.create(user=self.user, confirmed=True)
        self.client.force_login(self.user)

    def test_code_valide(self, send):
        response = self.client.post(reverse("two_factor_verify"), {"token": code(self.device), "next": "/admin/"})
        self.assertRedirects(response, "/admin/", fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        send.assert_not_called()

    def test_code_invalide(self, send):
        response = self.client.post(reverse("two_factor_verify"), {"token": "123456"})
        self.assertContains(response, "Code invalide")
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_code_rejoue_refuse(self, send):
        token = code(self.device)
        self.client.post(reverse("two_factor_verify"), {"token": token})
        self.client.logout()
        self.client.force_login(self.user)
        response = self.client.post(reverse("two_factor_verify"), {"token": token})
        self.assertContains(response, "Code invalide")

    def test_code_de_secours_a_usage_unique_et_alerte(self, send):
        backup = StaticDevice.objects.create(user=self.user, confirmed=True)
        backup.token_set.create(token="12345678")  # noqa: S106 - code de secours factice
        response = self.client.post(reverse("two_factor_verify"), {"token": "1234 5678"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(backup.token_set.exists())
        self.assertIn("Code de secours", send.call_args.args[1]["embeds"][0]["title"])

    def test_redirection_externe_ignoree(self, send):
        response = self.client.post(
            reverse("two_factor_verify"), {"token": code(self.device), "next": "https://evil.example/"}
        )
        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)


@override_settings(DISCORD_WEBHOOKS=WEBHOOKS)
@mock.patch("core.notifications.send_discord")
class LockoutTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        User.objects.create_superuser("root", "root@example.com", PASSWORD)

    def login(self, password, ip="203.0.113.7"):
        return self.client.post(
            reverse("admin:login"),
            {"username": "root", "password": password},
            HTTP_X_FORWARDED_FOR=f"10.0.0.1, {ip}",
        )

    def test_blocage_apres_cinq_echecs_et_alerte(self, send):
        for _ in range(5):
            self.login("faux")
        response = self.login(PASSWORD)  # même le bon mot de passe est refusé pendant le blocage
        self.assertEqual(response.status_code, 429)
        for _ in range(3):
            self.login("faux")  # l'attaquant insiste : pas de nouvelle alerte
        send.assert_called_once()
        description = send.call_args.args[1]["embeds"][0]["description"]
        self.assertIn("203.0.113.7", description)
        self.assertIn("exec web python manage.py axes_reset_ip", description)  # commande valable dans le conteneur

    def test_blocage_par_ip_seulement(self, send):
        """Un attaquant ne peut pas bloquer le propriétaire du compte depuis une autre adresse."""
        for _ in range(5):
            self.login("faux", ip="203.0.113.7")
        response = self.login(PASSWORD, ip="198.51.100.20")
        self.assertEqual(response.status_code, 302)

    def test_ip_reelle_derriere_nginx(self, send):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.7", REMOTE_ADDR="172.18.0.1")
        self.assertEqual(client_ip(request), "203.0.113.7")  # l'entrée ajoutée par NGINX, pas celle du client
        self.assertEqual(client_ip(RequestFactory().get("/", REMOTE_ADDR="127.0.0.1")), "127.0.0.1")


class ResetCommandTests(TestCase):
    def test_reinitialise_les_appareils(self):
        user = User.objects.create_user("root", password=PASSWORD)
        TOTPDevice.objects.create(user=user, confirmed=True)
        StaticDevice.objects.create(user=user, confirmed=True)
        out = StringIO()
        call_command("reset_2fa", "root", stdout=out)
        self.assertFalse(TOTPDevice.objects.filter(user=user).exists())
        self.assertFalse(StaticDevice.objects.filter(user=user).exists())
        self.assertIn("2FA réinitialisée", out.getvalue())

    def test_compte_inconnu(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("reset_2fa", "personne")

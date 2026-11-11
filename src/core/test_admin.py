"""Thème de l'admin (unfold, ADR 0006) : chaque page de l'admin se rend sans erreur."""

from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from core.testing import login_verified


class AdminThemeTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser("root", "root@example.com", "x" * 16)
        login_verified(self.client, self.admin_user)

    def test_tableau_de_bord(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Constats ouverts")
        self.assertContains(response, "vigil/favicon-32.png")

    def test_toutes_les_listes_de_l_admin_s_affichent(self):
        for model in admin.site._registry:
            name = f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
            with self.subTest(model=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_formulaires_d_ajout_des_comptes(self):
        for name in ("admin:auth_user_add", "admin:auth_group_add"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_logo_et_favicons_presents(self):
        for path in ("vigil/logo.png", "vigil/favicon-32.png", "vigil/favicon-16.png", "vigil/apple-touch-icon.png"):
            with self.subTest(path=path):
                self.assertIsNotNone(finders.find(path))

    def test_connexion_directe_renvoie_vers_l_admin(self):
        """Le formulaire d'unfold n'envoie pas de « next » : sans LOGIN_REDIRECT_URL, on finissait sur une 404."""
        self.client.logout()
        response = self.client.post(reverse("admin:login"), {"username": "root", "password": "x" * 16})
        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)

    def test_connexion_requise(self):
        self.client.logout()
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)

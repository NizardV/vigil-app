"""Authentification renforcée (ADR 0007) : double authentification TOTP obligatoire et alertes de sécurité.

- Toute session connectée doit avoir validé un second facteur (code TOTP ou code de secours), sauf sur quelques
  chemins publics ou techniques (`EXEMPT_PREFIXES`).
- Un compte sans appareil confirmé est envoyé vers l'enrôlement : possible **uniquement** tant qu'aucun appareil
  n'est confirmé. Ensuite, seule la commande `reset_2fa` (accès au serveur requis) permet d'en enrôler un autre.
- Blocage après échecs de connexion : django-axes (réglages dans settings.py), avec l'IP réelle lue derrière NGINX.
- Alertes Discord (salon security-alerts) : blocage d'une IP, nouvel appareil 2FA.
"""

import logging
import secrets
from urllib.parse import urlencode

import segno
from django import forms
from django.conf import settings
from django.contrib import admin
from django.core.cache import cache
from django.db import transaction
from django.dispatch import receiver
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django_otp import devices_for_user, user_has_device
from django_otp import login as otp_login
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from unfold.widgets import UnfoldAdminTextInputWidget

from core import notifications

log = logging.getLogger(__name__)

BACKUP_CODES = 10
# Chemins accessibles sans second facteur, même connecté : santé, webhooks (signés), fichiers statiques, et les
# pages nécessaires pour s'authentifier ou se déconnecter.
EXEMPT_PREFIXES = ("/health/", "/watch/discord/", "/static/")


# --- IP du client ---------------------------------------------------------------------------------------------


def client_ip(request: HttpRequest) -> str:
    """IP réelle du client. Vigil n'écoute que sur 127.0.0.1 derrière NGINX, qui *ajoute* l'adresse du client à la
    fin de X-Forwarded-For (`$proxy_add_x_forwarded_for`) : la dernière entrée est fiable, les précédentes non
    (fournies par le client). Sans en-tête (tests, dev), REMOTE_ADDR."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


# --- Alertes ---------------------------------------------------------------------------------------------------


def alert(title: str, description: str, color: int = 0xDC2626) -> None:
    """Alerte dans #security-alerts. N'interrompt jamais la requête : un échec est seulement journalisé."""
    url = notifications.webhook_url("security-alerts")
    if not url:
        return
    payload = {"embeds": [{"title": title, "description": description, "color": color, "footer": {"text": "Vigil"}}]}
    try:
        notifications.send_discord(url, payload, timeout=5)
    except notifications.NotificationError as exc:
        log.warning("Alerte de sécurité non envoyée : %s", exc)


try:  # django-axes absent (ex. réglages minimalistes) : pas d'alerte de blocage, le reste fonctionne.
    from axes.signals import user_locked_out
except ImportError:  # pragma: no cover
    user_locked_out = None

if user_locked_out is not None:

    @receiver(user_locked_out)
    def alert_on_lockout(sender, request=None, username=None, ip_address=None, **kwargs) -> None:
        # Le signal revient à chaque tentative pendant le blocage : une seule alerte par IP et par période.
        if not cache.add(f"vigil:lockout-alert:{ip_address}", True, timeout=settings.AXES_COOLOFF_TIME * 3600):
            return
        alert(
            "🔒 Connexion bloquée sur l'admin Vigil",
            f"Trop d'échecs de connexion depuis **{notifications.escape(ip_address or '?')}** "
            f"(identifiant tenté : `{notifications.escape(username or '?', 64)}`). "
            f"Blocage pendant {settings.AXES_COOLOFF_TIME} h.\n"
            "Si c'est toi : `python src/manage.py axes_reset_ip <ip>` sur le serveur.",
        )


# --- Middleware ------------------------------------------------------------------------------------------------


def _is_exempt(path: str) -> bool:
    open_paths = {
        reverse("admin:login"),
        reverse("admin:logout"),
        reverse("two_factor_verify"),
        reverse("two_factor_setup"),
    }
    return path in open_paths or path.startswith(EXEMPT_PREFIXES)


class Require2FAMiddleware:
    """Après le mot de passe, rien n'est accessible tant que le second facteur n'est pas validé.

    À placer après `django_otp.middleware.OTPMiddleware` (qui fournit `request.user.is_verified()`).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = request.user
        if (
            settings.TWO_FACTOR_REQUIRED
            and user.is_authenticated
            and not user.is_verified()
            and not _is_exempt(request.path)
        ):
            target = "two_factor_verify" if user_has_device(user) else "two_factor_setup"
            return redirect(f"{reverse(target)}?{urlencode({'next': request.get_full_path()})}")
        return self.get_response(request)


# --- Vues ------------------------------------------------------------------------------------------------------


class TokenForm(forms.Form):
    token = forms.CharField(
        label="Code",
        max_length=16,
        widget=UnfoldAdminTextInputWidget(
            attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True}
        ),
    )


def _next_url(request: HttpRequest) -> str:
    target = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse("admin:index")


def _page(request: HttpRequest, template: str, **context) -> HttpResponse:
    return render(request, template, {**admin.site.each_context(request), "next": _next_url(request), **context})


def _login_required(view):
    def wrapped(request: HttpRequest, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('admin:login')}?{urlencode({'next': request.get_full_path()})}")
        return view(request, *args, **kwargs)

    return wrapped


@_login_required
def verify(request: HttpRequest) -> HttpResponse:
    """Second facteur : code TOTP de l'application, ou un code de secours (usage unique)."""
    user = request.user
    if user.is_verified():
        return redirect(_next_url(request))
    if not user_has_device(user):
        return redirect(f"{reverse('two_factor_setup')}?{urlencode({'next': _next_url(request)})}")
    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        token = form.cleaned_data["token"].replace(" ", "")
        for device in devices_for_user(user, confirmed=True):
            if device.verify_token(token):  # avec limitation progressive des essais (django-otp)
                otp_login(request, device)
                if isinstance(device, StaticDevice):
                    left = device.token_set.count()
                    alert(
                        "🔑 Code de secours utilisé",
                        f"Connexion de **{notifications.escape(user.get_username())}** avec un code de secours "
                        f"(il en reste {left}), depuis {notifications.escape(client_ip(request))}.",
                        color=0xF97316,
                    )
                return redirect(_next_url(request))
        form.add_error("token", "Code invalide ou expiré.")
    return _page(request, "core/two_factor_verify.html", form=form, title="Double authentification")


@_login_required
def setup(request: HttpRequest) -> HttpResponse:
    """Enrôlement du premier appareil : QR code à scanner, puis confirmation par un code. Affiche ensuite les codes
    de secours, une seule fois."""
    user = request.user
    if user_has_device(user):  # déjà enrôlé : un nouvel appareil passe par `reset_2fa` sur le serveur
        return redirect(f"{reverse('two_factor_verify')}?{urlencode({'next': _next_url(request)})}")

    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=user, name="Application d'authentification", confirmed=False)

    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if device.verify_token(form.cleaned_data["token"].replace(" ", "")):
            codes = _confirm(user, device)
            otp_login(request, device)
            alert(
                "🛡️ Nouvel appareil de double authentification",
                f"Un appareil 2FA vient d'être enrôlé pour **{notifications.escape(user.get_username())}** "
                f"depuis {notifications.escape(client_ip(request))}. Si ce n'est pas toi : change le mot de passe "
                "et lance `python src/manage.py reset_2fa` sur le serveur.",
                color=0xF97316,
            )
            return _page(request, "core/two_factor_codes.html", codes=codes, title="Codes de secours")
        form.add_error("token", "Code invalide : vérifie l'heure du téléphone et réessaie.")

    qr = segno.make(device.config_url, error="m").svg_inline(scale=5, border=2, dark="#000000", light="#ffffff")
    return _page(
        request,
        "core/two_factor_setup.html",
        form=form,
        qr=mark_safe(qr),  # noqa: S308 - SVG produit par segno à partir de notre propre URL otpauth
        secret=device.config_url.split("secret=")[1].split("&")[0],
        title="Activer la double authentification",
    )


@transaction.atomic
def _confirm(user, device: TOTPDevice) -> list[str]:
    """Confirme l'appareil TOTP et crée les codes de secours (stockés en clair par django-otp, comme ses jetons)."""
    device.confirmed = True
    device.save(update_fields=["confirmed"])
    StaticDevice.objects.filter(user=user).delete()
    backup = StaticDevice.objects.create(user=user, name="Codes de secours", confirmed=True)
    codes = [f"{secrets.randbelow(10**8):08d}" for _ in range(BACKUP_CODES)]
    for code in codes:
        backup.token_set.create(token=code)
    return codes

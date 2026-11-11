from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from core import two_factor
from core.views import health

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="admin:index", permanent=False)),  # pas d'autre interface pour l'instant
    # Second facteur (ADR 0007) : avant admin/ pour ne pas être capturé par l'admin.
    path("admin/2fa/", two_factor.verify, name="two_factor_verify"),
    path("admin/2fa/setup/", two_factor.setup, name="two_factor_setup"),
    path("admin/2fa/passkeys/", two_factor.passkeys, name="two_factor_passkeys"),
    # API des passkeys (django-otp-webauthn) : l'enregistrement exige une session déjà validée par la 2FA.
    path("webauthn/", include("django_otp_webauthn.urls", namespace="otp_webauthn")),
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("projects/", include("modules.projects.urls")),
    path("watch/", include("modules.watch.urls")),
]

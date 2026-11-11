"""Aides de test partagées (aucune dépendance vers les modules métier)."""

from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice


def login_verified(client, user) -> TOTPDevice:
    """Connecte `user` avec le second facteur déjà validé (la 2FA est obligatoire, ADR 0007)."""
    device = TOTPDevice.objects.create(user=user, name="test", confirmed=True)
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return device

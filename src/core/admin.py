"""Comptes et groupes Django, réenregistrés avec les formulaires du thème unfold (ADR 0006)."""

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django_otp_webauthn.admin import WebAuthnCredentialAdmin
from django_otp_webauthn.models import WebAuthnCredential
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(WebAuthnCredential)
class PasskeyAdmin(WebAuthnCredentialAdmin, ModelAdmin):
    """Passkeys (ADR 0007) : consultation et suppression. L'ajout se fait depuis la page « Passkeys » (WebAuthn)."""

    def has_add_permission(self, request):
        return False

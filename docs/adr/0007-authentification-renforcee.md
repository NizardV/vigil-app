# ADR 0007 — Authentification renforcée : 2FA obligatoire (TOTP ou passkey), blocage des tentatives

- Statut : acceptée (2026-11-11)
- Complète : section « Sécurité » de `.claude/CLAUDE.md` (cible : tableau de bord non exposé publiquement)

## Contexte

L'admin de Vigil (https://vigil.nizard.dev/admin/) est exposé sur Internet et protégé par un mot de passe seul.
Il pilote des jetons sensibles (GitHub, Notion, Discord, accès d'audit au VPS). Un mot de passe deviné ou fuité
suffirait à tout contrôler, et rien ne limite les essais.

## Décision

Défense en profondeur :

1. **Double authentification TOTP obligatoire** (django-otp) pour **toute** session connectée, pas seulement
   `/admin/` (les vues `/projects/` sont aussi connectées). Middleware `core.two_factor.Require2FAMiddleware`.
   - Enrôlement web **uniquement pour le premier appareil** ; ensuite, `manage.py reset_2fa <compte>` sur le serveur.
     Un mot de passe volé ne permet donc pas d'ajouter son propre appareil.
   - 10 codes de secours à usage unique, affichés une seule fois.
   - Anti-rejeu et limitation progressive des essais fournis par django-otp.
2. **Blocage après 5 échecs de connexion depuis une même IP, pendant 1 h** (django-axes), avec l'IP réelle lue
   derrière NGINX (dernière entrée de `X-Forwarded-For` ; Vigil n'écoute que sur 127.0.0.1). Blocage **par IP**
   et non par compte : un tiers ne peut pas verrouiller le compte du propriétaire.
3. **Passkeys** (django-otp-webauthn) : second facteur au même titre que le code TOTP (« Utiliser une passkey »),
   **jamais à la place du mot de passe** (`OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN = False`). Ajout depuis la page
   Accès › Passkeys, **seulement avec une session déjà validée** (vérifié par le middleware et par la bibliothèque).
   Domaine : `OTP_WEBAUTHN_RP_ID` = premier hôte de `DJANGO_ALLOWED_HOSTS` (vigil.nizard.dev).

Alertes dans #security-alerts : IP bloquée (une seule par IP et par période), nouvel appareil 2FA, code de secours
utilisé, nouvelle passkey. Session limitée à 12 h.

**Restriction réseau (Tailscale) abandonnée** (2026-11-11) : l'admin doit rester utilisable depuis des appareils
variés sans installer de client VPN sur chacun. La 2FA, le blocage des tentatives et les alertes couvrent le risque.

## Conséquences

- (+) Un mot de passe seul ne suffit plus ; une attaque par essais est bloquée et signalée.
- (−) Quatre dépendances (django-otp, django-axes, segno pour le QR code, django-otp-webauthn), compatibles Django 6.1.
- (−) Perte du téléphone **et** des codes de secours : réinitialisation par `reset_2fa` via SSH (accès au serveur
  requis, ce qui est voulu).
- (−) Les codes de secours sont stockés en clair par django-otp (comme ses jetons statiques) : leur sécurité repose
  sur celle de la base, déjà requise pour tout le reste.
- `VIGIL_TWO_FACTOR_REQUIRED=0` désactive l'exigence (dev local uniquement ; vrai par défaut).
- Le cache de dédoublonnage des alertes de blocage est local à chaque worker gunicorn (2) : au pire une alerte
  par worker, jamais une par tentative.

## Alternatives écartées

- **django-two-factor-auth** : ne déclare pas encore Django 6.
- **Blocage par compte** : permettrait à n'importe qui de verrouiller l'administrateur.
- **Tailscale / Cloudflare Access devant l'admin** : client à installer sur chaque appareil, ou DNS à déplacer ;
  disproportionné pour un seul utilisateur qui se connecte depuis des appareils variés.
- **Passkey sans mot de passe** : possible, mais on garde deux facteurs distincts.

"""Réception des clics 👍/👎 envoyés par Discord (« Interactions Endpoint URL » du bot)."""

import json
import re

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.discord_interactions import verify_signature

from . import services

MAX_BODY = 20_000
CUSTOM_ID = re.compile(r"^watch:fb:(\d{1,12}):(1|-1)$")
PING, COMPONENT = 1, 3
EPHEMERAL = 64  # visible seulement par la personne qui a cliqué


def _reply(text: str) -> JsonResponse:
    return JsonResponse({"type": 4, "data": {"content": text, "flags": EPHEMERAL, "allowed_mentions": {"parse": []}}})


@csrf_exempt  # la signature Ed25519 de Discord remplace le jeton CSRF (appel de serveur à serveur)
@require_POST
def interactions(request):
    public_key = settings.DISCORD_PUBLIC_KEY
    if not public_key:
        return HttpResponse("non configuré", status=503)  # sans clé publique, on n'accepte RIEN
    try:
        if int(request.META.get("CONTENT_LENGTH") or 0) > MAX_BODY:
            return HttpResponse(status=413)
    except ValueError:
        return HttpResponse(status=400)
    body = request.body
    timestamp = request.headers.get("X-Signature-Timestamp", "")
    signature = request.headers.get("X-Signature-Ed25519", "")
    if not verify_signature(public_key, timestamp, body, signature):
        return HttpResponse("signature invalide", status=401)
    try:
        data = json.loads(body)
        kind = data.get("type")
    except (ValueError, AttributeError):
        return HttpResponse(status=400)

    if kind == PING:  # Discord vérifie l'URL lors de la configuration du bot
        return JsonResponse({"type": PING})
    if kind != COMPONENT:
        return _reply("Action non prise en charge.")
    match = CUSTOM_ID.match(str((data.get("data") or {}).get("custom_id", "")))
    if not match:
        return _reply("Action inconnue.")
    user = (data.get("member") or {}).get("user") or data.get("user") or {}
    author = f"discord:{user.get('id', 'inconnu')}"
    feedback = services.record_feedback(int(match.group(1)), int(match.group(2)), author)
    if feedback is None:
        return _reply("Cet article n'existe plus.")
    return _reply("👍 Retour enregistré : pertinent." if feedback.rating == 1 else "👎 Retour enregistré : hors sujet.")

from django.db import connection
from django.http import JsonResponse


def health(request):
    """Sonde de sante : repond 200 si l'app et la base repondent, 503 sinon."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - la sonde ne doit jamais lever
        return JsonResponse({"status": "degraded"}, status=503)
    return JsonResponse({"status": "ok"})

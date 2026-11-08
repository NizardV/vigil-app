"""Client Gemini minimal (API REST), partagé par les modules. Aucune dépendance vers les modules métier.

Choix de conception :
- appel HTTP direct (pas de SDK) : l'ancien paquet `google-generativeai` est abandonné ;
- la clé voyage dans l'en-tête `x-goog-api-key`, jamais dans l'URL (qui finit dans les journaux) ;
- les instructions (`system`) sont séparées du contenu à analyser (`user`) : un article qui contient
  « ignore tes instructions » reste de la donnée, pas une consigne ;
- la clé n'apparaît dans aucun message d'erreur.
"""

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_MODEL = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_RETRY_WAIT = 5.0


class LLMError(Exception):
    """Échec d'un appel au modèle. Ne contient jamais la clé d'API."""


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout: int = 60):
        if not _MODEL.match(model or ""):
            raise LLMError("nom de modèle invalide")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def _call(self, body: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY n'est pas défini")
        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key, "User-Agent": "vigil"}
        url = f"{BASE}/{self.model}:generateContent"
        for attempt in (1, 2):
            request = urllib.request.Request(url, data=payload, headers=headers, method="POST")  # noqa: S310
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - hôte fixe (https)
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt == 1:
                    time.sleep(min(MAX_RETRY_WAIT, 2.0))
                    continue
                raise LLMError(f"Gemini a répondu {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise LLMError("Gemini injoignable") from None
            except ValueError:
                raise LLMError("réponse de Gemini illisible") from None
        raise LLMError("Gemini indisponible")  # pragma: no cover - sortie de sécurité

    def generate(
        self,
        *,
        system: str,
        user: str,
        json_output: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        thinking_budget: int | None = None,
    ) -> str:
        """Renvoie le texte généré. `system` = consignes, `user` = données à traiter (non fiables).

        `thinking_budget=0` désactive la « réflexion » des modèles 2.5 : sans cela, ces jetons comptent dans
        `max_tokens` et une réponse JSON peut être coupée net (vérifié : jusqu'à 760 jetons de réflexion).
        """
        config: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if json_output:
            config["responseMimeType"] = "application/json"
        if thinking_budget is not None:
            config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        data = self._call(
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": config,
            }
        )
        if (data.get("promptFeedback") or {}).get("blockReason"):
            raise LLMError("contenu refusé par le modèle")
        try:
            candidate = data["candidates"][0]
            parts = candidate["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts).strip()
        except (KeyError, IndexError, TypeError):
            raise LLMError("réponse de Gemini sans contenu") from None
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise LLMError("réponse tronquée (limite de jetons atteinte)")
        return text

    def generate_json(self, *, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
        text = self.generate(system=system, user=user, json_output=True, **kwargs)
        try:
            result = json.loads(text)
        except ValueError:
            raise LLMError("le modèle n'a pas renvoyé du JSON valide") from None
        if not isinstance(result, dict):
            raise LLMError("le modèle n'a pas renvoyé un objet JSON")
        return result

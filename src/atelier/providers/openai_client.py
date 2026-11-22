"""Client OpenAI (API de la plateforme, pas l'abonnement ChatGPT)."""

from __future__ import annotations

import base64

from atelier.providers.base import ImageInput, ProviderClient
from atelier.providers.errors import ErrorKind, ProviderError


class OpenAIClient(ProviderClient):
    # La clef attendue est une clef d'API de la plateforme OpenAI (facturée à l'usage) ;
    # un abonnement ChatGPT ne donne pas accès à cette API.
    base_url = "https://api.openai.com/v1"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key.get_secret_value()}"}

    async def check_model(self, model: str) -> None:
        # Lecture de la fiche du modèle : valide clef ET nom du modèle sans rien générer.
        await self._request("GET", f"/models/{model}")

    async def generate_text(self, prompt: str, system: str | None = None) -> str:
        messages = [{"role": "system", "content": system}] if system else []
        messages.append({"role": "user", "content": prompt})
        data = await self._request(
            "POST",
            "/chat/completions",
            json={"model": self.config.text_model, "messages": messages},
        )
        choice = (data.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "content_filter":
            raise ProviderError("openai", ErrorKind.CONTENT, "réponse filtrée")
        text = (choice.get("message") or {}).get("content")
        if not isinstance(text, str) or not text:
            raise ProviderError("openai", ErrorKind.UNAVAILABLE, "réponse sans texte")
        return text

    async def edit_image(self, prompt: str, images: list[ImageInput]) -> bytes:
        files = [
            ("image[]", (f"image{i}.jpg", img.data, img.mime_type)) for i, img in enumerate(images)
        ]
        data = await self._request(
            "POST", "/images/edits",
            data={"model": self.config.image_model, "prompt": prompt}, files=files,
        )  # fmt: skip
        b64 = ((data.get("data") or [{}])[0]).get("b64_json")
        if not isinstance(b64, str):
            raise ProviderError("openai", ErrorKind.UNAVAILABLE, "réponse sans image")
        return base64.b64decode(b64)

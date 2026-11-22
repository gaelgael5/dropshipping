"""Client Google Gemini (API Generative Language)."""

from __future__ import annotations

import base64
from typing import Any

from atelier.providers.base import ImageInput, ProviderClient
from atelier.providers.errors import ErrorKind, ProviderError


class GeminiClient(ProviderClient):
    base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _auth_headers(self) -> dict[str, str]:
        # En-tête, jamais « ?key= » : une clef dans l'URL finit dans les journaux des
        # relais et dans les messages d'exception de httpx.
        return {"x-goog-api-key": self.config.api_key.get_secret_value()}

    async def check_model(self, model: str) -> None:
        await self._request("GET", f"/models/{model}")

    async def _generate(self, model: str, parts: list[dict[str, Any]], **extra: Any) -> list[Any]:
        data = await self._request(
            "POST", f"/models/{model}:generateContent",
            json={"contents": [{"parts": parts}], **extra},
        )  # fmt: skip
        # Un refus de contenu arrive en HTTP 200 : il faut le lire dans le corps.
        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        candidate = (data.get("candidates") or [{}])[0]
        if blocked or candidate.get("finishReason") in {"SAFETY", "PROHIBITED_CONTENT"}:
            raise ProviderError("gemini", ErrorKind.CONTENT, str(blocked or "SAFETY"))
        return list((candidate.get("content") or {}).get("parts") or [])

    async def generate_text(self, prompt: str, system: str | None = None) -> str:
        extra = {"systemInstruction": {"parts": [{"text": system}]}} if system else {}
        parts = await self._generate(self.config.text_model, [{"text": prompt}], **extra)
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            raise ProviderError("gemini", ErrorKind.UNAVAILABLE, "réponse sans texte")
        return text

    async def edit_image(self, prompt: str, images: list[ImageInput]) -> bytes:
        inputs: list[dict[str, Any]] = [{"text": prompt}] + [
            {"inline_data": {"mime_type": img.mime_type,
                             "data": base64.b64encode(img.data).decode()}}
            for img in images
        ]  # fmt: skip
        for part in await self._generate(self.config.image_model, inputs):
            blob = (
                part.get("inlineData") or part.get("inline_data")
                if isinstance(part, dict)
                else None
            )
            if isinstance(blob, dict) and blob.get("data"):
                return base64.b64decode(blob["data"])
        raise ProviderError("gemini", ErrorKind.UNAVAILABLE, "réponse sans image")

"""Interface commune des fournisseurs et appel HTTP partagé."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from atelier.providers.errors import (
    ErrorKind,
    ProviderError,
    classify_response,
    transport_error,
)

TIMEOUT_S = httpx.Timeout(120.0, connect=10.0)  # une génération d'image prend du temps


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: SecretStr
    text_model: str
    image_model: str


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime_type: str = "image/jpeg"


class ProviderClient:
    """Base des clients : porte la clef et l'appel HTTP ; les sous-classes la réalisent."""

    base_url: str

    def __init__(self, config: ProviderConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self._transport = transport  # doublure en test ; réseau réel sinon

    def _auth_headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        # La clef n'est déballée qu'ici, au moment de l'appel (point d'injection).
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=TIMEOUT_S, transport=self._transport,
            headers=self._auth_headers(),
        ) as http:  # fmt: skip
            try:
                response = await http.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                raise transport_error(self.config.name, exc) from None
        if response.is_error:
            raise classify_response(
                self.config.name, response, self.config.api_key.get_secret_value()
            )
        try:
            return response.json()
        except ValueError:
            raise ProviderError(
                self.config.name, ErrorKind.UNAVAILABLE, "réponse non JSON"
            ) from None

    async def check_model(self, model: str) -> None:
        raise NotImplementedError

    async def generate_text(self, prompt: str, system: str | None = None) -> str:
        raise NotImplementedError

    async def edit_image(self, prompt: str, images: list[ImageInput]) -> bytes:
        raise NotImplementedError

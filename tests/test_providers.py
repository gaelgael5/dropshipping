from __future__ import annotations

import base64
import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from atelier.providers.base import ImageInput, ProviderConfig
from atelier.providers.errors import ErrorKind, ProviderError
from atelier.providers.gemini_client import GeminiClient
from atelier.providers.openai_client import OpenAIClient

KEY = "sk-test-Hq93LmZ"
PNG = b"\x89PNG-fake"
Handler = Callable[[httpx.Request], httpx.Response]


def _config(name: str) -> ProviderConfig:
    return ProviderConfig(name, SecretStr(KEY), "txt-model", "img-model")


def _openai(handler: Handler) -> OpenAIClient:
    return OpenAIClient(_config("openai"), httpx.MockTransport(handler))


def _gemini(handler: Handler) -> GeminiClient:
    return GeminiClient(_config("gemini"), httpx.MockTransport(handler))


async def test_openai_sends_bearer_and_checks_model() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "txt-model"})

    await _openai(handler).check_model("txt-model")
    assert seen[0].url.path == "/v1/models/txt-model"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


async def test_gemini_key_in_header_never_in_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"name": "models/x"})

    await _gemini(handler).check_model("img-model")
    assert seen[0].headers["x-goog-api-key"] == KEY
    assert KEY not in str(seen[0].url)


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (401, {"error": {"message": "bad key", "code": "invalid_api_key"}}, ErrorKind.AUTH),
        (400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                         "details": [{"reason": "API_KEY_INVALID"}]}}, ErrorKind.AUTH),
        (429, {"error": {"code": "insufficient_quota"}}, ErrorKind.QUOTA),
        (400, {"error": {"code": "content_policy_violation"}}, ErrorKind.CONTENT),
        (503, {"error": {"message": "overloaded"}}, ErrorKind.UNAVAILABLE),
        (404, {"error": {"message": "model not found"}}, ErrorKind.REQUEST),
        (502, None, ErrorKind.UNAVAILABLE),
    ],
)  # fmt: skip
async def test_errors_are_classified(status: int, body: object, kind: ErrorKind) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body) if body else httpx.Response(status, text="<html>")

    with pytest.raises(ProviderError) as exc:
        await _gemini(handler).check_model("m")
    assert exc.value.kind is kind
    assert KEY not in str(exc.value)


async def test_network_failure_is_unavailable_without_url_or_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}", request=request)

    with pytest.raises(ProviderError) as exc:
        await _openai(handler).check_model("m")
    assert exc.value.kind is ErrorKind.UNAVAILABLE
    assert "api.openai.com" not in str(exc.value) and KEY not in str(exc.value)


async def test_openai_text_and_image() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            body = json.loads(request.content)
            assert body["model"] == "txt-model" and body["messages"][0]["role"] == "system"
            return httpx.Response(200, json={"choices": [{"message": {"content": "Titre"},
                                                          "finish_reason": "stop"}]})  # fmt: skip
        assert request.url.path.endswith("/images/edits")
        assert b'name="image[]"' in request.content and b"img-model" in request.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    client = _openai(handler)
    assert await client.generate_text("écris", system="tu es") == "Titre"
    assert await client.edit_image("change", [ImageInput(b"a"), ImageInput(b"b")]) == PNG


async def test_openai_content_filter_is_content_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        choice = {"message": {"content": ""}, "finish_reason": "content_filter"}
        return httpx.Response(200, json={"choices": [choice]})

    with pytest.raises(ProviderError) as exc:
        await _openai(handler).generate_text("x")
    assert exc.value.kind is ErrorKind.CONTENT


async def test_gemini_text_and_image() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("txt-model:generateContent"):
            assert body["systemInstruction"]["parts"][0]["text"] == "sys"
            return httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "Bon"}, {"text": "jour"}]}}]},
            )
        assert body["contents"][0]["parts"][1]["inline_data"]["mime_type"] == "image/jpeg"
        img = {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}, img]}}]}
        )

    client = _gemini(handler)
    assert await client.generate_text("x", system="sys") == "Bonjour"
    assert await client.edit_image("x", [ImageInput(b"a")]) == PNG


@pytest.mark.parametrize(
    "body",
    [{"promptFeedback": {"blockReason": "SAFETY"}},
     {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}],
)  # fmt: skip
async def test_gemini_block_in_200_is_content_error(body: object) -> None:
    with pytest.raises(ProviderError) as exc:
        await _gemini(lambda _: httpx.Response(200, json=body)).generate_text("x")
    assert exc.value.kind is ErrorKind.CONTENT


async def test_gemini_answer_without_image_is_explicit() -> None:
    body = {"candidates": [{"content": {"parts": [{"text": "je ne peux pas"}]}}]}
    with pytest.raises(ProviderError, match="sans image"):
        await _gemini(lambda _: httpx.Response(200, json=body)).edit_image("x", [ImageInput(b"a")])


async def test_provider_message_echoing_key_fragment_is_redacted() -> None:
    # Constaté sur test1 : OpenAI recopie début et fin de la clef refusée dans son message.
    echoed = f"Incorrect API key provided: {KEY[:8]}*****************{KEY[-4:]}. See docs."

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": echoed, "code": "invalid_api_key"}})

    with pytest.raises(ProviderError) as exc:
        await _openai(handler).check_model("m")
    assert KEY[:8] not in str(exc.value) and KEY[-4:] not in str(exc.value)
    assert "Incorrect API key provided" in str(exc.value)

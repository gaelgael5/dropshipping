"""Corps de formulaire en UTF-8 brut : les rendre lisibles comme ceux d'un navigateur."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_FORM_TYPE = b"application/x-www-form-urlencoded"


def _percent_encode_non_ascii(chunk: bytes) -> bytes:
    return b"".join(b"%%%02X" % byte if byte >= 0x80 else bytes((byte,)) for byte in chunk)


class Utf8FormBodyMiddleware:
    """Pourcent-encode les octets non ASCII d'un corps urlencoded avant son analyse.

    Un navigateur pourcent-encode toujours ; curl -d, un raccourci de téléphone ou un
    agent envoient souvent l'UTF-8 tel quel. Starlette décode alors ces octets en
    Latin-1, et « Vêtements » est stocké « VÃªtements » (constaté sur test1). Encodés
    ici, ils sont décodés en UTF-8 comme le reste. Octet par octet, donc sans risque
    de couper un caractère entre deux morceaux du corps.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._is_form(scope):
            await self.app(scope, receive, send)
            return

        async def receive_encoded() -> Message:
            message = await receive()
            if message["type"] == "http.request" and message.get("body"):
                message["body"] = _percent_encode_non_ascii(message["body"])
            return message

        # Le corps change de longueur : l'en-tête d'origine deviendrait faux.
        scope = {
            **scope,
            "headers": [(k, v) for k, v in scope["headers"] if k != b"content-length"],
        }
        await self.app(scope, receive_encoded, send)

    @staticmethod
    def _is_form(scope: Scope) -> bool:
        for key, value in scope["headers"]:
            if key == b"content-type":
                return bool(value.split(b";")[0].strip().lower() == _FORM_TYPE)
        return False

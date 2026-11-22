from __future__ import annotations

import re
from pathlib import Path

from pydantic import SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from atelier.errors import StartupError

ENV_PREFIX = "ATELIER_"
_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class Settings(BaseSettings):
    # Aucune valeur par défaut pour la base, le volume et le jeton : un défaut
    # « pratique » (localhost, /tmp...) ferait démarrer l'atelier contre une
    # ressource qui n'est pas la sienne au lieu d'échouer. Seuls l'adresse
    # d'écoute et les délais ont un défaut, car ils ne désignent aucune ressource.
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="forbid")

    database_url: SecretStr
    images_root: Path
    capture_token: SecretStr
    # Clef maîtresse du coffre de secrets : 32 octets en hexadécimal. Hors base, pour
    # qu'une sauvegarde ou une base volée ne livre aucun secret. La perdre rend le
    # coffre illisible : elle se sauvegarde à part, jamais avec la base.
    secrets_key: SecretStr
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    db_connect_timeout_s: float = 10.0

    @field_validator("secrets_key")
    @classmethod
    def _check_secrets_key(cls, value: SecretStr) -> SecretStr:
        # Le message ne cite jamais la valeur reçue (voir _describe_invalid_settings).
        if not _HEX_KEY_RE.fullmatch(value.get_secret_value()):
            raise ValueError("64 caractères hexadécimaux attendus (openssl rand -hex 32)")
        return value

    def secrets_key_bytes(self) -> bytes:
        return bytes.fromhex(self.secrets_key.get_secret_value())


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        raise StartupError(_describe_invalid_settings(exc)) from None


def _describe_invalid_settings(exc: ValidationError) -> str:
    # On ne reprend que le nom de la variable et la nature du problème : le
    # message standard de pydantic recopie la valeur reçue, qui peut être un
    # mot de passe ou un jeton.
    problems = [
        f"{ENV_PREFIX}{'.'.join(str(part) for part in err['loc']).upper()} : {err['msg']}"
        for err in exc.errors()
    ]
    return "Configuration invalide — " + " ; ".join(problems)

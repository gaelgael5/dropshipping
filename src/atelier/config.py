from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from atelier.errors import StartupError

ENV_PREFIX = "ATELIER_"


class Settings(BaseSettings):
    # Aucune valeur par défaut pour la base, le volume et le jeton : un défaut
    # « pratique » (localhost, /tmp...) ferait démarrer l'atelier contre une
    # ressource qui n'est pas la sienne au lieu d'échouer. Seuls l'adresse
    # d'écoute et les délais ont un défaut, car ils ne désignent aucune ressource.
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="forbid")

    database_url: SecretStr
    images_root: Path
    capture_token: SecretStr
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    db_connect_timeout_s: float = 10.0


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

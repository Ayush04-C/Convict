"""Ayush-owned settings. Stub mode does not require provider credentials."""
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    demo_user: str = "ayush"
    demo_pass_hash: SecretStr = SecretStr("")
    jwt_secret: SecretStr = SecretStr("")
    model_fast: str = ""
    model_mid: str = ""
    model_pro: str = ""
    gemini_api_key: SecretStr = SecretStr("")
    crustdata_api_key: SecretStr = SecretStr("")
    database_path: Path = Path("data/leo.sqlite")
    mode: Literal["stub", "live"] = "stub"

    def require_live_credentials(self) -> None:
        raise NotImplementedError("Stage 2/4: validate authentication and live provider configuration")

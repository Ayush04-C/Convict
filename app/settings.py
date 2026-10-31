"""Ayush-owned settings; API authentication is independent of live providers."""
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfigurationError(ValueError):
    """Safe startup error: variable names and guidance, never secret values."""


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

    def require_auth_credentials(self) -> None:
        from argon2 import PasswordHasher
        from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

        invalid = []
        if not self.demo_user.strip() or len(self.demo_user) > 100:
            invalid.append("DEMO_USER")
        secret = self.jwt_secret.get_secret_value()
        if len(secret.encode()) < 32 or len(set(secret)) < 8:
            invalid.append("JWT_SECRET (generate a random secret of at least 32 bytes)")
        password_hash = self.demo_pass_hash.get_secret_value()
        if not password_hash.startswith("$argon2id$"):
            invalid.append("DEMO_PASS_HASH (Argon2id hash required)")
        else:
            try:
                PasswordHasher().verify(password_hash, "leo-configuration-check")
            except VerifyMismatchError:
                pass  # A mismatch proves the encoded hash is usable.
            except (InvalidHashError, VerificationError):
                invalid.append("DEMO_PASS_HASH (invalid Argon2id hash)")
        if invalid:
            raise AuthConfigurationError(
                "Invalid authentication configuration: " + ", ".join(invalid)
                + ". Run python -m scripts.configure_auth from the Leo directory."
            )

    def require_live_credentials(self) -> None:
        raise NotImplementedError("Stage 2/4: validate authentication and live provider configuration")

"""Ayush-owned isolated auth fixtures; never load the developer's .env."""
import secrets

import httpx
import pytest
from pwdlib import PasswordHash

from app.main import create_app
from app.settings import Settings

TEST_PASSWORD = "test-only-password-42"
pytest_plugins = ["tests.ledger_support"]


@pytest.fixture(autouse=True)
def isolate_auth_environment(monkeypatch):
    for name in ("DEMO_USER", "DEMO_PASS_HASH", "JWT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODE", "stub")


@pytest.fixture(scope="session")
def auth_settings(tmp_path_factory):
    return Settings(
        _env_file=None, demo_user="test-user",
        demo_pass_hash=PasswordHash.recommended().hash(TEST_PASSWORD),
        jwt_secret=secrets.token_urlsafe(48), mode="stub",
        database_path=tmp_path_factory.mktemp("auth-api") / "ledger.sqlite",
    )


@pytest.fixture
async def client(auth_settings):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(auth_settings)), base_url="http://test",
    ) as client:
        yield client

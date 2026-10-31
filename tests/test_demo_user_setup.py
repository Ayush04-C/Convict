"""`make setup` gives a fresh clone a working, documented fake login with no prompts."""
import os
import secrets
from pathlib import Path

import httpx
from dotenv import dotenv_values
from pwdlib import PasswordHash

from app.main import create_app
from app.settings import Settings
from scripts.configure_auth import DEMO_PASSWORD, DEMO_USERNAME, configure

ROOT = Path(__file__).resolve().parents[1]


def no_prompt(_):
    raise AssertionError("Demo setup must never prompt")


def demo(path, **kwargs):
    return configure(path, input_fn=no_prompt, password_fn=no_prompt, demo=True, **kwargs)


def test_a_fresh_clone_gets_the_documented_demo_user_without_a_single_prompt(tmp_path):
    path = tmp_path / ".env"
    path.write_text((ROOT / ".env.example").read_text())          # what `make setup` copies
    assert demo(path) is True
    values = dotenv_values(path)
    assert values["DEMO_USER"] == DEMO_USERNAME == "maya"          # replaces the template's "ayush"
    assert PasswordHash.recommended().verify(DEMO_PASSWORD, values["DEMO_PASS_HASH"])
    assert len(values["JWT_SECRET"]) >= 32
    assert DEMO_PASSWORD not in path.read_text()                   # only a hash is stored
    assert os.stat(path).st_mode & 0o777 == 0o600
    Settings(_env_file=path).require_auth_credentials()


def test_setup_works_when_there_is_no_env_file_yet(tmp_path):
    path = tmp_path / ".env"
    assert demo(path) is True and dotenv_values(path)["DEMO_USER"] == "maya"


def test_the_jwt_secret_is_random_per_clone_not_a_shared_default(tmp_path):
    first, second = tmp_path / "a.env", tmp_path / "b.env"
    demo(first), demo(second)
    assert dotenv_values(first)["JWT_SECRET"] != dotenv_values(second)["JWT_SECRET"]
    assert dotenv_values(first)["DEMO_PASS_HASH"] != dotenv_values(second)["DEMO_PASS_HASH"]  # salted


def test_existing_credentials_are_never_replaced_by_the_demo_account(tmp_path):
    path = tmp_path / ".env"
    own = PasswordHash.recommended().hash("my-own-long-password")
    path.write_text(f"DEMO_USER='someone'\nDEMO_PASS_HASH='{own}'\nJWT_SECRET='{secrets.token_urlsafe(48)}'\n")
    before = path.read_text()
    assert demo(path) is False
    assert path.read_text() == before


def test_provider_keys_and_comments_survive_demo_setup(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# keep me\nDEMO_USER=ayush\nDEMO_PASS_HASH=\nJWT_SECRET=\n"
                    "GEMINI_API_KEY='provider-test-value'\nMODE=live\n")
    demo(path)
    content = path.read_text()
    assert "# keep me\n" in content and "GEMINI_API_KEY='provider-test-value'" in content
    assert dotenv_values(path)["MODE"] == "live" and dotenv_values(path)["DEMO_USER"] == "maya"


def test_running_demo_setup_twice_changes_nothing_the_second_time(tmp_path):
    path = tmp_path / ".env"
    assert demo(path) is True
    once = path.read_text()
    assert demo(path) is False and path.read_text() == once


async def test_the_demo_user_can_actually_sign_in_and_a_wrong_password_cannot(tmp_path):
    path = tmp_path / ".env"
    demo(path)
    app = create_app(Settings(_env_file=path, database_path=tmp_path / "ledger.sqlite", mode="stub"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post("/auth/login", json={"username": "maya", "password": DEMO_PASSWORD})
        bad = await client.post("/auth/login", json={"username": "maya", "password": DEMO_PASSWORD + "x"})
        me = await client.get("/auth/me", headers={"Authorization": "Bearer " + ok.json()["access_token"]})
    assert ok.status_code == 200 and bad.status_code == 401
    assert me.json() == {"username": "maya"}


def test_the_readme_documents_exactly_the_credentials_setup_creates():
    readme = (ROOT / "README.md").read_text()
    assert f"`{DEMO_USERNAME}`" in readme and f"`{DEMO_PASSWORD}`" in readme
    assert "make setup" in readme and "fake" in readme.lower()


def test_make_setup_creates_env_and_runs_the_demo_option():
    makefile = (ROOT / "Makefile").read_text()
    setup = makefile.split("\nsetup:\n", 1)[1].split("\n\n", 1)[0].split("\nauth-setup:")[0]
    assert ".env.example" in setup and "scripts.configure_auth --demo" in setup
    assert "setup" in makefile.split(".PHONY:", 1)[1].split("\n", 1)[0]


def test_the_default_server_only_listens_on_localhost():
    assert "--host 127.0.0.1" in (ROOT / "Makefile").read_text()

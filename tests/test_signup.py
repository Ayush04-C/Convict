"""Sign-up from the web page: new accounts can sign in, own their own runs, and cannot take the demo name."""
import httpx
import pytest

from app import auth
from app.main import create_app
from app.settings import Settings
from scripts.configure_auth import configure


@pytest.fixture
def client(tmp_path):
    env = tmp_path / ".env"
    configure(env, demo=True)
    auth._signups.clear()
    app = create_app(Settings(_env_file=env, database_path=tmp_path / "ledger.sqlite", mode="stub"))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def post(client, path, username, password):
    return await client.post(path, json={"username": username, "password": password})


async def test_signup_then_login_and_me(client):
    async with client:
        created = await post(client, "/auth/signup", "Priya.K", "a-long-password")
        assert created.status_code == 201
        me = await client.get("/auth/me", headers={"Authorization": "Bearer " + created.json()["access_token"]})
        assert me.json() == {"username": "priya.k"}
        assert (await post(client, "/auth/login", "priya.k", "a-long-password")).status_code == 200
        assert (await post(client, "/auth/login", "PRIYA.K", "a-long-password")).status_code == 200
        assert (await post(client, "/auth/login", "priya.k", "wrong-password!")).status_code == 401


async def test_demo_login_still_works_and_name_is_reserved(client):
    async with client:
        assert (await post(client, "/auth/login", "maya", "maya-yc-demo-2026")).status_code == 200
        assert (await post(client, "/auth/signup", "maya", "a-long-password")).status_code == 409
        assert (await post(client, "/auth/signup", "MAYA", "a-long-password")).status_code == 409


async def test_duplicate_username_refused_and_original_password_kept(client):
    async with client:
        assert (await post(client, "/auth/signup", "sam", "first-password-1")).status_code == 201
        assert (await post(client, "/auth/signup", "SAM", "second-password-2")).status_code == 409
        assert (await post(client, "/auth/login", "sam", "first-password-1")).status_code == 200
        assert (await post(client, "/auth/login", "sam", "second-password-2")).status_code == 401


@pytest.mark.parametrize("username,password", [("ab", "a-long-password"), ("bad name", "a-long-password"),
                                               ("x" * 33, "a-long-password"), ("-lead", "a-long-password"),
                                               ("okname", "short")])
async def test_invalid_signup_refused(client, username, password):
    async with client:
        assert (await post(client, "/auth/signup", username, password)).status_code in (422, 400)


async def test_unknown_user_cannot_log_in(client):
    async with client:
        assert (await post(client, "/auth/login", "nobody", "a-long-password")).status_code == 401


async def test_signup_is_rate_limited(client):
    async with client:
        codes = [(await post(client, "/auth/signup", f"user{i}", "a-long-password")).status_code
                 for i in range(auth.SIGNUP_LIMIT + 1)]
        assert codes[:-1] == [201] * auth.SIGNUP_LIMIT and codes[-1] == 429


async def test_password_stored_only_as_hash(client, tmp_path):
    async with client:
        await post(client, "/auth/signup", "hashed", "a-long-password")
    import sqlite3
    stored = sqlite3.connect(tmp_path / "ledger.sqlite").execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("$argon2id$") and "a-long-password" not in stored


def test_page_offers_signup():
    from pathlib import Path
    html = Path("app/web/index.html").read_text()
    js = Path("app/web/app.js").read_text()
    assert 'id="signup"' in html and "/auth/signup" in js


def test_demo_button_uses_the_documented_credentials():
    from pathlib import Path
    from scripts.configure_auth import DEMO_PASSWORD, DEMO_USERNAME
    js = Path("app/web/app.js").read_text()
    assert 'id="demo-login"' in Path("app/web/index.html").read_text()
    assert f"username:'{DEMO_USERNAME}',password:'{DEMO_PASSWORD}'" in js

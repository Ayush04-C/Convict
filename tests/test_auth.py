from datetime import datetime, timezone
import secrets

import jwt
import pytest

from app.auth import AUDIENCE, ISSUER, create_access_token
from tests.conftest import TEST_PASSWORD


def claims():
    now = int(datetime.now(timezone.utc).timestamp())
    return {"sub": "test-user", "iat": now, "exp": now + 3600, "iss": ISSUER, "aud": AUDIENCE}


def assert_unauthorized(response):
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["Cache-Control"] == "no-store"
    error = response.json()["error"]
    assert error["code"] == "UNAUTHORIZED"
    assert error["message"] == "Invalid credentials or access token."
    assert error["request_id"] == response.headers["X-Request-ID"]


async def test_login_and_authenticated_identity(client, auth_settings):
    response = await client.post("/auth/login", json={"username": "test-user", "password": TEST_PASSWORD})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    result = response.json()
    assert set(result) == {"access_token", "token_type", "expires_in"}
    assert result["expires_in"] == 3600
    assert result["token_type"] == "bearer"
    decoded = jwt.decode(result["access_token"], auth_settings.jwt_secret.get_secret_value(),
                         algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE)
    assert decoded["exp"] - decoded["iat"] == 3600
    assert set(decoded) == {"sub", "iat", "exp", "iss", "aud"}
    assert TEST_PASSWORD not in str(decoded)
    me = await client.get("/auth/me", headers={"Authorization": "Bearer " + result["access_token"]})
    assert me.status_code == 200
    assert me.json() == {"username": "test-user"}


@pytest.mark.parametrize("username,password", [("test-user", "wrong-password"),
                                                ("wrong-user", TEST_PASSWORD),
                                                ("wrong-user", "wrong-password"),
                                                ("名字", TEST_PASSWORD)])
async def test_bad_credentials_are_generic(client, username, password, caplog):
    response = await client.post("/auth/login", json={"username": username, "password": password})
    assert_unauthorized(response)
    assert password not in response.text
    assert password not in caplog.text
    assert username not in response.text


@pytest.mark.parametrize("body", [{}, {"username": "test-user"},
    {"password": "secret-value"}, {"username": "", "password": "secret-value"},
    {"username": "x" * 101, "password": "secret-value"},
    {"username": "test-user", "password": ""},
    {"username": "test-user", "password": "p" * 1025},
    {"username": "test-user", "password": 42},
    {"username": "test-user", "password": "secret-value", "extra": "secret-value"},
    ["secret-value"]])
async def test_invalid_login_body_does_not_echo_input(client, body):
    response = await client.post("/auth/login", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "secret-value" not in response.text
    assert "p" * 1025 not in response.text
    assert response.headers["Cache-Control"] == "no-store"


async def test_malformed_json_is_safe(client):
    response = await client.post("/auth/login", content='{"password":"secret-value",',
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "secret-value" not in response.text


async def test_unknown_username_still_verifies_password(client, monkeypatch):
    calls = []
    def verify(password, encoded):
        calls.append(password)
        return True
    monkeypatch.setattr("app.auth.PASSWORD_HASHER.verify", verify)
    response = await client.post("/auth/login", json={"username": "unknown", "password": TEST_PASSWORD})
    assert_unauthorized(response)
    assert calls == [TEST_PASSWORD]


async def test_unexpected_errors_are_safe_and_correlated(auth_settings, caplog):
    import httpx
    from app.main import create_app
    app = create_app(auth_settings)
    @app.get("/test-error")
    def error():
        raise RuntimeError("private-value-should-not-leak")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test",
    ) as client:
        response = await client.get("/test-error")
    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "private-value-should-not-leak" not in response.text + caplog.text
    assert response.headers["X-Request-ID"] in caplog.text


@pytest.mark.parametrize("authorization", [None, "", "Bearer", "Basic abc", "Bearer invalid",
                                            "Bearer " + "x" * 8193])
async def test_missing_or_malformed_authorization(client, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    assert_unauthorized(await client.get("/auth/me", headers=headers))


@pytest.mark.parametrize("field", ["sub", "iat", "exp", "iss", "aud"])
async def test_missing_required_token_field(client, auth_settings, field):
    payload = claims()
    del payload[field]
    token = jwt.encode(payload, auth_settings.jwt_secret.get_secret_value(), algorithm="HS256")
    assert_unauthorized(await client.get("/auth/me", headers={"Authorization": "Bearer " + token}))


@pytest.mark.parametrize("field,value", [
    ("sub", "other-user"), ("sub", 42), ("iss", "other-issuer"), ("aud", "other-api"),
    ("aud", [AUDIENCE]), ("exp", 0), ("exp", None), ("iat", None),
    ("exp", "not-a-date"), ("iat", "not-a-date"), ("iat", True),
    ("iat", 9999999999), ("exp", 9999999999), ("exp", float("inf")),
])
async def test_invalid_signed_claims_rejected(client, auth_settings, field, value):
    payload = claims()
    payload[field] = value
    token = jwt.encode(payload, auth_settings.jwt_secret.get_secret_value(), algorithm="HS256")
    assert_unauthorized(await client.get("/auth/me", headers={"Authorization": "Bearer " + token}))


@pytest.mark.parametrize("algorithm", ["HS384", "none"])
async def test_other_algorithms_rejected(client, auth_settings, algorithm):
    key = "" if algorithm == "none" else auth_settings.jwt_secret.get_secret_value()
    token = jwt.encode(claims(), key, algorithm=algorithm)
    assert_unauthorized(await client.get("/auth/me", headers={"Authorization": "Bearer " + token}))


async def test_wrong_signature_rejected(client):
    token = jwt.encode(claims(), secrets.token_urlsafe(48), algorithm="HS256")
    assert_unauthorized(await client.get("/auth/me", headers={"Authorization": "Bearer " + token}))


async def test_token_tampering_rejected(client, auth_settings):
    token = create_access_token("test-user", auth_settings)
    header, _, signature = token.split(".")
    fake_payload = jwt.encode({**claims(), "sub": "another-user"}, "", algorithm="none").split(".")[1]
    tampered = ".".join((header, fake_payload, signature))
    assert_unauthorized(await client.get("/auth/me", headers={"Authorization": "Bearer " + tampered}))


@pytest.mark.parametrize("path,method", [("/runs", "post"), ("/runs/r", "get"),
    ("/runs/r/events", "get"), ("/runs/r/report", "get"), ("/runs/r/chat", "post")])
async def test_every_run_route_requires_authentication(client, auth_settings, path, method):
    assert_unauthorized(await getattr(client, method)(path))
    token = create_access_token("test-user", auth_settings)
    response = await getattr(client, method)(path, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 501


async def test_openapi_exposes_bearer_auth_only_on_protected_routes(client):
    schema = (await client.get("/openapi.json")).json()
    assert schema["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "security" not in schema["paths"]["/auth/login"]["post"]
    assert schema["paths"]["/auth/me"]["get"]["security"] == [{"HTTPBearer": []}]


def test_cannot_issue_token_for_unknown_user(auth_settings):
    with pytest.raises(ValueError, match="unknown demo user"):
        create_access_token("another-user", auth_settings)

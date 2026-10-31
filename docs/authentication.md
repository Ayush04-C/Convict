# Authentication

One configured demo account signs in with username/password and receives a
one-hour HS256 JWT. No user database or provider key is required for this stage.
These are Leo API credentials, separate from your GitHub account or provider keys.

## Configure and run

From `Leo/`, with the virtual environment active:

```sh
python scripts/init_env.py
python -m scripts.configure_auth
make run
```

The setup command asks for a password (12–1024 characters) and confirmation through
hidden prompts. It generates an Argon2id hash and random signing secret and writes
missing settings atomically to `.env` with owner-only file permissions. It preserves
other configuration, comments and already configured auth values. Rerunning it does
not rotate credentials. A mismatch, invalid existing auth value or interrupted
prompt leaves the file unchanged.

| Variable | Meaning |
|---|---|
| `DEMO_USER` | Configured demo username; defaults to `ayush` |
| `DEMO_PASS_HASH` | Argon2id encoded hash; do not put a plaintext password here |
| `JWT_SECRET` | Random signing secret of at least 32 bytes |

The API fails early with variable names/setup guidance if auth configuration is
invalid. `.env` is ignored by Git. Exported shell variables take precedence over
`.env`; unset stale exports if the server appears to ignore a changed file.
Restart the server after changes. Tests and the offline demo need no auth setup.

## Endpoints

`POST /auth/login` accepts JSON:

```json
{"username": "ayush", "password": "your-configured-password"}
```

Success: `200` with `access_token`, `token_type: "bearer"`, and `expires_in: 3600`.
Auth responses use `Cache-Control: no-store` and `Pragma: no-cache`.

Send `Authorization: Bearer <access_token>` on protected requests.
`GET /auth/me` returns `{"username":"ayush"}` for the configured user.
All `/runs` routes require this dependency, including events, report and chat.
Public endpoints are login, health, API docs and the OpenAPI schema.

## Manual check in API docs

1. Open `http://127.0.0.1:8000/docs` after `make run` starts.
2. Call `/auth/me` before authorizing: expect **401**.
3. Call `/auth/login` with the configured username and a wrong password: expect **401**.
4. Log in with the correct password: expect **200**; copy only `access_token`.
5. Click **Authorize**, paste the token without the `Bearer ` prefix, then authorize.
6. Call `/auth/me`: expect **200** with your username.
7. Call `POST /runs` or `GET /runs/example`: expect **501**. Auth passed; analysis is unfinished.
8. Clear authorization or replace the token with an invalid value: protected calls return **401**.
9. Submit a login missing its password: expect **422**, with no submitted input echoed.

Expired-token behavior is covered by automated tests; manual tokens expire one hour
after issue. The server uses HTTP locally; deployment would require HTTPS.

## Errors and token checks

401 uses `WWW-Authenticate: Bearer` and a generic response for wrong credentials,
missing bearer headers, expired/altered tokens, or invalid claims:

```json
{"error":{"code":"UNAUTHORIZED","message":"Invalid credentials or access token.","request_id":"..."}}
```

422 uses `VALIDATION_ERROR`. Request IDs also appear in `X-Request-ID`. Unexpected
errors return a generic 500; internal logs contain exception type and request ID,
not exception contents, passwords, signing secrets or bearer tokens.

Verification allows HS256 only and requires `sub`, `iat`, `exp`, `iss=leo` and
`aud=leo-api`. Subject must match `DEMO_USER`; timestamps must be integers. Issue time
cannot be in the future, expiry must be in the future, and lifetime must be positive
and no longer than one hour. Tokens
are signed, not encrypted, and contain no passwords or provider credentials.

## Scope and next stage

Registration, refresh tokens and server-side logout/revocation are not implemented.
Log in again after expiration. Changing the password does not revoke existing JWTs;
rotating `JWT_SECRET` and restarting invalidates all previous tokens. This is a
local hackathon demo, with no login rate limiter yet; do not expose it publicly as-is.

Run ownership checks require the upcoming ledger/API stages. JWT authentication
alone does not implement those checks. Clients must send SSE bearer tokens in
headers using fetch-based streaming, never in URLs.

## Verification

```sh
make test
make lint
make demo
```

Auth tests cover login, malformed input, signature/algorithm/claim failures,
expiration, route protection, startup errors, safe error logs, setup preservation,
password confirmation, interrupted input and atomic-write failure. Tests use
isolated fake credentials and make no external calls.

Validated locally: **111 tests passed**, Ruff passed, offline demo passed, and a
temporary real HTTP server passed login/401/identity/501 smoke checks. The smoke
check used in-memory credentials and did not change the developer's `.env`.

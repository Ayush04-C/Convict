"""Demo-account authentication. No provider clients or ledger dependency."""
from datetime import datetime, timedelta, timezone
from secrets import compare_digest

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

from app.schemas import LoginRequest, TokenResponse, UserResponse
from app.settings import Settings

ALGORITHM = "HS256"
ISSUER = "leo"
AUDIENCE = "leo-api"
TOKEN_SECONDS = 3600
PASSWORD_HASHER = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False, description="Paste the access_token returned by POST /auth/login")
router = APIRouter(prefix="/auth", tags=["Authentication"])


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Invalid credentials or access token.",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


def create_access_token(user: str, settings: Settings) -> str:
    if user != settings.demo_user:
        raise ValueError("Cannot issue a token for an unknown demo user")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user, "iat": now, "exp": now + timedelta(seconds=TOKEN_SECONDS),
         "iss": ISSUER, "aud": AUDIENCE},
        settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM,
    )


def get_current_user(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if credentials is None or len(credentials.credentials) > 8192:
        raise unauthorized()
    settings = request.app.state.settings
    try:
        payload = jwt.decode(
            credentials.credentials, settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM], issuer=ISSUER, audience=AUDIENCE,
            options={"require": ["sub", "iat", "exp", "iss", "aud"], "strict_aud": True},
        )
        if (payload["sub"] != settings.demo_user
                or type(payload["iat"]) is not int or type(payload["exp"]) is not int
                or not 0 < payload["exp"] - payload["iat"] <= TOKEN_SECONDS):
            raise unauthorized()
    except jwt.InvalidTokenError:
        raise unauthorized() from None
    return payload["sub"]


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, response: Response) -> TokenResponse:
    settings = request.app.state.settings
    # Do the same expensive verification even when the username is incorrect.
    valid_password = PASSWORD_HASHER.verify(
        body.password.get_secret_value(), settings.demo_pass_hash.get_secret_value(),
    )
    valid_user = compare_digest(body.username.encode(), settings.demo_user.encode())
    if not (valid_password and valid_user):
        raise unauthorized()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return TokenResponse(access_token=create_access_token(settings.demo_user, settings))


@router.get("/me", response_model=UserResponse)
def me(user: str = Depends(get_current_user)) -> UserResponse:
    return UserResponse(username=user)

"""Demo-account authentication. No provider clients or ledger dependency."""
import time
from datetime import datetime, timedelta, timezone
from secrets import compare_digest

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash

from app import users
from app.schemas import LoginRequest, SignupRequest, TokenResponse, UserResponse
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
    if user != settings.demo_user and users.get_password_hash(settings.database_path, user) is None:
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
        if (not isinstance(payload["sub"], str)
                or (payload["sub"] != settings.demo_user
                    and users.get_password_hash(settings.database_path, payload["sub"]) is None)
                or type(payload["iat"]) is not int or type(payload["exp"]) is not int
                or not 0 < payload["exp"] - payload["iat"] <= TOKEN_SECONDS):
            raise unauthorized()
    except jwt.InvalidTokenError:
        raise unauthorized() from None
    return payload["sub"]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, response: Response) -> TokenResponse:
    settings = request.app.state.settings
    demo_hash = settings.demo_pass_hash.get_secret_value()
    password = body.password.get_secret_value()
    if compare_digest(body.username.encode(), settings.demo_user.encode()):
        user, stored = settings.demo_user, demo_hash
    else:
        user = users.normalize(body.username)
        stored = users.get_password_hash(settings.database_path, user)
    # An unknown name still pays for one verification, so timing does not reveal who has an account.
    valid = PASSWORD_HASHER.verify(password, stored or demo_hash) and stored is not None
    if not valid:
        raise unauthorized()
    _no_store(response)
    return TokenResponse(access_token=create_access_token(user, settings))


SIGNUP_WINDOW_SECONDS = 3600
SIGNUP_LIMIT = 10
_signups: dict[str, list[float]] = {}


def _rate_limited(client: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _signups.get(client, []) if now - t < SIGNUP_WINDOW_SECONDS]
    limited = len(recent) >= SIGNUP_LIMIT
    if not limited:
        recent.append(now)
    _signups[client] = recent
    if len(_signups) > 1000:
        for key in [k for k, v in _signups.items() if not v or now - v[-1] >= SIGNUP_WINDOW_SECONDS]:
            del _signups[key]
    return limited


@router.post("/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest, request: Request, response: Response) -> TokenResponse:
    settings = request.app.state.settings
    username = users.normalize(body.username)
    password = body.password.get_secret_value()
    if not users.USERNAME_PATTERN.fullmatch(username):
        raise HTTPException(422, "Username must be 3 to 32 characters: letters, digits, dot, dash or underscore.")
    if len(password) < users.MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"Password must be at least {users.MIN_PASSWORD_LENGTH} characters.")
    if _rate_limited(request.client.host if request.client else "unknown"):
        raise HTTPException(429, "Too many sign-ups from this address. Try again later.",
                            headers={"Retry-After": str(SIGNUP_WINDOW_SECONDS)})
    if username == settings.demo_user.lower():
        raise HTTPException(409, "That username is taken.")
    try:
        users.create_user(settings.database_path, username, PASSWORD_HASHER.hash(password))
    except users.UsernameTaken:
        raise HTTPException(409, "That username is taken.") from None
    _no_store(response)
    return TokenResponse(access_token=create_access_token(username, settings))


@router.get("/me", response_model=UserResponse)
def me(user: str = Depends(get_current_user)) -> UserResponse:
    return UserResponse(username=user)

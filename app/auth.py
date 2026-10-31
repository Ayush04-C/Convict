"""Ayush: JWT creation/verification is intentionally not implemented yet."""
from fastapi import HTTPException


async def get_current_user() -> str:
    raise HTTPException(501, "Stage 2: JWT authentication is not implemented")


def create_access_token(user: str) -> str:
    raise NotImplementedError("Stage 2: HS256 JWT with expiry")

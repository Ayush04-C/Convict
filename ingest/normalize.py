"""Daksh: replace this identity stub with unit/date/currency normalization."""
from app.schemas import ClaimSet


async def normalize_claims(claims: ClaimSet) -> ClaimSet:
    return claims.model_copy(update={"is_stub": True})

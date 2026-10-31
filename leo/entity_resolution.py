"""Ayush: resolve against claims; never choose tied matches by confidence alone."""
from app.schemas import ClaimSet, Resolution


async def resolve_company(name: str, domain: str | None, claims: ClaimSet, client) -> Resolution:
    raise NotImplementedError("Stage 5: resolution, disambiguation and bounded web fallback")

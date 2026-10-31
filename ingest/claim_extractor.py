"""Daksh: replace fixture loading with Gemini document extraction."""
from pathlib import Path

from app.schemas import ClaimSet, DeckContent


async def extract_claims(deck: DeckContent) -> ClaimSet:
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/sample_claims.json"
    return ClaimSet.model_validate_json(fixture.read_text()).model_copy(update={"is_stub": True})

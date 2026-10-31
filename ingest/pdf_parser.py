"""Daksh: implement real PDF prechecks here; no validation is claimed by this stub."""
from app.schemas import DeckContent


async def parse_deck(path: str) -> DeckContent:
    return DeckContent(path=path, page_count=0, processed_pages=0,
                       warnings=("STUB: PDF not inspected",), is_stub=True)

"""Daksh: PDF prechecks. No model and no network; rejects what we cannot process safely.

Limits follow docs/contracts.md: PDF only, 20 MB, 30 processed pages. Anything
unreadable raises DeckRejectedError, whose message is safe to show to the user.
The pipeline turns it into a failed-run event (the HTTP 202 has already been sent).
"""
import asyncio
from pathlib import Path

from app.schemas import DeckContent

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 30


class DeckRejectedError(ValueError):
    """The uploaded deck cannot be processed; str(error) is user-safe."""


def _inspect(path: str) -> DeckContent:
    file = Path(path)
    try:
        size = file.stat().st_size
        with file.open("rb") as handle:
            head = handle.read(1024)
    except OSError:
        raise DeckRejectedError("The deck file could not be read.") from None
    if size == 0:
        raise DeckRejectedError("The deck file is empty.")
    if size > MAX_BYTES:
        raise DeckRejectedError("The deck is larger than the 20 MB limit.")
    if b"%PDF-" not in head:
        raise DeckRejectedError("Only PDF decks are supported.")

    try:
        import pymupdf
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is not installed; run pip install -r requirements-features.txt") from exc

    try:
        doc = pymupdf.open(str(file))
    except Exception:
        raise DeckRejectedError("The PDF is damaged or could not be opened.") from None
    try:
        if doc.needs_pass:
            raise DeckRejectedError("The PDF is password protected.")
        page_count = doc.page_count
        if page_count == 0:
            raise DeckRejectedError("The PDF has no pages.")
        processed = min(page_count, MAX_PAGES)
        warnings: list[str] = []
        if page_count > MAX_PAGES:
            warnings.append(f"Deck has {page_count} pages; only the first {MAX_PAGES} are processed.")
        if not any(doc[i].get_text().strip() for i in range(processed)):
            warnings.append("No text layer found; extraction relies on page images.")
    except DeckRejectedError:
        raise
    except Exception:
        raise DeckRejectedError("The PDF is damaged or could not be read.") from None
    finally:
        doc.close()
    return DeckContent(path=path, page_count=page_count, processed_pages=processed,
                       warnings=tuple(warnings))


async def parse_deck(path: str) -> DeckContent:
    return await asyncio.to_thread(_inspect, path)

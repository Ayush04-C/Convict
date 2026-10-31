"""PDF prechecks: text and image-only decks, rejected inputs, size and page limits.

Needs PyMuPDF (requirements-features.txt); skipped where it is not installed.
All PDFs are generated in tmp_path, so no binary fixtures are committed.
"""
import pytest

pymupdf = pytest.importorskip("pymupdf")

from ingest.pdf_parser import MAX_BYTES, MAX_PAGES, DeckRejectedError, parse_deck  # noqa: E402


def make_pdf(path, pages=1, text="Hello deck", **save_kwargs):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(str(path), **save_kwargs)
    doc.close()
    return str(path)


async def test_text_pdf_reports_pages_and_no_warnings(tmp_path):
    deck = await parse_deck(make_pdf(tmp_path / "deck.pdf", pages=3))
    assert (deck.page_count, deck.processed_pages) == (3, 3)
    assert deck.warnings == ()
    assert deck.is_stub is False
    assert deck.path.endswith("deck.pdf")


async def test_image_only_pdf_is_accepted_with_warning(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pix.set_rect(pix.irect, (200, 30, 30))
    page.insert_image(pymupdf.Rect(72, 72, 172, 172), pixmap=pix)
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    deck = await parse_deck(str(path))
    assert deck.processed_pages == 1
    assert any("page images" in w for w in deck.warnings)


async def test_page_cap_is_recorded_as_truncation(tmp_path):
    deck = await parse_deck(make_pdf(tmp_path / "long.pdf", pages=MAX_PAGES + 5))
    assert deck.page_count == MAX_PAGES + 5
    assert deck.processed_pages == MAX_PAGES
    assert any(str(MAX_PAGES) in w for w in deck.warnings)


async def test_password_protected_pdf_is_rejected(tmp_path):
    path = make_pdf(tmp_path / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
                    user_pw="secret", owner_pw="owner")
    with pytest.raises(DeckRejectedError, match="password"):
        await parse_deck(path)


async def test_oversize_file_is_rejected_before_opening(tmp_path):
    path = tmp_path / "huge.pdf"
    path.write_bytes(b"%PDF-1.7\n" + b"0" * MAX_BYTES)
    with pytest.raises(DeckRejectedError, match="20 MB"):
        await parse_deck(str(path))


async def test_non_pdf_is_rejected(tmp_path):
    path = tmp_path / "notes.pdf"
    path.write_text("this is plain text, not a deck")
    with pytest.raises(DeckRejectedError, match="Only PDF"):
        await parse_deck(str(path))


async def test_damaged_pdf_is_rejected(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< garbage that never closes")
    with pytest.raises(DeckRejectedError):
        await parse_deck(str(path))


async def test_empty_and_missing_files_are_rejected(tmp_path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(DeckRejectedError, match="empty"):
        await parse_deck(str(empty))
    with pytest.raises(DeckRejectedError, match="could not be read"):
        await parse_deck(str(tmp_path / "missing.pdf"))

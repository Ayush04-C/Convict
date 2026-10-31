"""The three synthetic demo decks: valid, labelled, and exactly what the demo script relies on."""
from pathlib import Path

import pymupdf
import pytest

from ingest.pdf_parser import parse_deck
from scripts.make_demo_decks import DECKS, FAKE, HONEST, INFLATED, main

COMMITTED = Path(__file__).resolve().parents[1] / "demo" / "decks"


def page_texts(path):
    with pymupdf.open(str(path)) as doc:
        return [page.get_text() for page in doc]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("decks")
    main(str(out))
    return out


@pytest.mark.parametrize("deck", DECKS, ids=lambda d: d.filename)
async def test_each_deck_passes_the_pdf_precheck_with_no_warnings(built, deck):
    parsed = await parse_deck(str(built / deck.filename))
    assert parsed.page_count == 1 + len(deck.slides) == parsed.processed_pages
    assert 9 <= parsed.page_count <= 12 and parsed.warnings == ()


@pytest.mark.parametrize("deck", DECKS, ids=lambda d: d.filename)
def test_every_page_carries_the_synthetic_disclaimer(built, deck):
    for number, text in enumerate(page_texts(built / deck.filename), start=1):
        assert " ".join(deck.disclaimer.split()[:5]) in " ".join(text.split()), f"page {number}"
        assert "SYNTHETIC DEMO DECK" in text


@pytest.mark.parametrize("deck", DECKS, ids=lambda d: d.filename)
def test_the_phrases_the_demo_depends_on_are_in_the_text_layer(built, deck):
    text = " ".join(" ".join(page_texts(built / deck.filename)).split())
    for phrase in deck.expected:
        assert phrase in text, phrase


@pytest.mark.parametrize("deck", DECKS, ids=lambda d: d.filename)
def test_decks_are_plain_ascii_so_extraction_never_trips_on_symbols(built, deck):
    assert "".join(page_texts(built / deck.filename)).isascii()


def test_the_inflated_deck_has_the_honest_decks_structure_with_different_figures(built):
    assert len(HONEST.slides) == len(INFLATED.slides)
    honest = " ".join(" ".join(page_texts(built / HONEST.filename)).split())
    inflated = " ".join(" ".join(page_texts(built / INFLATED.filename)).split())
    for true_figure in ("Founded in 2017", "San Francisco, California", "About 400", "$141M", "$35M"):
        assert true_figure in honest and true_figure not in inflated
    for false_figure in ("Founded in 2012", "Austin, Texas", "1,200", "$400M", "$200M"):
        assert false_figure in inflated and false_figure not in honest
    assert "DELIBERATELY FALSE" in inflated and "DELIBERATELY FALSE" not in honest


def test_the_fake_company_deck_names_a_company_that_does_not_exist(built):
    text = " ".join(page_texts(built / FAKE.filename))
    assert "This company does not exist" in text and "Retool" not in text
    assert FAKE.company == "Orrery Vantage Systems GmbH"


def test_decks_are_never_mistakable_for_a_company_statement(built):
    for deck in (HONEST, INFLATED):
        assert "Not issued by Retool" in deck.disclaimer


@pytest.mark.parametrize("deck", DECKS, ids=lambda d: d.filename)
def test_the_committed_decks_match_the_generator(built, deck):
    committed = COMMITTED / deck.filename
    assert committed.exists(), "run: python -m scripts.make_demo_decks"
    assert page_texts(committed) == page_texts(built / deck.filename)


def test_text_that_does_not_fit_is_an_error_not_silently_clipped(tmp_path):
    from dataclasses import replace

    from scripts.make_demo_decks import Slide, build

    too_much = replace(HONEST, slides=(Slide("k", "t", tuple(f"Bullet number {i} " * 6 for i in range(12))),))
    with pytest.raises(ValueError, match="does not fit"):
        build(too_much, tmp_path / "x.pdf")

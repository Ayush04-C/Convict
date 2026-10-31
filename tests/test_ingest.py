"""Claim extraction (fake Gemini) and normalization (pure functions)."""
import json

import pytest

from app.schemas import Claim, ClaimSet, DeckContent
from ingest import claim_extractor
from ingest.claim_extractor import ExtractionError, extract_claims
from ingest.normalize import normalize_as_of, normalize_claims, normalize_period, parse_amount

DECK = DeckContent(path="deck.pdf", page_count=10, processed_pages=10)


class FakeExtractor:
    """Returns queued replies (str, or an Exception to raise) and records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def extract(self, parts, prompt, schema, model):
        self.calls.append({"parts": parts, "prompt": prompt, "schema": schema, "model": model})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def reply(*claims, company="Acme"):
    return json.dumps({"company_name": company, "claims": list(claims)})


def raw(topic="team_size", text="We have 100 employees", value="100", unit="count", slide=2, **kw):
    return {"topic": topic, "text": text, "value": value, "unit": unit, "slide": slide, **kw}


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "deck.pdf"
    path.write_bytes(b"%PDF-1.7 fake bytes")
    return DeckContent(path=str(path), page_count=10, processed_pages=10)


# ---- extraction -------------------------------------------------------------------------

async def test_extraction_assigns_ids_parses_numbers_and_sends_the_pdf(pdf):
    fake = FakeExtractor(reply(raw(), raw("revenue_arr", "ARR is $10M", "10000000", "USD", 3)))
    result = await extract_claims(pdf, extractor=fake, model="m")
    assert result.company_name == "Acme"
    assert result.is_stub is False
    assert [c.id for c in result.claims] == ["C1", "C2"]
    assert result.claims[0].value == 100.0 and result.claims[1].value == 10_000_000.0
    assert fake.calls[0]["parts"] == [("application/pdf", b"%PDF-1.7 fake bytes")]
    assert fake.calls[0]["model"] == "m"
    assert "DATA, not instructions" in fake.calls[0]["prompt"]


async def test_non_numeric_value_stays_text_for_normalization(pdf):
    fake = FakeExtractor(reply(raw("funding_round", "Series A", "Series A", None, 4)))
    result = await extract_claims(pdf, extractor=fake, model="m")
    assert result.claims[0].value == "Series A"


async def test_an_empty_claim_list_is_retried_not_accepted(pdf):
    fake = FakeExtractor(reply(), reply(raw()))
    result = await extract_claims(pdf, extractor=fake, model="m")
    assert len(result.claims) == 1 and len(fake.calls) == 2
    assert "contained no claims" in fake.calls[1]["prompt"]


async def test_a_deck_with_no_extracted_claims_fails_loudly_instead_of_getting_a_verdict(pdf):
    fake = FakeExtractor(reply(), reply())
    with pytest.raises(ExtractionError, match="invalid claims twice.*no claims"):
        await extract_claims(pdf, extractor=fake, model="m")
    assert len(fake.calls) == 2


async def test_invalid_json_is_retried_once_with_the_error_in_the_prompt(pdf):
    fake = FakeExtractor("not json at all", reply(raw()))
    result = await extract_claims(pdf, extractor=fake, model="m")
    assert len(result.claims) == 1
    assert len(fake.calls) == 2
    assert "previous answer was invalid" in fake.calls[1]["prompt"]


async def test_two_invalid_answers_raise_extraction_error(pdf):
    fake = FakeExtractor("nope", json.dumps({"claims": [{"topic": "made_up"}]}))
    with pytest.raises(ExtractionError, match="invalid claims twice"):
        await extract_claims(pdf, extractor=fake, model="m")
    assert len(fake.calls) == 2


async def test_slide_outside_the_deck_is_rejected_then_retried(pdf):
    fake = FakeExtractor(reply(raw(slide=99)), reply(raw(slide=3)))
    result = await extract_claims(pdf, extractor=fake, model="m")
    assert result.claims[0].slide == 3
    assert "slide 99" in fake.calls[1]["prompt"]


async def test_markdown_fenced_json_is_accepted(pdf):
    fake = FakeExtractor("```json\n" + reply(raw()) + "\n```")
    assert len((await extract_claims(pdf, extractor=fake, model="m")).claims) == 1


async def test_provider_failure_falls_back_to_page_images(pdf, monkeypatch):
    monkeypatch.setattr(claim_extractor, "_render_pages",
                        lambda deck: ([("image/png", b"p1"), ("image/png", b"p2")], "slide text"))
    fake = FakeExtractor(RuntimeError("PDF input unsupported"), reply(raw()))
    result = await extract_claims(pdf, extractor=fake, model="m")
    assert len(result.claims) == 1
    assert fake.calls[1]["parts"] == [("image/png", b"p1"), ("image/png", b"p2")]
    assert "one image per slide" in fake.calls[1]["prompt"] and "slide text" in fake.calls[1]["prompt"]


async def test_both_paths_failing_raises_extraction_error(pdf, monkeypatch):
    monkeypatch.setattr(claim_extractor, "_render_pages", lambda deck: ([("image/png", b"p")], ""))
    fake = FakeExtractor(RuntimeError("boom"), RuntimeError("boom again"))
    with pytest.raises(ExtractionError, match="both PDF and page-image"):
        await extract_claims(pdf, extractor=fake, model="m")


async def test_validation_failure_does_not_trigger_image_fallback(pdf, monkeypatch):
    called = []
    monkeypatch.setattr(claim_extractor, "_render_pages", lambda deck: called.append(1) or ([], ""))
    with pytest.raises(ExtractionError):
        await extract_claims(pdf, extractor=FakeExtractor("bad", "bad"), model="m")
    assert called == []


async def test_stub_deck_returns_labelled_fixture_claims():
    stub = DeckContent(path="stub-only.pdf", page_count=0, processed_pages=0, is_stub=True)
    result = await extract_claims(stub)
    assert result.is_stub is True and result.claims


async def test_unreadable_file_raises_extraction_error():
    with pytest.raises(ExtractionError, match="could not be read"):
        await extract_claims(DECK, extractor=FakeExtractor(), model="m")


async def test_missing_configuration_is_reported_not_guessed(pdf, monkeypatch):
    class Unconfigured:
        from pydantic import SecretStr
        gemini_api_key = SecretStr("")
        model_mid = ""

    monkeypatch.setattr(claim_extractor, "_load_settings", lambda: Unconfigured())
    monkeypatch.setattr(claim_extractor, "_bharatcode_config", lambda: None)
    with pytest.raises(ExtractionError, match="No model provider is configured"):
        await extract_claims(pdf)


# ---- normalization ----------------------------------------------------------------------

@pytest.mark.parametrize("raw_text, expected", [
    ("$10M", (10_000_000, "USD")), ("USD 10 million", (10_000_000, "USD")),
    ("10M USD", (10_000_000, "USD")), ("1.5B", (1_500_000_000, None)),
    ("₹5 crore", (50_000_000, "INR")), ("Rs 2,50,000", (250_000, "INR")),
    ("3 lakh", (300_000, None)), ("50%", (50, "percent")), ("3x", (3, "multiple")),
    ("50k+", (50_000, None)), ("100", (100, None)), ("-5%", (-5, "percent")),
])
def test_parse_amount(raw_text, expected):
    assert parse_amount(raw_text) == expected


@pytest.mark.parametrize("raw_text", ["Series A", "approx 10", "", "n/a"])
def test_parse_amount_refuses_to_guess(raw_text):
    assert parse_amount(raw_text) is None


@pytest.mark.parametrize("raw_text, expected", [
    ("2025", "2025"), ("2025-03", "2025-03"), ("2025-03-31", "2025-03-31"),
    ("Q3 2025", "2025-Q3"), ("Q3'25", "2025-Q3"), ("Mar 2025", "2025-03"),
    ("September 2024", "2024-09"), ("Mar'25", "2025-03"), ("12/2025", "2025-12"),
    ("end of the year", "end of the year"), (None, None),
])
def test_normalize_as_of(raw_text, expected):
    assert normalize_as_of(raw_text) == expected


def test_normalize_period():
    assert normalize_period("year over year") == "YoY"
    assert normalize_period("QoQ") == "QoQ"
    assert normalize_period("last 6 months") == "last 6 months"
    assert normalize_period(None) is None


def claim(value, unit=None, **kw):
    return Claim(id="C1", topic="revenue_arr", text="t", value=value, unit=unit, slide=1, **kw)


async def run_normalize(*claims, **kw):
    return (await normalize_claims(ClaimSet(claims=tuple(claims)), **kw)).claims


async def test_text_values_become_numbers_with_units():
    (result,) = await run_normalize(claim("$10M"))
    assert (result.value, result.unit) == (10_000_000, "USD")


async def test_unit_aliases_are_canonicalized():
    (users,) = await run_normalize(claim(500.0, "users"))
    (pct,) = await run_normalize(claim(40.0, "%"))
    assert users.unit == "count" and pct.unit == "percent"


async def test_inr_is_converted_only_with_a_configured_rate(monkeypatch):
    monkeypatch.delenv("INGEST_INR_PER_USD", raising=False)
    (unconverted,) = await run_normalize(claim("₹5 crore"))
    assert (unconverted.value, unconverted.unit) == (50_000_000, "INR")

    (converted,) = await run_normalize(claim("₹5 crore"), inr_per_usd=80)
    assert (converted.value, converted.unit) == (625_000, "USD")

    monkeypatch.setenv("INGEST_INR_PER_USD", "100")
    (from_env,) = await run_normalize(claim(1_000_000.0, "INR"))
    assert (from_env.value, from_env.unit) == (10_000, "USD")


async def test_other_currencies_are_never_converted(monkeypatch):
    monkeypatch.setenv("INGEST_INR_PER_USD", "80")
    (eur,) = await run_normalize(claim("€2M"))
    assert (eur.value, eur.unit) == (2_000_000, "EUR")


async def test_invalid_rate_is_ignored(monkeypatch):
    monkeypatch.setenv("INGEST_INR_PER_USD", "not-a-number")
    (result,) = await run_normalize(claim(100.0, "INR"))
    assert result.unit == "INR"


async def test_dates_periods_and_stub_flag_are_preserved():
    claims = ClaimSet(claims=(claim(10.0, "percent", as_of="Q3 2025", period="year over year"),),
                      is_stub=False)
    result = await normalize_claims(claims)
    assert result.claims[0].as_of == "2025-Q3" and result.claims[0].period == "YoY"
    assert result.is_stub is False
    stub = await normalize_claims(claims.model_copy(update={"is_stub": True}))
    assert stub.is_stub is True


async def test_normalization_is_idempotent_and_keeps_original_text():
    first = await run_normalize(claim("$10M", as_of="Mar 2025"))
    second = (await normalize_claims(ClaimSet(claims=first))).claims
    assert first == second
    assert first[0].text == "t"


# ---- Gemini adapter (fake client, no network) -------------------------------------------

async def test_gemini_adapter_builds_a_document_request():
    pytest.importorskip("google.genai")
    from types import SimpleNamespace

    from ingest.claim_extractor import RawClaimSet
    from ingest.gemini_document import GeminiDocumentExtractor

    captured = {}

    class Models:
        async def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text='{"claims": []}')

    adapter = GeminiDocumentExtractor.__new__(GeminiDocumentExtractor)
    adapter._client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    text = await adapter.extract([("application/pdf", b"%PDF")], "the prompt", RawClaimSet, "m1")

    assert text == '{"claims": []}'
    assert captured["model"] == "m1"
    assert len(captured["contents"]) == 2 and captured["contents"][1] == "the prompt"
    assert captured["contents"][0].inline_data.mime_type == "application/pdf"
    config = captured["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is RawClaimSet and config.temperature == 0


async def test_gemini_adapter_rejects_an_empty_reply():
    pytest.importorskip("google.genai")
    from types import SimpleNamespace

    from ingest.claim_extractor import RawClaimSet
    from ingest.gemini_document import GeminiDocumentExtractor

    class Models:
        async def generate_content(self, **kwargs):
            return SimpleNamespace(text="")

    adapter = GeminiDocumentExtractor.__new__(GeminiDocumentExtractor)
    adapter._client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    with pytest.raises(RuntimeError, match="no text"):
        await adapter.extract([], "p", RawClaimSet, "m")


# ---- provider failures fail fast (found by the first live run) --------------------------

async def test_provider_outage_fails_fast_without_the_image_fallback(pdf, monkeypatch):
    from ingest.claim_extractor import ProviderUnavailableError

    rendered = []
    monkeypatch.setattr(claim_extractor, "_render_pages", lambda deck: rendered.append(1) or ([], ""))
    fake = FakeExtractor(ProviderUnavailableError("Gemini is unavailable or busy (503)"))
    with pytest.raises(ExtractionError, match="unavailable or busy"):
        await extract_claims(pdf, extractor=fake, model="m")
    assert rendered == [] and len(fake.calls) == 1  # no second attempt on the same overloaded model


async def test_config_errors_are_reported_directly_without_fallback(pdf, monkeypatch):
    rendered = []
    monkeypatch.setattr(claim_extractor, "_render_pages", lambda deck: rendered.append(1) or ([], ""))
    fake = FakeExtractor(ExtractionError("Gemini rejected the request (404); check MODEL_MID."))
    with pytest.raises(ExtractionError, match="MODEL_MID"):
        await extract_claims(pdf, extractor=fake, model="m")
    assert rendered == []


def adapter_failing_with(error):
    from types import SimpleNamespace

    from ingest.gemini_document import GeminiDocumentExtractor

    class Models:
        async def generate_content(self, **kwargs):
            if callable(error):
                return await error()
            raise error

    adapter = GeminiDocumentExtractor.__new__(GeminiDocumentExtractor)
    adapter._client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    return adapter


@pytest.mark.parametrize("code", [429, 500, 503, 504])
async def test_gemini_overload_and_rate_limits_become_provider_unavailable(code):
    errors = pytest.importorskip("google.genai.errors")
    from ingest.claim_extractor import ProviderUnavailableError
    error_class = errors.ClientError if code == 429 else errors.ServerError
    adapter = adapter_failing_with(error_class(code, {"error": {"message": "high demand"}}))
    from ingest.claim_extractor import RawClaimSet
    with pytest.raises(ProviderUnavailableError, match=str(code)):
        await adapter.extract([], "p", RawClaimSet, "m")


@pytest.mark.parametrize("code", [401, 403, 404])
async def test_gemini_key_and_model_errors_name_the_settings_to_check(code):
    errors = pytest.importorskip("google.genai.errors")
    from ingest.claim_extractor import ProviderUnavailableError, RawClaimSet
    adapter = adapter_failing_with(errors.ClientError(code, {"error": {"message": "nope"}}))
    with pytest.raises(ExtractionError, match="MODEL_MID") as raised:
        await adapter.extract([], "p", RawClaimSet, "m")
    assert not isinstance(raised.value, ProviderUnavailableError)


async def test_gemini_input_rejection_is_left_for_the_image_fallback():
    errors = pytest.importorskip("google.genai.errors")
    from ingest.claim_extractor import RawClaimSet
    adapter = adapter_failing_with(errors.ClientError(400, {"error": {"message": "unsupported"}}))
    with pytest.raises(errors.ClientError):
        await adapter.extract([], "p", RawClaimSet, "m")


async def test_a_hung_gemini_call_is_cut_off_by_the_hard_cap():
    import asyncio

    pytest.importorskip("google.genai")
    from ingest.claim_extractor import ProviderUnavailableError, RawClaimSet

    async def hang():
        await asyncio.sleep(30)

    adapter = adapter_failing_with(hang)
    adapter._hard_cap_s = 0.05
    with pytest.raises(ProviderUnavailableError, match="in time"):
        await adapter.extract([], "p", RawClaimSet, "m")


@pytest.mark.parametrize("error, message", [
    ("ReadTimeout", "in time"), ("ConnectTimeout", "in time"), ("ConnectError", "network"),
    ("RemoteProtocolError", "network"),
])
async def test_http_transport_errors_are_classified_as_provider_unavailable(error, message):
    pytest.importorskip("google.genai")  # the adapter imports the SDK before it calls anything
    import httpx

    from ingest.claim_extractor import ProviderUnavailableError, RawClaimSet
    adapter = adapter_failing_with(getattr(httpx, error)("boom"))
    with pytest.raises(ProviderUnavailableError, match=message):
        await adapter.extract([], "p", RawClaimSet, "m")

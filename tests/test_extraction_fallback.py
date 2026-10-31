"""BharatCode fallback for claim extraction. No network: httpx.MockTransport and fakes only."""
import json

import httpx
import pytest

from app.schemas import DeckContent
from ingest import claim_extractor
from ingest.bharatcode_document import BharatCodeExtractor
from ingest.claim_extractor import (
    ExtractionError, FallbackExtractor, ProviderUnavailableError, RawClaimSet, extract_claims,
)

GOOD = json.dumps({"company_name": "Acme", "claims": [
    {"topic": "team_size", "text": "100 employees", "value": "100", "unit": "count", "slide": 1}]})


def chat_reply(content=GOOD):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def adapter(handler, **kw):
    return BharatCodeExtractor("secret-test-key", transport=httpx.MockTransport(handler), **kw)


class Recorder:
    """Callable MockTransport handler that records requests and replays queued responses."""

    def __init__(self, *responses):
        self.requests, self.responses = [], list(responses)

    def __call__(self, request):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)


async def extract(extractor, parts=(), model="gemini-name"):
    return await extractor.extract(list(parts), "PROMPT", RawClaimSet, model)


# ---- BharatCode adapter ----------------------------------------------------------------

async def test_request_shape_auth_model_and_json_mode():
    rec = Recorder(chat_reply())
    text = await extract(adapter(rec), model="gemini-name")
    request = rec.requests[0]
    body = json.loads(request.content)
    assert text == GOOD
    assert str(request.url) == "https://bharatcode.ai/api/model/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret-test-key"
    assert body["model"] == "qwen-3.8-27b"          # its own model, never the Gemini name
    assert body["response_format"] == {"type": "json_object"} and body["temperature"] == 0
    prompt = body["messages"][0]["content"][0]["text"]
    assert "PROMPT" in prompt and "JSON schema" in prompt and "claims" in prompt


async def test_pdf_part_is_rendered_to_slide_text_and_images(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for text in ("First slide ARR 10M", "Second slide team 50"):
        doc.new_page().insert_text((72, 72), text)
    pdf = doc.tobytes()
    rec = Recorder(chat_reply())
    await extract(adapter(rec), [("application/pdf", pdf)])
    content = json.loads(rec.requests[0].content)["messages"][0]["content"]
    assert "Slide 1 text" in content[0]["text"] and "Second slide team 50" in content[0]["text"]
    images = [part for part in content[1:] if part["type"] == "image_url"]
    assert len(images) == 2 and images[0]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_image_parts_pass_through():
    rec = Recorder(chat_reply())
    await extract(adapter(rec), [("image/png", b"\x89PNG fake")])
    content = json.loads(rec.requests[0].content)["messages"][0]["content"]
    assert content[1]["type"] == "image_url"


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
async def test_overload_codes_are_provider_unavailable(code):
    with pytest.raises(ProviderUnavailableError, match=str(code)):
        await extract(adapter(Recorder(httpx.Response(code, text="busy"))))


@pytest.mark.parametrize("code", [401, 403])
async def test_access_errors_name_the_setting_and_never_leak_the_key(code):
    with pytest.raises(ExtractionError, match="BHARATCODE_API_KEY") as raised:
        await extract(adapter(Recorder(httpx.Response(code, text="plan required"))))
    assert not isinstance(raised.value, ProviderUnavailableError)
    assert "secret-test-key" not in str(raised.value)


async def test_a_400_is_retried_once_without_response_format():
    rec = Recorder(httpx.Response(400, text="unsupported response_format"), chat_reply())
    assert await extract(adapter(rec)) == GOOD
    assert "response_format" in json.loads(rec.requests[0].content)
    assert "response_format" not in json.loads(rec.requests[1].content)


@pytest.mark.parametrize("error, message", [
    (httpx.ReadTimeout("slow"), "in time"), (httpx.ConnectTimeout("slow"), "in time"),
    (httpx.ConnectError("down"), "network"),
])
async def test_timeouts_and_network_errors_are_provider_unavailable(error, message):
    with pytest.raises(ProviderUnavailableError, match=message):
        await extract(adapter(Recorder(error)))


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, text="not json"),
    httpx.Response(418, text="teapot"),
])
async def test_unusable_replies_raise_a_plain_error_not_an_outage(response):
    with pytest.raises(RuntimeError) as raised:
        await extract(adapter(Recorder(response)))
    assert not isinstance(raised.value, ExtractionError)


# ---- FallbackExtractor -----------------------------------------------------------------

class Fake:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def extract(self, parts, prompt, schema, model):
        self.calls.append(model)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_primary_success_never_touches_the_fallback():
    primary, secondary = Fake("from gemini"), Fake("from qwen")
    assert await extract(FallbackExtractor(primary, secondary)) == "from gemini"
    assert secondary.calls == []


async def test_outage_on_primary_falls_back_with_the_secondary_own_model():
    primary, secondary = Fake(ProviderUnavailableError("busy (503)")), Fake("from qwen")
    assert await extract(FallbackExtractor(primary, secondary), model="gemini-name") == "from qwen"
    assert primary.calls == ["gemini-name"] and secondary.calls == [""]


async def test_both_unavailable_is_reported_as_one_outage():
    chain = FallbackExtractor(Fake(ProviderUnavailableError("a")), Fake(ProviderUnavailableError("b")))
    with pytest.raises(ProviderUnavailableError, match="Both Gemini and BharatCode"):
        await extract(chain)


async def test_primary_configuration_errors_are_not_masked_by_the_fallback():
    secondary = Fake("from qwen")
    chain = FallbackExtractor(Fake(ExtractionError("Gemini rejected the request (404)")), secondary)
    with pytest.raises(ExtractionError, match="404"):
        await extract(chain)
    assert secondary.calls == []


async def test_fallback_configuration_errors_are_reported_after_an_outage():
    chain = FallbackExtractor(Fake(ProviderUnavailableError("busy")),
                              Fake(ExtractionError("BharatCode rejected the request (403)")))
    with pytest.raises(ExtractionError, match="fallback failed.*403") as raised:
        await extract(chain)
    assert not isinstance(raised.value, ProviderUnavailableError)


async def test_extract_claims_survives_a_gemini_outage_end_to_end(tmp_path):
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.7 fake")
    deck = DeckContent(path=str(pdf), page_count=3, processed_pages=3)
    chain = FallbackExtractor(Fake(ProviderUnavailableError("Gemini busy (503)")), Fake(GOOD))
    result = await extract_claims(deck, extractor=chain, model="gemini-name")
    assert result.company_name == "Acme" and result.claims[0].topic == "team_size"


# ---- default wiring --------------------------------------------------------------------

class Settings:
    def __init__(self, key="", model=""):
        from pydantic import SecretStr
        self.gemini_api_key, self.model_mid = SecretStr(key), model


BHARAT = {"api_key": "bk", "model": "qwen-3.8-27b", "base_url": "https://bharatcode.ai/api/model/v1"}


def wire(monkeypatch, settings, bharat):
    monkeypatch.setattr(claim_extractor, "_load_settings", lambda: settings)
    monkeypatch.setattr(claim_extractor, "_bharatcode_config", lambda: bharat)


def test_both_configured_gives_gemini_with_bharatcode_fallback(monkeypatch):
    pytest.importorskip("google.genai")
    wire(monkeypatch, Settings("gk", "gemini-3.5-flash"), BHARAT)
    extractor, model = claim_extractor._default_extractor()
    assert isinstance(extractor, FallbackExtractor) and model == "gemini-3.5-flash"
    assert isinstance(extractor.secondary, BharatCodeExtractor)


def test_only_bharatcode_configured_uses_it_alone(monkeypatch):
    wire(monkeypatch, Settings(), BHARAT)
    extractor, model = claim_extractor._default_extractor()
    assert isinstance(extractor, BharatCodeExtractor) and model == "qwen-3.8-27b"


def test_only_gemini_configured_has_no_fallback(monkeypatch):
    pytest.importorskip("google.genai")
    wire(monkeypatch, Settings("gk", "gemini-3.5-flash"), None)
    extractor, _ = claim_extractor._default_extractor()
    assert not isinstance(extractor, (FallbackExtractor, BharatCodeExtractor))


def test_nothing_configured_is_an_error(monkeypatch):
    wire(monkeypatch, Settings(), None)
    with pytest.raises(ExtractionError, match="No model provider"):
        claim_extractor._default_extractor()


def test_bharatcode_config_reads_the_environment(monkeypatch):
    monkeypatch.setenv("BHARATCODE_API_KEY", "env-key")
    monkeypatch.setenv("BHARATCODE_MODEL", "some-model")
    monkeypatch.delenv("BHARATCODE_BASE_URL", raising=False)
    config = claim_extractor._bharatcode_config()
    assert config["api_key"] == "env-key" and config["model"] == "some-model"
    assert config["base_url"].endswith("/api/model/v1")

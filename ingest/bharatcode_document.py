"""Daksh: BharatCode (OpenAI-compatible chat/completions) adapter, the extraction fallback.

BharatCode models take text and images but not PDFs, so a PDF part is rendered locally into
slide text plus page images. The model name is owned by this adapter; the `model` argument
(a Gemini name) is ignored. Errors use the same classes as the Gemini adapter, so the caller
cannot tell the providers apart: overload and timeouts are ProviderUnavailableError, key or
plan problems are ExtractionError with a message that never contains the key.
"""
import asyncio
import base64
import json
from collections.abc import Sequence

import httpx
from pydantic import BaseModel

from ingest.claim_extractor import ExtractionError, ProviderUnavailableError

DEFAULT_BASE_URL = "https://bharatcode.ai/api/model/v1"
DEFAULT_MODEL = "qwen-3.8-27b"
DEFAULT_TIMEOUT_S = 120
MAX_PAGES = 30
IMAGE_DPI = 110
ACCESS_CODES = {401, 403}  # bad key, or the model is not in the account's plan


def render_pdf(pdf: bytes) -> tuple[str, list[bytes]]:
    """Slide text (with slide markers) and one PNG per page, first MAX_PAGES pages only."""
    import pymupdf

    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        texts, images = [], []
        for index in range(min(doc.page_count, MAX_PAGES)):
            page = doc[index]
            texts.append(f"--- Slide {index + 1} text ---\n{page.get_text().strip()}")
            images.append(page.get_pixmap(dpi=IMAGE_DPI).tobytes("png"))
        return "\n".join(texts), images
    finally:
        doc.close()


def _image_part(png: bytes) -> dict:
    return {"type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}


class BharatCodeExtractor:
    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL,
                 timeout_s: float = DEFAULT_TIMEOUT_S, transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._timeout_s = timeout_s
        self._transport = transport

    async def _content(self, parts: Sequence[tuple[str, bytes]], prompt: str,
                       schema: type[BaseModel]) -> list[dict]:
        instruction = (f"{prompt}\n\nReturn ONLY one JSON object (no markdown) matching this JSON "
                       f"schema:\n{json.dumps(schema.model_json_schema())}")
        images: list[dict] = []
        for mime, data in parts:
            if mime == "application/pdf":
                text, pngs = await asyncio.to_thread(render_pdf, data)
                instruction += f"\n\nSLIDE TEXT:\n{text}"
                images += [_image_part(png) for png in pngs]
            elif mime.startswith("image/"):
                images.append(_image_part(data))
        return [{"type": "text", "text": instruction}, *images]

    async def _post(self, client: httpx.AsyncClient, body: dict) -> httpx.Response:
        return await client.post(self._url, json=body, headers={"Authorization": f"Bearer {self._api_key}"})

    async def extract(self, parts: Sequence[tuple[str, bytes]], prompt: str,
                      schema: type[BaseModel], model: str) -> str:
        body = {"model": self.model, "temperature": 0,
                "messages": [{"role": "user", "content": await self._content(parts, prompt, schema)}],
                "response_format": {"type": "json_object"}}
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
                response = await asyncio.wait_for(self._post(client, body), timeout=self._timeout_s * 2)
                if response.status_code == 400:  # some deployments reject response_format
                    body.pop("response_format")
                    response = await asyncio.wait_for(self._post(client, body), timeout=self._timeout_s * 2)
        except (TimeoutError, asyncio.TimeoutError, httpx.TimeoutException):
            raise ProviderUnavailableError("BharatCode did not answer in time; try again shortly.") from None
        except httpx.TransportError:
            raise ProviderUnavailableError("Could not reach BharatCode (network error).") from None

        code = response.status_code
        if code in ACCESS_CODES:
            raise ExtractionError(f"BharatCode rejected the request ({code}); check BHARATCODE_API_KEY "
                                  "and that your plan includes the model.")
        if code == 429 or code >= 500:
            raise ProviderUnavailableError(f"BharatCode is unavailable or busy ({code}); try again shortly.")
        if code != 200:
            raise RuntimeError(f"BharatCode returned HTTP {code}")
        try:
            text = response.json()["choices"][0]["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, AttributeError):
            raise RuntimeError("BharatCode returned an unreadable response") from None
        if not text.strip():
            raise RuntimeError("BharatCode returned no text")
        return text

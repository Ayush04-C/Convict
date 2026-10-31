"""Daksh: thin Gemini adapter for document extraction (PDF or page images in, JSON text out).

Kept separate from app/llm.py (Ayush's shared text client) because extraction sends
binary parts. Every call is time-bounded: a provider outage must fail the run quickly,
never freeze it. Errors are classified so the caller can tell "try page images" (the
provider rejected our input) from "give up now" (overload, rate limit, bad key or model).
"""
import asyncio
from collections.abc import Sequence

from pydantic import BaseModel

from ingest.claim_extractor import ExtractionError, ProviderUnavailableError

DEFAULT_TIMEOUT_S = 90
DEFAULT_ATTEMPTS = 2
CONFIG_CODES = {401, 403, 404}  # bad key, no access, unknown model: not fixable by retrying


class GeminiDocumentExtractor:
    _hard_cap_s: float = DEFAULT_TIMEOUT_S * 2 + 15

    def __init__(self, api_key: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 attempts: int = DEFAULT_ATTEMPTS):
        from google import genai
        from google.genai import types

        self._hard_cap_s = timeout_s * 2 + 15
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=int(timeout_s * 1000), retry_options=types.HttpRetryOptions(attempts=attempts)))

    async def extract(self, parts: Sequence[tuple[str, bytes]], prompt: str,
                      schema: type[BaseModel], model: str) -> str:
        from google.genai import errors, types

        contents = [types.Part.from_bytes(data=data, mime_type=mime) for mime, data in parts]
        contents.append(prompt)
        config = types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema, temperature=0)
        try:
            # Hard cap above the SDK's own timeout and retries, so nothing can hang forever.
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(model=model, contents=contents, config=config),
                timeout=self._hard_cap_s)
        except (TimeoutError, asyncio.TimeoutError):
            raise ProviderUnavailableError("Gemini did not answer in time; try again shortly.") from None
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            if code in CONFIG_CODES:
                raise ExtractionError(
                    f"Gemini rejected the request ({code}); check GEMINI_API_KEY and MODEL_MID.") from None
            if code == 429 or (isinstance(code, int) and code >= 500):
                raise ProviderUnavailableError(
                    f"Gemini is unavailable or busy ({code}); try again shortly.") from None
            raise  # other 4xx (for example 400): the caller may retry with page images
        if not response.text:
            raise RuntimeError("Gemini returned no text")
        return response.text

"""Shared structured Gemini adapter. Agent prompts and ADK configuration stay with Daksh."""

import asyncio
from typing import Any

from google import genai
from google.genai import errors, types
import httpx
from pydantic import BaseModel, ValidationError

from app.settings import Settings


class LlmError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None):
        self.kind, self.status = kind, status
        super().__init__(message)


class GeminiClient:
    def __init__(self, settings: Settings, *, client: Any = None):
        settings.require_live_credentials("gemini")
        self.timeout = settings.gemini_timeout
        self.models = frozenset(
            m for m in (settings.model_fast, settings.model_mid, settings.model_pro) if m.strip()
        )
        self._owns_client = client is None
        self._client = (
            client
            or genai.Client(
                api_key=settings.gemini_api_key.get_secret_value(),
                http_options=types.HttpOptions(
                    timeout=int(self.timeout * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            ).aio
        )
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def aclose(self):
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def generate(self, prompt: str, schema: type, model: str) -> Any:
        if self._closed:
            raise LlmError("closed", "Gemini client is closed.")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A nonempty prompt is required.")
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise ValueError("A Pydantic output model is required.")
        if model not in self.models:
            raise LlmError(
                "config", "Model must match a configured MODEL_FAST, MODEL_MID or MODEL_PRO value."
            )
        contents = prompt
        try:
            async with asyncio.timeout(self.timeout):
                for attempt in range(2):
                    try:
                        response = await self._client.models.generate_content(
                            model=model,
                            contents=contents,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_schema=schema,
                                temperature=0,
                            ),
                        )
                        if response.parsed is not None:
                            return schema.model_validate(response.parsed)
                        if not response.text:
                            raise LlmError("blocked", "Gemini returned no structured content.")
                        return schema.model_validate_json(response.text)
                    except ValidationError as error:
                        if attempt:
                            raise LlmError(
                                "validation", "Gemini output failed schema validation twice."
                            ) from None
                        codes = ", ".join(
                            sorted(
                                {
                                    e["type"]
                                    for e in error.errors(include_input=False, include_url=False)
                                }
                            )
                        )
                        contents = (
                            prompt
                            + "\nThe previous output failed validation ("
                            + codes
                            + "). Return only JSON matching the supplied schema."
                        )
        except (TimeoutError, httpx.TimeoutException):
            raise LlmError("timeout", "Gemini operation exceeded its deadline.") from None
        except httpx.HTTPError:
            raise LlmError("network", "Gemini transport failed.") from None
        except errors.APIError as error:
            code = error.code
            kind = {
                400: "request",
                401: "auth",
                403: "permission",
                404: "model",
                429: "rate_limit",
            }.get(code, "server")
            raise LlmError(kind, "Gemini request failed (" + kind + ").", code) from None
        except ValueError:
            raise LlmError("config", "Gemini SDK rejected the request or output schema.") from None

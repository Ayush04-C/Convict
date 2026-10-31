"""Daksh: thin Gemini adapter for document extraction (PDF or page images in, JSON text out).

Kept separate from app/llm.py (Ayush's shared text client) because extraction sends
binary parts. Call signatures were checked against google-genai 2.34 offline; the live
call itself is verified at build time with the event key.
"""
from collections.abc import Sequence

from pydantic import BaseModel


class GeminiDocumentExtractor:
    def __init__(self, api_key: str):
        from google import genai

        self._client = genai.Client(api_key=api_key)

    async def extract(self, parts: Sequence[tuple[str, bytes]], prompt: str,
                      schema: type[BaseModel], model: str) -> str:
        from google.genai import types

        contents = [types.Part.from_bytes(data=data, mime_type=mime) for mime, data in parts]
        contents.append(prompt)
        response = await self._client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=schema, temperature=0),
        )
        if not response.text:
            raise RuntimeError("Gemini returned no text")
        return response.text

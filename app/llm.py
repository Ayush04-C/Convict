"""Ayush: shared live Gemini adapter; Daksh owns ADK specialist configuration."""
from typing import Any


class GeminiClient:
    async def generate(self, prompt: str, schema: type, model: str) -> Any:
        raise NotImplementedError("Stage 4: structured Gemini call and bounded validation retry")

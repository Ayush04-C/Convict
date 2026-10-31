"""Small live structured-output probe; requires --live and configured models."""

import argparse
import asyncio
import json

from pydantic import BaseModel

from app.llm import GeminiClient, LlmError
from app.settings import ProviderConfigurationError, Settings


class Probe(BaseModel):
    ok: bool


async def check(settings: Settings):
    settings.require_live_credentials("gemini")
    model = settings.model_fast or settings.model_mid
    if not model:
        raise ProviderConfigurationError(
            "Set MODEL_FAST or MODEL_MID before the live Gemini check."
        )
    async with GeminiClient(settings) as client:
        result = await client.generate("Return JSON with the field ok set to true.", Probe, model)
        if result.ok is not True:
            raise LlmError("validation", "Gemini probe returned an unexpected value.")
        return {
            "mode": "LIVE GEMINI",
            "model": model,
            "structured_output_valid": True,
            "requests": client.request_count,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        print("No requests made. Use --live for one small structured Gemini probe.")
        return
    try:
        result = asyncio.run(check(Settings()))
    except (LlmError, ProviderConfigurationError) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

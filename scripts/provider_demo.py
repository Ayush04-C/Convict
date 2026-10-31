"""Offline provider/cache/dossier/ledger/structured-LLM smoke; no live requests."""

import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from pydantic import BaseModel

from app.llm import GeminiClient
from app.schemas import Policy
from app.settings import Settings
from crustdata.client import CrustdataClient
from crustdata.dossier import build_dossier, dossier_evidence
from ledger.db import initialize
from ledger.repo import LedgerRepository


class Probe(BaseModel):
    ok: bool


async def run_demo(path: Path):
    await initialize(path)
    settings = Settings(
        _env_file=None,
        crustdata_api_key="offline-test-key",
        gemini_api_key="offline-test-key",
        model_fast="offline-fast",
        mode="stub",
    )
    payload = [
        {
            "matched_on": 123,
            "matches": [
                {
                    "confidence_score": 1,
                    "company_data": {
                        "crustdata_company_id": 123,
                        "headcount": {"total": 10},
                        "funding": None,
                    },
                }
            ],
        }
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        async with CrustdataClient(settings, http=http, cache_path=path) as provider:
            first = await build_dossier(123, provider)
            second = await build_dossier(123, provider)
            assert first == second and provider.request_count == 1
    repo = LedgerRepository(path)
    run = await repo.create_run(
        "offline-demo", "Synthetic", None, Policy(max_check_usd=0), "stub.pdf"
    )
    await repo.add_evidence(run, (dossier_evidence(run, first),))
    sdk = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=AsyncMock(return_value=SimpleNamespace(parsed={"ok": True}, text=None))
        )
    )
    async with GeminiClient(settings, client=sdk) as llm:
        result = await llm.generate("Synthetic offline probe", Probe, "offline-fast")
    return {
        "mode": "OFFLINE SYNTHETIC PROVIDER CHECK",
        "mock_http_requests": 1,
        "repeat_from_cache": True,
        "ledger_evidence_rows": len(await repo.get_evidence(run)),
        "mock_gemini_validated": result.ok,
        "real_api_calls": 0,
    }


def main():
    with tempfile.TemporaryDirectory(prefix="leo-provider-") as directory:
        result = asyncio.run(run_demo(Path(directory) / "provider.sqlite"))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

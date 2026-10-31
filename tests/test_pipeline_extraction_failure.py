"""A deck whose extraction finds no claims must fail the run, never produce a verdict about nothing."""
import json

import pytest

from app.schemas import Policy
from app.settings import Settings
from ledger.errors import LedgerNotFound
from ingest.claim_extractor import extract_claims
from leo.orchestrator import run_pipeline
from tests.pipeline_support import dependencies, pdf_bytes


class EmptyExtractor:
    """A model that returns valid JSON with no claims, as a throttled model or a fallback can."""

    def __init__(self):
        self.calls = 0

    async def extract(self, parts, prompt, schema, model):
        self.calls += 1
        return '{"company_name": "Synthetic", "claims": []}'


async def test_empty_extraction_fails_the_run_instead_of_scoring_it(ledger, tmp_path):
    path = tmp_path / "deck.pdf"
    path.write_bytes(pdf_bytes())
    run = await ledger.create_run("t", "Synthetic", "example.com", Policy(max_check_usd=100000), str(path))
    extractor, deps = EmptyExtractor(), dependencies()

    async def extract(deck):
        return await extract_claims(deck, extractor=extractor, model="m")

    deps.extract = extract
    config = Settings(_env_file=None, mode="live", pipeline_timeout=20, phase_timeout=5)
    await run_pipeline(run, repo=ledger, settings=config, dependencies=deps)

    info = await ledger.get_run(run)
    assert info["status"] == "failed" and info["phase"] == "FAILED"
    assert extractor.calls == 2                                   # one retry, then give up
    assert (await ledger.get_agent_statuses(run)) == ()           # no specialist ever ran
    events = await ledger.get_events(run)
    assert json.loads(events[-1].payload_json)["status"] == "failed"      # the terminal event says so
    assert "gates_confirmed" not in [e.kind for e in events]
    with pytest.raises(LedgerNotFound):
        await ledger.get_decision(run)                                    # and there is no verdict

"""Failure context and explicit retries preserve the failed run and saved PDF."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.schemas import Policy
from ingest.claim_extractor import ExtractionError, ProviderUnavailableError
from tests.pipeline_support import dependencies
from tests.test_run_api import api, terminal, upload


@pytest.mark.parametrize("kind", [ExtractionError, ProviderUnavailableError])
async def test_extraction_reason_is_retained_and_failed_review_can_be_retried(
    auth_settings, tmp_path, kind
):
    deps = dependencies()
    original_extract = deps.extract
    secret = "test-private-provider-key"
    deps.extract = AsyncMock(side_effect=kind("Provider unavailable: " + secret))
    async with api(auth_settings, tmp_path, deps=deps, gemini_api_key=SecretStr(secret)) as (
        app,
        client,
    ):
        run = (await upload(client)).json()["run_id"]
        assert (await terminal(client, run))["status"] == "failed"
        before = await app.state.ledger.get_events(run)
        report = (await client.get(f"/runs/{run}/report")).json()
        assert report["failure_code"] == "EXTRACTION_FAILED"
        assert "Provider unavailable: " not in report["failure_details"]["message"]
        assert report["failure_details"]["category"] == (
            "provider_unavailable" if kind is ProviderUnavailableError else "extraction_error"
        )
        assert secret not in json.dumps(report)
        assert secret not in "".join(e.payload_json for e in before)
        assert "Extraction detail" in (await client.get(f"/runs/{run}/report?format=markdown")).text
        deps.extract = original_extract
        result = await client.post(f"/runs/{run}/retry")
        assert result.status_code == 202
        retry = result.json()["run_id"]
        assert retry != run and (await terminal(client, retry))["status"] == "done"
        old, new = await app.state.ledger.get_run(run), await app.state.ledger.get_run(retry)
        assert old["deck_path"] != new["deck_path"]
        assert Path(old["deck_path"]).read_bytes() == Path(new["deck_path"]).read_bytes()
        assert old["policy"] == new["policy"] and old["domain"] == new["domain"]
        assert await app.state.ledger.get_events(run) == before
        assert (await client.post(f"/runs/{retry}/retry")).status_code == 409


async def test_retry_requires_authentication_ownership_and_a_retained_pdf(auth_settings, tmp_path):
    async with api(auth_settings, tmp_path) as (app, client):
        run = await app.state.ledger.create_run(
            "someone-else", "Private", None, Policy(max_check_usd=0), "private.pdf"
        )
        await app.state.ledger.fail_run(run, "EXTRACTION_FAILED")
        assert (await client.post(f"/runs/{run}/retry")).status_code == 404
        client.headers.pop("Authorization")
        assert (await client.post(f"/runs/{run}/retry")).status_code == 401
    async with api(auth_settings, tmp_path / "second") as (app, client):
        run = await app.state.ledger.create_run(
            auth_settings.demo_user, "Synthetic", None, Policy(max_check_usd=0), "missing.pdf"
        )
        await app.state.ledger.fail_run(run, "EXTRACTION_FAILED")
        assert (await client.post(f"/runs/{run}/retry")).status_code == 410


@pytest.mark.parametrize(
    "error,category,status",
    [
        (ExtractionError("Gemini rejected the request (403); private-key"), "configuration", 403),
        (
            ProviderUnavailableError("Gemini is unavailable or busy (429); private-key"),
            "rate_limit",
            429,
        ),
        (ProviderUnavailableError("Gemini did not answer in time; private-key"), "timeout", None),
        (
            ExtractionError("The model returned invalid claims twice (private-key)"),
            "invalid_response",
            None,
        ),
        (ExtractionError("private-key"), "extraction_error", None),
    ],
)
def test_only_allowlisted_diagnostics_are_persisted(error, category, status):
    from app.failures import extraction_failure

    details = extraction_failure(error)
    assert details["category"] == category and details["provider_status"] == status
    assert "private-key" not in json.dumps(details)

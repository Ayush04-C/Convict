"""Real SQLite and provider adapter handoff; synthetic HTTP only."""

import json

import httpx
import pytest

from app.schemas import Resolution
from app.settings import Settings
from crustdata.client import CrustdataClient
from crustdata.dossier import build_dossier, dossier_evidence
from ledger.errors import LedgerConflict, LedgerNotFound, LedgerStateError
from leo.context import build_context_packets
from leo.entity_resolution import resolve_company
from tests.ledger_support import advance
from tests.test_entity_resolution import match, response


async def test_resolution_dossier_context_survive_ledger_round_trip(ledger, run):
    await advance(ledger, run, "RESOLVE")
    claims = await ledger.get_claims(run)
    settings = Settings(_env_file=None, crustdata_api_key="synthetic")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=response(match())))
    ) as http:
        async with CrustdataClient(settings, http=http, cache_path=ledger.path) as provider:
            resolution = await resolve_company("Synthetic", "example.com", claims, provider)
            await ledger.save_resolution(run, resolution)
            dossier = await build_dossier(resolution.company_id, provider)
    packets = build_context_packets(claims, dossier, (await ledger.get_run(run))["policy"])
    await ledger.add_evidence(run, (dossier_evidence(run, dossier),))
    await ledger.save_context_packets(run, packets)
    for packet in packets:
        assert await ledger.get_context_packet(run, packet.agent) == packet
    assert (await ledger.get_run(run))["crustdata_company_id"] == 1
    assert len(await ledger.get_evidence(run)) == 1
    saved = [e for e in await ledger.get_events(run) if e.kind == "resolution_saved"]
    assert json.loads(saved[0].payload_json)["company_id"] == 1
    with pytest.raises(LedgerConflict):
        await ledger.save_context_packets(run, packets)
    with pytest.raises(LedgerConflict):
        await ledger.save_resolution(run, resolution)


async def test_unresolved_identity_is_terminal_without_packets(ledger, run):
    await advance(ledger, run, "RESOLVE")
    await ledger.save_resolution(run, Resolution(status="cannot_assess", reason="Ambiguous"))
    record = await ledger.get_run(run)
    assert record["status"] == "cannot_assess" and record["crustdata_company_id"] is None
    with pytest.raises(LedgerStateError):
        await ledger.set_phase(run, "ROUND1", "round1")
    with pytest.raises(LedgerNotFound):
        await ledger.get_context_packet(run, "identity")
    assert (await ledger.get_events(run))[-1].kind == "done"


async def test_resolution_write_and_terminal_event_are_atomic(ledger, run, monkeypatch):
    await advance(ledger, run, "RESOLVE")
    before = await ledger.get_events(run)

    async def fail(*args):
        raise RuntimeError("Synthetic storage failure")

    monkeypatch.setattr("ledger.repo.event", fail)
    with pytest.raises(RuntimeError):
        await ledger.save_resolution(
            run, Resolution(status="resolved", company_id=1, reason="Test")
        )
    assert (await ledger.get_run(run))["crustdata_company_id"] is None
    assert await ledger.get_events(run) == before


async def test_resolution_cannot_be_saved_outside_resolve(ledger, run):
    with pytest.raises(LedgerStateError):
        await ledger.save_resolution(
            run, Resolution(status="resolved", company_id=1, reason="Test")
        )

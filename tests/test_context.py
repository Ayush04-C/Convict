"""Deterministic context tests with synthetic company data."""

import json

import pytest
from pydantic import ValidationError

from app.schemas import Claim, ClaimSet, Dossier, Policy
from leo.context import AGENTS, DOSSIER_GROUPS, PRIMARY, SECONDARY, build_context_packets


def dossier(**updates):
    values = dict(
        company_id=1,
        data_json=json.dumps(
            {g: {"synthetic": g} for groups in DOSSIER_GROUPS.values() for g in groups}
        ),
        fetched_at="2026-10-31T00:00:00Z",
    )
    values.update(updates)
    return Dossier(**values)


@pytest.mark.parametrize("topic", PRIMARY)
def test_every_claim_routes_to_primary_and_only_defined_secondary(topic):
    claim = Claim(id="C1", topic=topic, text="Synthetic", value=10, slide=2)
    packets = build_context_packets(ClaimSet(claims=(claim,)), dossier(), Policy(max_check_usd=100))
    for packet in packets:
        expected = packet.agent == PRIMARY[topic] or packet.agent in SECONDARY.get(topic, ())
        assert packet.claims == ((claim,) if expected else ())


def test_product_background_is_preserved_without_testable_credit():
    claim = Claim(id="C1", topic="product_other", text="Synthetic product", slide=1)
    for packet in build_context_packets(ClaimSet(claims=(claim,)), {}, Policy(max_check_usd=0)):
        assert packet.claims[0].text == claim.text and not packet.claims[0].testable
    assert claim.testable  # The original claim is not mutated.


def test_slices_have_only_allowed_groups_and_independent_values():
    packets = build_context_packets(ClaimSet(), dossier(), Policy(max_check_usd=12))
    assert tuple(p.agent for p in packets) == AGENTS
    for packet in packets:
        assert set(json.loads(packet.dossier_json)) == set(DOSSIER_GROUPS[packet.agent])
    data = json.loads(packets[0].dossier_json)
    data["basic_info"]["synthetic"] = "changed"
    assert json.loads(packets[1].dossier_json)["basic_info"]["synthetic"] == "basic_info"
    with pytest.raises(ValidationError):
        packets[0].dossier_json = "{}"


def test_null_missing_groups_and_policy_survive():
    policy = Policy(max_check_usd=20, stage="seed")
    packets = build_context_packets(ClaimSet(), dossier(data_json='{"funding":null}'), policy)
    assert json.loads(packets[2].dossier_json) == {"funding": None}
    assert json.loads(packets[0].dossier_json) == {}
    assert all(p.policy == policy for p in packets)


@pytest.mark.parametrize(
    "claim_stub,dossier_stub", [(False, True), (True, False), (True, True), (False, False)]
)
def test_stub_provenance_is_preserved(claim_stub, dossier_stub):
    packets = build_context_packets(
        ClaimSet(is_stub=claim_stub), dossier(is_stub=dossier_stub), Policy(max_check_usd=0)
    )
    assert all(p.is_stub == (claim_stub or dossier_stub) for p in packets)


@pytest.mark.parametrize("bad", ["[]", '{"funding":NaN}', "bad", "null"])
def test_invalid_dossier_is_rejected(bad):
    with pytest.raises(ValueError):
        build_context_packets(ClaimSet(), dossier(data_json=bad), Policy(max_check_usd=0))


@pytest.mark.parametrize("slices", [{"other": "{}"}, {"identity": '{"funding":{}}'}])
def test_supplied_slices_cannot_bypass_group_scope(slices):
    with pytest.raises(ValueError):
        build_context_packets(ClaimSet(), slices, Policy(max_check_usd=0))


def test_claim_order_preserved_and_duplicate_ids_rejected():
    a = Claim(id="Z", topic="team_size", text="First", slide=1)
    b = Claim(id="A", topic="team_size", text="Second", slide=2)
    packets = build_context_packets(ClaimSet(claims=(a, b)), {}, Policy(max_check_usd=0))
    assert packets[1].claims == (a, b)
    with pytest.raises(ValueError):
        build_context_packets(ClaimSet(claims=(a, a)), {}, Policy(max_check_usd=0))

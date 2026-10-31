"""Identity gate regression tests; all provider responses are synthetic."""

import asyncio
import json
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas import Claim, ClaimSet, Resolution
from crustdata.client import CrustdataError
from leo.entity_resolution import resolve_company


def match(ident=1, score=0.9, domain="example.com", year=2020, country="IND", employees="11-50"):
    return {
        "confidence_score": score,
        "company_data": {
            "basic_info": {
                "crustdata_company_id": ident,
                "name": "Synthetic",
                "primary_domain": domain,
                "year_founded": year,
                "employee_count_range": employees,
            },
            "locations": {"country": country},
        },
    }


def response(*matches):
    return [{"matched_on": "Synthetic", "matches": list(matches)}]


def client(*responses, urls=()):
    return SimpleNamespace(
        identify=AsyncMock(side_effect=responses),
        web_search=AsyncMock(return_value={"results": [{"url": url} for url in urls]}),
    )


def claims(*pairs):
    return ClaimSet(
        claims=tuple(
            Claim(id=f"C{i}", topic=t, value=v, text=str(v), slide=1)
            for i, (t, v) in enumerate(pairs)
        )
    )


@pytest.mark.parametrize("score", [0.9, 1])
async def test_clear_high_confidence_and_domain_precedence(score):
    c = client(response(match(score=score)))
    result = await resolve_company("Synthetic", "https://www.example.com", claims(), c)
    assert result.company_id == 1 and result.status == "resolved"
    c.identify.assert_awaited_once_with(domains=["example.com"])
    c.web_search.assert_not_awaited()


@pytest.mark.parametrize("score", [0.5, 0.6, 0.8999, 1])
async def test_ties_require_two_independent_attributes(score):
    c = client(response(match(score=score), match(2, score, "other.com", 2010, "USA")))
    deck = claims(("identity_founding", 2020), ("identity_hq", "India"))
    result = await resolve_company("Synthetic", None, deck, c)
    assert result.company_id == 1
    assert set(json.loads(result.candidates_json)[0]["matching_attributes"]) == {
        "founding_year",
        "headquarters",
    }


@pytest.mark.parametrize("score", [0.5, 0.6, 1])
async def test_one_attribute_lead_or_equal_matches_is_unresolved(score):
    c = client(response(match(score=score), match(2, score, "other.com", 2010)))
    result = await resolve_company("Synthetic", None, claims(("identity_founding", 2020)), c)
    assert result.status == "cannot_assess" and result.company_id is None
    c.web_search.assert_not_awaited()


async def test_duplicate_and_conflicting_claims_do_not_add_votes():
    deck = claims(
        ("identity_founding", 2020),
        ("identity_founding", 2020),
        ("identity_hq", "India"),
        ("identity_hq", "USA"),
    )
    c = client(response(match(score=0.6), match(2, 0.6, "other.com", 2010)))
    assert (await resolve_company("Synthetic", None, deck, c)).status == "cannot_assess"


async def test_employee_range_can_supply_second_attribute():
    c = client(response(match(score=0.6), match(2, 0.6, "other.com", 2010, employees="51-200")))
    deck = claims(("identity_founding", 2020), ("team_size", 25))
    assert (await resolve_company("Synthetic", None, deck, c)).company_id == 1


async def test_known_wrong_domain_is_never_selected():
    c = client(response(match(domain="wrong.com", score=1)))
    assert (
        await resolve_company("Synthetic", "example.com", claims(), c)
    ).status == "cannot_assess"


async def test_known_conflicting_founding_year_blocks_automatic_acceptance():
    c = client(response(match(score=1, year=2010)))
    assert (
        await resolve_company("Synthetic", None, claims(("identity_founding", 2020)), c)
    ).status == "cannot_assess"


async def test_a_supplied_domain_that_resolves_to_one_company_outranks_a_conflicting_deck_year():
    c = client(response(match(score=1, year=2017)))
    result = await resolve_company(
        "Synthetic", "example.com", claims(("identity_founding", 2012)), c
    )
    assert (result.status, result.company_id) == ("resolved", 1)
    c.identify.assert_awaited_once_with(domains=["example.com"])


async def test_a_supplied_domain_does_not_rescue_a_low_confidence_match():
    c = client(response(match(score=0.6, year=2017)))
    result = await resolve_company(
        "Synthetic", "example.com", claims(("identity_founding", 2012)), c
    )
    assert result.status == "cannot_assess"


async def test_a_supplied_domain_does_not_pick_between_two_equal_candidates():
    c = client(response(match(ident=1, score=1, year=2017), match(ident=2, score=1, year=2018)))
    result = await resolve_company(
        "Synthetic", "example.com", claims(("identity_founding", 2012)), c
    )
    assert result.status == "cannot_assess"


async def test_a_name_only_lookup_still_refuses_a_conflicting_year():
    c = client(response(match(score=1, year=2017)))
    result = await resolve_company("Synthetic", None, claims(("identity_founding", 2012)), c)
    assert result.status == "cannot_assess"


async def test_duplicate_ids_and_order_do_not_change_resolution():
    a, b = match(score=0.6), match(2, 0.6, "other.com", 2010, "USA")
    deck = claims(("identity_founding", 2020), ("identity_hq", "India"))
    first = await resolve_company("Synthetic", None, deck, client(response(a, b, a)))
    second = await resolve_company("Synthetic", None, deck, client(response(b, a)))
    assert first == second


@pytest.mark.parametrize("score", [0, 0.4999])
async def test_web_fallback_reidentifies_at_most_three_domains(score):
    c = client(
        response(match(score=score)),
        response(match()),
        urls=(
            "javascript:bad",
            "https://user:pass@bad.com",
            "https://example.com/about",
            "https://example.com/other",
            "https://second.com",
            "https://third.com",
            "https://fourth.com",
        ),
    )
    result = await resolve_company("Synthetic", None, claims(), c)
    assert result.company_id == 1
    assert c.identify.await_args_list[1].kwargs == {
        "domains": ["example.com", "second.com", "third.com"]
    }
    c.web_search.assert_awaited_once_with(query="Synthetic company", sources=("web",))


@pytest.mark.parametrize(
    "fallback",
    [
        response(),
        response(match(score=0.89)),
        response(match(domain="unrelated.com")),
        response(match(score=1), match(2, 1, "second.com")),
    ],
)
async def test_web_never_confirms_low_confidence_mismatched_or_tied_identity(fallback):
    c = client(response(), fallback, urls=("https://example.com", "https://second.com"))
    assert (await resolve_company("Synthetic", None, claims(), c)).status == "cannot_assess"


async def test_empty_search_stops_without_more_identify_calls():
    c = client(response())
    assert (await resolve_company("Synthetic", None, claims(), c)).status == "cannot_assess"
    assert c.identify.await_count == 1


async def test_web_does_not_override_explicit_domain():
    c = client(response(), urls=("https://different.com",))
    assert (
        await resolve_company("Synthetic", "example.com", claims(), c)
    ).status == "cannot_assess"
    assert c.identify.await_count == 1


@pytest.mark.parametrize(
    "bad", [None, {}, [{"matches": [None]}], response(match(score=float("nan")))]
)
async def test_malformed_candidates_are_operational_errors(bad):
    with pytest.raises(CrustdataError):
        await resolve_company("Synthetic", None, claims(), client(bad))


async def test_candidates_without_valid_ids_are_not_selected():
    c = client(response(match(ident=None)))
    assert (await resolve_company("Synthetic", None, claims(), c)).status == "cannot_assess"


@pytest.mark.parametrize(
    "name,domain,timeout",
    [("", None, 60), ("a", "bad", 60), ("a", None, 0), ("a", None, float("inf"))],
)
async def test_bad_inputs_make_no_requests(name, domain, timeout):
    c = client()
    with pytest.raises(ValueError):
        await resolve_company(name, domain, claims(), c, timeout=timeout)
    c.identify.assert_not_awaited()


async def test_provider_errors_propagate_and_deadline_is_typed():
    c = client()
    error = CrustdataError("auth", 401, "safe")
    c.identify.side_effect = error
    with pytest.raises(CrustdataError) as caught:
        await resolve_company("Synthetic", None, claims(), c)
    assert caught.value is error
    c.identify.side_effect = lambda **kwargs: None

    async def slow(**kwargs):
        await asyncio.sleep(10)

    c.identify.side_effect = slow
    with pytest.raises(CrustdataError) as caught:
        await resolve_company("Synthetic", None, claims(), c, timeout=0.01)
    assert caught.value.kind == "timeout"
    task = asyncio.create_task(resolve_company("Synthetic", None, claims(), c))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "status,ident", [("resolved", None), ("resolved", 0), ("cannot_assess", 1)]
)
def test_resolution_identity_invariant(status, ident):
    with pytest.raises(ValidationError):
        Resolution(status=status, company_id=ident, reason="test")


@pytest.mark.parametrize("ident", [True, "1", 1.5, -1])
def test_resolution_requires_a_strict_positive_integer_id(ident):
    with pytest.raises(ValidationError):
        Resolution(status="resolved", company_id=ident, reason="test")


async def test_web_discards_malformed_urls_and_unrelated_company_names():
    wrong = match()
    wrong["company_data"]["basic_info"]["name"] = "Unrelated business"
    c = client(response(), response(wrong), urls=("https://[broken", "https://example.com"))
    assert (await resolve_company("Synthetic", None, claims(), c)).status == "cannot_assess"


@pytest.mark.parametrize(
    "hq,country",
    [
        ("USA", "USA"),
        ("United States", "USA"),
        ("San Francisco, US", "USA"),
        ("UK", "GBR"),
        ("India", "IND"),
    ],
)
async def test_hq_country_aliases_are_normalized_on_both_sides(hq, country):
    deck = claims(("identity_founding", 2020), ("identity_hq", hq))
    c = client(response(match(score=0.6, country=country), match(2, 0.6, "other.com", 2010, "AUS")))
    assert (await resolve_company("Synthetic", None, deck, c)).company_id == 1

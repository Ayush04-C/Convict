"""Synthetic provider data and real specialist composition for integration tests."""

from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pymupdf

from agents.registry import get_agents
from app.schemas import AdjudicationResult, Claim, ClaimSet
from crustdata.cache import ProviderResponse
from leo.runtime import PipelineDependencies

COMPANY = {
    "basic_info": {
        "crustdata_company_id": 123,
        "name": "Synthetic",
        "primary_domain": "example.com",
        "all_domains": ["example.com"],
        "status": "active",
        "year_founded": 2020,
    },
    "locations": {"country": "IND", "city": "Bengaluru"},
    "headcount": {"total": 12},
    "revenue": {"estimated": {"lower_bound_usd": 20_000_000, "upper_bound_usd": 50_000_000}},
    "funding": {"last_round_type": "series_c", "milestones": [{"round_type": "series_c"}]},
}
CLAIMS = ClaimSet(
    company_name="Synthetic",
    claims=(
        Claim(
            id="C1",
            topic="revenue_arr",
            text="200 million USD ARR",
            value=200_000_000,
            unit="USD",
            slide=1,
        ),
        Claim(
            id="C2",
            topic="identity_founding",
            text="Founded 2020",
            value=2020,
            unit="year",
            slide=1,
        ),
        Claim(id="C3", topic="team_size", text="12 people", value=12, unit="count", slide=1),
    ),
)


def pdf_bytes():
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 40), "Synthetic company. ARR claim 200 million USD.")
        return doc.tobytes()


class EmptyDebate:
    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, schema, model):
        self.calls += 1
        return schema.model_validate({})


def dependencies(data=None, claims=CLAIMS):
    data = deepcopy(COMPANY if data is None else data)
    envelope = [
        {"matched_on": "example.com", "matches": [{"confidence_score": 1, "company_data": data}]}
    ]
    provider = SimpleNamespace(
        identify=AsyncMock(return_value=envelope),
        enrich_result=AsyncMock(
            return_value=ProviderResponse(json.dumps(envelope), "2026-10-31T00:00:00Z")
        ),
        web_search=AsyncMock(return_value={"results": []}),
    )
    return PipelineDependencies(
        provider,
        EmptyDebate(),
        extract=AsyncMock(return_value=claims),
        agents=lambda: get_agents(models=("fake-fast", "fake-mid")),
        adjudicate=AsyncMock(
            return_value=AdjudicationResult(narrative="Synthetic test: no open disputes.")
        ),
    )

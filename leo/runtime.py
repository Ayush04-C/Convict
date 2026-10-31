"""Explicit pipeline dependencies; synthetic mode never silently substitutes for live data."""

from dataclasses import dataclass
from typing import Callable, Any

from agents.base import StubSpecialist
from agents.registry import get_agents
from app.schemas import Claim, ClaimSet
from ingest.pdf_parser import parse_deck
from ingest.claim_extractor import extract_claims
from ingest.normalize import normalize_claims
from leo.adjudicator import adjudicate
from leo.context import AGENTS


@dataclass
class PipelineDependencies:
    provider: Any
    llm: Any
    parse: Callable = parse_deck
    extract: Callable = extract_claims
    normalize: Callable = normalize_claims
    agents: Callable = get_agents
    adjudicate: Callable = adjudicate


async def synthetic_claims(deck):
    return ClaimSet(
        company_name="Synthetic demo",
        is_stub=True,
        claims=(
            Claim(
                id="C1",
                topic="team_size",
                text="SYNTHETIC: twelve employees",
                value=12,
                unit="count",
                slide=1,
            ),
        ),
    )


class SyntheticProvider:
    def payload(self, name="Synthetic demo", domain="example.com"):
        return [
            {
                "matched_on": domain,
                "matches": [
                    {
                        "confidence_score": 1,
                        "company_data": {
                            "basic_info": {
                                "crustdata_company_id": 1,
                                "name": name,
                                "primary_domain": domain,
                            },
                            "headcount": {"total": 12},
                        },
                    }
                ],
            }
        ]

    async def identify(self, names=None, domains=None):
        return self.payload(
            names[0] if names else "Synthetic demo", domains[0] if domains else "example.com"
        )

    async def enrich_result(self, **kwargs):
        from crustdata.cache import ProviderResponse, json_text

        return ProviderResponse(json_text(self.payload()), "2026-10-31T00:00:00Z")


class NoLiveLlm:
    async def generate(self, *args, **kwargs):
        raise RuntimeError("Live language models are disabled in synthetic mode.")


def synthetic_dependencies():
    return PipelineDependencies(
        SyntheticProvider(),
        NoLiveLlm(),
        extract=synthetic_claims,
        agents=lambda: [StubSpecialist(a) for a in AGENTS],
    )

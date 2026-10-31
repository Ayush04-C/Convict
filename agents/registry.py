"""Ayush: the only foundation composition point importing concrete agents."""

from agents.funding import FundingAgent
from agents.identity import IdentityAgent
from agents.protocol import SpecialistAgent
from agents.scale_team import ScaleTeamAgent
from agents.traction import TractionAgent


def get_agents(*, models: tuple[str, str] | None = None) -> list[SpecialistAgent]:
    return [
        IdentityAgent(models=models),
        ScaleTeamAgent(models=models),
        FundingAgent(models=models),
        TractionAgent(models=models),
    ]

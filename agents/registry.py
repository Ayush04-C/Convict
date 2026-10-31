"""Ayush: the only foundation composition point importing concrete agents."""
from agents.funding import FundingAgent
from agents.identity import IdentityAgent
from agents.protocol import SpecialistAgent
from agents.scale_team import ScaleTeamAgent
from agents.traction import TractionAgent


def get_agents() -> list[SpecialistAgent]:
    return [IdentityAgent(), ScaleTeamAgent(), FundingAgent(), TractionAgent()]

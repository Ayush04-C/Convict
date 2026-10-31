"""Daksh: replace IdentityAgent methods with real checks and ADK behavior."""
from agents.base import StubSpecialist


class IdentityAgent(StubSpecialist):
    name = "identity"

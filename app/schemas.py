"""Shared contracts. Ayush maintains; coordinate changes after handoff."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentName = Literal["identity", "scale_team", "funding", "traction"]
Topic = Literal[
    "identity_founding", "identity_hq", "identity_founder", "team_size", "team_growth",
    "funding_raised", "funding_round", "investors", "revenue_arr", "revenue_growth",
    "valuation_ask", "traction_users", "traction_customers", "hiring", "product_other",
]
FindingStatus = Literal["VERIFIED", "PARTIAL", "CONTRADICTED", "UNVERIFIABLE"]
Phase = Literal["QUEUED", "INGEST", "RESOLVE", "ROUND1", "CHALLENGE", "REPLY",
                "ADJUDICATE", "SCORING", "DONE", "FAILED", "CANNOT_ASSESS"]
RunStatus = Literal["queued", "parsing", "resolving", "round1", "round2", "scoring",
                    "done", "cannot_assess", "failed"]
DecisionTier = Literal["INVEST", "INVEST_WITH_CONDITIONS", "DIG_DEEPER", "PASS"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Claim(FrozenModel):
    id: str = Field(min_length=1)
    topic: Topic
    text: str
    value: float | str | None = None
    unit: str | None = None
    period: str | None = None
    as_of: str | None = None
    slide: int = Field(ge=1)
    testable: bool = True


class ClaimSet(FrozenModel):
    company_name: str | None = None
    claims: tuple[Claim, ...] = ()
    is_stub: bool = False


class DeckContent(FrozenModel):
    path: str
    page_count: int = Field(ge=0)
    processed_pages: int = Field(ge=0)
    warnings: tuple[str, ...] = ()
    is_stub: bool = False


class Policy(FrozenModel):
    max_check_usd: float = Field(ge=0)
    stage: str = "unspecified"


class Dossier(FrozenModel):
    company_id: int
    data_json: str
    fetched_at: str
    source_updated_at: str | None = None
    is_stub: bool = False


class ContextPacket(FrozenModel):
    agent: AgentName
    claims: tuple[Claim, ...]
    policy: Policy
    dossier_json: str = "{}"
    is_stub: bool = False


class Evidence(FrozenModel):
    run_id: str
    evidence_id: str
    agent: str
    source: str
    field_path: str
    value_json: str
    fetched_at: str
    source_updated_at: str | None = None


class Metric(FrozenModel):
    run_id: str
    agent: AgentName
    name: str
    value: float | None
    unit: str
    formula: str
    evidence_ids: tuple[str, ...] = ()


class Finding(FrozenModel):
    finding_id: str
    agent: AgentName
    claim_id: str | None = None
    status: FindingStatus
    severity: int = Field(default=1, ge=1, le=5)
    severity_final: int | None = Field(default=None, ge=1, le=5)
    note: str
    evidence_ids: tuple[str, ...] = ()
    round: int = Field(default=1, ge=1)
    is_stub: bool = False


class Challenge(FrozenModel):
    challenge_id: str
    from_agent: AgentName
    to_agent: AgentName
    finding_id: str
    type: Literal["CONTRADICTS", "CONTEXT", "CORROBORATES", "GAP"]
    argument: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class Reply(FrozenModel):
    reply_id: str
    challenge_id: str
    agent: AgentName
    action: Literal["CONCEDE", "REBUT", "REVISE"]
    text: str
    new_severity: int | None = Field(default=None, ge=1, le=5)


class FindingRevision(FrozenModel):
    revision_id: str
    finding_id: str
    author: str
    severity: int = Field(ge=1, le=5)
    reason: str
    evidence_ids: tuple[str, ...] = ()


class Verdict(FrozenModel):
    agent: AgentName
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    stance: str
    flags: tuple[str, ...] = Field(default=(), max_length=3)
    is_stub: bool = False


class AdjudicationResult(FrozenModel):
    narrative: str
    revisions: tuple[FindingRevision, ...] = ()
    is_stub: bool = False


class Resolution(FrozenModel):
    status: Literal["resolved", "cannot_assess"]
    company_id: int | None = None
    reason: str
    candidates_json: str = "[]"


class Event(FrozenModel):
    event_id: int
    run_id: str
    kind: str
    payload_json: str
    created_at: str


class Decision(FrozenModel):
    total_score: float = Field(ge=0, le=100)
    credibility: float | None = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    verdict: DecisionTier
    check_size_usd: float = Field(ge=0)
    gates: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    is_stub: bool = False

"""Trusted, run-scoped ledger API. Specialists must use LedgerWriter.

Every mutation uses one short transaction, including its progress event. Optional
actor arguments enforce namespace and phase checks inside that same transaction.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from uuid import uuid4

from app.schemas import (
    AdjudicationResult, AgentName, Challenge, Claim, ClaimSet, ContextPacket, Decision,
    Event, Evidence, Finding, FindingRevision, Metric, Phase, Policy, Reply, RunStatus, Verdict,
)
from ledger.db import connection, transaction
from ledger.errors import (
    LedgerConflict, LedgerNotFound, LedgerPermissionError, LedgerReferenceError, LedgerStateError,
)

AGENTS = frozenset(("identity", "scale_team", "funding", "traction"))
PHASES = ("QUEUED", "INGEST", "RESOLVE", "ROUND1", "CHALLENGE", "REPLY", "ADJUDICATE", "SCORING", "DONE")
TERMINAL = frozenset(("DONE", "FAILED", "CANNOT_ASSESS"))
STATUS = dict(zip(PHASES, ("queued", "parsing", "resolving", "round1", "round2", "round2", "round2", "scoring", "done")))
STATUS.update(FAILED="failed", CANNOT_ASSESS="cannot_assess")
AGENT_PHASES = frozenset(("ROUND1", "CHALLENGE", "REPLY"))


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    try:
        return json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise ValueError("Ledger payload must be finite JSON data.") from None


def validate_json(value):
    def invalid_constant(_):
        raise ValueError()
    try:
        encode(json.loads(value, parse_constant=invalid_constant))
    except (TypeError, ValueError):
        raise ValueError("Invalid ledger JSON field.") from None


async def one(db, sql, params=()):
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def rows(db, sql, params=()):
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchall()


async def run_record(db, run_id):
    row = await one(db, "SELECT * FROM runs WHERE run_id=?", (run_id,))
    if row is None:
        raise LedgerNotFound("Run does not exist.")
    return row


async def event(db, run_id, kind, payload):
    async with db.execute("INSERT INTO events(run_id,kind,payload_json,created_at) VALUES (?,?,?,?)",
                          (run_id, kind, encode(payload), now())) as cursor:
        return cursor.lastrowid


async def references(db, run_id, evidence_ids, actor=None):
    private = actor is not None and (await run_record(db, run_id))["phase"] == "ROUND1"
    for evidence_id in evidence_ids:
        record = await one(db, "SELECT agent FROM evidence WHERE run_id=? AND evidence_id=?",
                           (run_id, evidence_id))
        if record is None:
            raise LedgerReferenceError("Evidence does not exist in this run.")
        if private and record["agent"] not in (actor, "leo"):
            raise LedgerPermissionError("Other agents' evidence is private before the round-one barrier.")


class LedgerRepository:
    def __init__(self, path: str | Path, *, timeout: float = 5):
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Database timeout must be finite and nonnegative.")
        self.path, self.timeout = Path(path), timeout

    @asynccontextmanager
    async def _write(self, run_id, actor=None, phases=AGENT_PHASES):
        async with connection(self.path, timeout=self.timeout) as db, transaction(db):
            run = await run_record(db, run_id)
            if run["phase"] in TERMINAL:
                raise LedgerStateError("Run is already terminal.")
            if actor is not None:
                if actor not in AGENTS:
                    raise LedgerPermissionError("Unknown specialist namespace.")
                if run["phase"] not in phases:
                    raise LedgerPermissionError("Operation is not allowed in this phase.")
                state = await one(db, "SELECT state FROM agent_status WHERE run_id=? AND agent=? AND phase=?",
                                  (run_id, actor, run["phase"]))
                if state and state["state"] in ("DONE", "FAILED"):
                    raise LedgerPermissionError("Agent has already finished this phase.")
            yield db

    async def _read(self, run_id, sql, params=()):
        async with connection(self.path, timeout=self.timeout) as db:
            await run_record(db, run_id)
            return await rows(db, sql, (run_id, *params))

    async def create_run(self, user: str, company_name: str, domain: str | None,
                         policy: Policy, deck_path: str) -> str:
        if not user.strip():
            raise ValueError("Run owner cannot be empty.")
        run_id = str(uuid4())
        async with connection(self.path, timeout=self.timeout) as db, transaction(db):
            await db.execute("INSERT INTO runs(run_id,user,company_name,domain,policy_json,deck_path,created_at) "
                             "VALUES (?,?,?,?,?,?,?)", (run_id, user, company_name, domain,
                                                       policy.model_dump_json(), deck_path, now()))
            await event(db, run_id, "run_created", {"status": "queued", "phase": "QUEUED"})
        return run_id

    async def get_run(self, run_id: str, *, user: str | None = None) -> dict:
        async with connection(self.path, timeout=self.timeout) as db:
            run = dict(await run_record(db, run_id))
            if user is not None and run["user"] != user:
                raise LedgerNotFound("Run does not exist.")
            run["policy"] = Policy.model_validate_json(run.pop("policy_json"))
            return run

    async def set_phase(self, run_id: str, phase: Phase, status: RunStatus) -> None:
        if phase not in STATUS or STATUS[phase] != status:
            raise LedgerStateError("Phase and user-facing status do not match.")
        async with connection(self.path, timeout=self.timeout) as db, transaction(db):
            run = await run_record(db, run_id)
            current = run["phase"]
            if current == phase and run["status"] == status:
                return
            if current in TERMINAL:
                raise LedgerStateError("Terminal runs cannot change phase.")
            if phase == "CANNOT_ASSESS" and current != "RESOLVE":
                raise LedgerStateError("Cannot-assess is only valid during resolution.")
            if phase not in ("FAILED", "CANNOT_ASSESS"):
                if phase == "DONE" or PHASES.index(phase) != PHASES.index(current) + 1:
                    raise LedgerStateError("Advance one phase at a time; save_decision completes the run.")
            await db.execute("UPDATE runs SET phase=?,status=? WHERE run_id=?", (phase, status, run_id))
            await event(db, run_id, "done" if phase in TERMINAL else "phase_changed",
                        {"phase": phase, "status": status})

    async def wait_for_phase(self, run_id: str, phase: Phase, timeout: float) -> bool:
        if phase not in STATUS or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Invalid phase or timeout.")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            # Reads use a bounded connection timeout, never a long write transaction.
            async with connection(self.path, timeout=min(self.timeout, remaining)) as db:
                run = await run_record(db, run_id)
            current = run["phase"]
            if current == phase:
                return True
            if current in TERMINAL or (phase in PHASES and PHASES.index(current) > PHASES.index(phase)):
                return False
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.025, remaining))

    async def set_agent_status(self, run_id: str, agent: AgentName, phase: Phase,
                               state: str, error: str | None = None) -> None:
        if agent not in AGENTS or phase not in AGENT_PHASES or state not in ("PENDING", "RUNNING", "DONE", "FAILED"):
            raise LedgerStateError("Invalid agent execution status.")
        async with self._write(run_id) as db:
            run = await run_record(db, run_id)
            if run["phase"] != phase:
                raise LedgerStateError("Agent status does not match the current phase.")
            previous = await one(db, "SELECT * FROM agent_status WHERE run_id=? AND agent=? AND phase=?",
                                 (run_id, agent, phase))
            if previous and previous["state"] == state:
                return
            allowed = {None: {"PENDING", "RUNNING", "FAILED"}, "PENDING": {"RUNNING", "FAILED"},
                       "RUNNING": {"DONE", "FAILED"}, "DONE": set(), "FAILED": set()}
            if state not in allowed[previous["state"] if previous else None]:
                raise LedgerStateError("Invalid agent status transition.")
            started = previous["started_at"] if previous else None
            started = started or (now() if state == "RUNNING" else None)
            ended = now() if state in ("DONE", "FAILED") else None
            await db.execute("INSERT INTO agent_status(run_id,agent,phase,state,error,started_at,ended_at) "
                             "VALUES (?,?,?,?,?,?,?) ON CONFLICT(run_id,agent,phase) DO UPDATE SET "
                             "state=excluded.state,error=excluded.error,started_at=excluded.started_at,ended_at=excluded.ended_at",
                             (run_id, agent, phase, state, error, started, ended))
            await event(db, run_id, "agent_status", {"agent": agent, "phase": phase, "state": state})

    async def get_agent_statuses(self, run_id: str) -> tuple[dict, ...]:
        return tuple(dict(r) for r in await self._read(run_id, "SELECT * FROM agent_status WHERE run_id=? ORDER BY agent,phase"))

    async def add_event(self, run_id: str, kind: str, payload: dict) -> int:
        if not kind.strip():
            raise ValueError("Event kind cannot be empty.")
        async with self._write(run_id) as db:
            return await event(db, run_id, kind, payload)

    async def get_events(self, run_id: str, after: int = 0, *, limit: int = 1000) -> tuple[Event, ...]:
        if after < 0 or not 1 <= limit <= 1000:
            raise ValueError("Invalid event cursor or limit.")
        result = await self._read(run_id, "SELECT * FROM events WHERE run_id=? AND event_id>? ORDER BY event_id LIMIT ?", (after, limit))
        return tuple(Event.model_validate(dict(r)) for r in result)

    async def add_claims(self, run_id: str, claims: ClaimSet) -> None:
        async with self._write(run_id) as db:
            await db.execute("INSERT INTO claim_sets VALUES (?,?,?)",
                             (run_id, claims.company_name, claims.is_stub))
            for claim in claims.claims:
                await db.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (run_id, claim.id, claim.topic, claim.text, encode(claim.value),
                                  claim.unit, claim.period, claim.as_of, claim.slide, claim.testable))
            await event(db, run_id, "claims_extracted", {"count": len(claims.claims), "is_stub": claims.is_stub})

    async def get_claims(self, run_id: str) -> ClaimSet:
        async with connection(self.path, timeout=self.timeout) as db:
            await db.execute("BEGIN")
            await run_record(db, run_id)
            meta = await one(db, "SELECT * FROM claim_sets WHERE run_id=?", (run_id,))
            result = await rows(db, "SELECT * FROM claims WHERE run_id=? ORDER BY rowid", (run_id,))
        return ClaimSet(company_name=meta["company_name"] if meta else None,
                        is_stub=bool(meta["is_stub"]) if meta else False,
                        claims=tuple(Claim(id=r["claim_id"], topic=r["topic"], text=r["text"],
                                           value=json.loads(r["value"]) if r["value"] is not None else None,
                                           unit=r["unit"], period=r["period"], as_of=r["as_of"],
                                           slide=r["slide"], testable=bool(r["testable"])) for r in result))

    async def save_context_packets(self, run_id: str, packets: tuple[ContextPacket, ...]) -> None:
        async with self._write(run_id) as db:
            for packet in packets:
                validate_json(packet.dossier_json)
                for claim in packet.claims:
                    if await one(db, "SELECT 1 FROM claims WHERE run_id=? AND claim_id=?", (run_id, claim.id)) is None:
                        raise LedgerReferenceError("Context claim does not belong to this run.")
                await db.execute("INSERT INTO context_packets VALUES (?,?,?)",
                                 (run_id, packet.agent, packet.model_dump_json()))
            await event(db, run_id, "context_saved", {"agents": [p.agent for p in packets]})

    async def get_context_packet(self, run_id: str, agent: AgentName) -> ContextPacket:
        result = await self._read(run_id, "SELECT packet_json FROM context_packets WHERE run_id=? AND agent=?", (agent,))
        if not result:
            raise LedgerNotFound("Context packet does not exist.")
        return ContextPacket.model_validate_json(result[0][0])

    @staticmethod
    def _owner(item, actor):
        if item.agent not in AGENTS | {"leo"}:
            raise LedgerPermissionError("Unknown record namespace.")
        if actor is not None and item.agent != actor:
            raise LedgerPermissionError("Cannot write another agent's records.")

    async def add_evidence(self, run_id: str, evidence: tuple[Evidence, ...], *, actor: AgentName | None = None) -> None:
        async with self._write(run_id, actor) as db:
            for item in evidence:
                self._owner(item, actor)
                if item.run_id != run_id:
                    raise LedgerReferenceError("Evidence belongs to another run.")
                validate_json(item.value_json)
                await db.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)",
                                 (run_id, item.evidence_id, item.agent, item.source, item.field_path,
                                  item.value_json, item.fetched_at, item.source_updated_at))
            await event(db, run_id, "evidence_added", {"count": len(evidence), "agent": actor})

    async def get_evidence(self, run_id: str) -> tuple[Evidence, ...]:
        result = await self._read(run_id, "SELECT * FROM evidence WHERE run_id=? ORDER BY rowid")
        return tuple(Evidence(run_id=r["run_id"], evidence_id=r["evidence_id"], agent=r["agent"],
                              source=r["source"], field_path=r["field_path"], value_json=r["value_json"],
                              fetched_at=r["fetched_at"], source_updated_at=r["crustdata_updated_at"]) for r in result)

    async def add_metrics(self, run_id: str, metrics: tuple[Metric, ...], *, actor: AgentName | None = None) -> None:
        async with self._write(run_id, actor) as db:
            for item in metrics:
                self._owner(item, actor)
                if item.run_id != run_id:
                    raise LedgerReferenceError("Metric belongs to another run.")
                await references(db, run_id, item.evidence_ids, actor)
                await db.execute("INSERT INTO metrics(run_id,agent,name,value,unit,formula,evidence_ids) VALUES (?,?,?,?,?,?,?)",
                                 (run_id, item.agent, item.name, item.value, item.unit, item.formula, encode(item.evidence_ids)))
            await event(db, run_id, "metrics_added", {"count": len(metrics), "agent": actor})

    async def get_metrics(self, run_id: str) -> tuple[Metric, ...]:
        result = await self._read(run_id, "SELECT * FROM metrics WHERE run_id=? ORDER BY metric_id")
        return tuple(Metric(run_id=r["run_id"], agent=r["agent"], name=r["name"], value=r["value"],
                            unit=r["unit"], formula=r["formula"], evidence_ids=json.loads(r["evidence_ids"])) for r in result)

    async def add_findings(self, run_id: str, findings: tuple[Finding, ...], *, actor: AgentName | None = None) -> None:
        async with self._write(run_id, actor, {"ROUND1"}) as db:
            for item in findings:
                self._owner(item, actor)
                if item.severity_final is not None:
                    raise LedgerStateError("Append a revision instead of supplying final severity on a new finding.")
                if item.claim_id is not None and await one(db, "SELECT 1 FROM claims WHERE run_id=? AND claim_id=?", (run_id, item.claim_id)) is None:
                    raise LedgerReferenceError("Finding claim does not belong to this run.")
                await references(db, run_id, item.evidence_ids, actor)
                status = item.status if item.evidence_ids else "UNVERIFIABLE"
                await db.execute("INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (run_id, item.finding_id, item.agent, item.claim_id, status, item.severity,
                                  item.note, encode(item.evidence_ids), item.round, item.is_stub))
            await event(db, run_id, "findings_added", {"count": len(findings), "agent": actor})

    async def get_all_findings(self, run_id: str, *, actor: AgentName | None = None) -> tuple[Finding, ...]:
        async with connection(self.path, timeout=self.timeout) as db:
            await db.execute("BEGIN")
            run = await run_record(db, run_id)
            if actor is not None and (actor not in AGENTS or run["phase"] not in ("CHALLENGE", "REPLY", "ADJUDICATE", "SCORING", "DONE")):
                raise LedgerPermissionError("Cross-agent findings are not readable before the round-one barrier.")
            result = await rows(db, "SELECT * FROM effective_findings WHERE run_id=? ORDER BY finding_id", (run_id,))
        return tuple(Finding(finding_id=r["finding_id"], agent=r["agent"], claim_id=r["claim_id"],
                             status=r["status"], severity=r["severity"], severity_final=r["severity_final"],
                             note=r["note"], evidence_ids=json.loads(r["evidence_ids"]), round=r["round"],
                             is_stub=bool(r["is_stub"])) for r in result)

    async def add_challenges(self, run_id: str, challenges: tuple[Challenge, ...], *, actor: AgentName | None = None) -> None:
        async with self._write(run_id, actor, {"CHALLENGE"}) as db:
            for item in challenges:
                if actor is not None and item.from_agent != actor:
                    raise LedgerPermissionError("Cannot challenge as another agent.")
                if item.from_agent == item.to_agent:
                    raise LedgerPermissionError("An agent cannot challenge itself.")
                target = await one(db, "SELECT agent FROM findings WHERE run_id=? AND finding_id=?", (run_id, item.finding_id))
                if target is None or target["agent"] != item.to_agent:
                    raise LedgerReferenceError("Challenge target finding does not belong to its target agent in this run.")
                count = (await one(db, "SELECT count(*) FROM challenges WHERE run_id=? AND from_agent=?", (run_id, item.from_agent)))[0]
                if count >= 2:
                    raise LedgerConflict("At most two challenges per agent are allowed.")
                await references(db, run_id, item.evidence_ids)
                await db.execute("INSERT INTO challenges VALUES (?,?,?,?,?,?,?,?)",
                                 (run_id, item.challenge_id, item.from_agent, item.to_agent, item.finding_id,
                                  item.type, item.argument, encode(item.evidence_ids)))
            await event(db, run_id, "challenges_added", {"count": len(challenges), "agent": actor})

    async def get_challenges_for(self, run_id: str, agent: AgentName, *, actor: AgentName | None = None) -> tuple[Challenge, ...]:
        async with connection(self.path, timeout=self.timeout) as db:
            await db.execute("BEGIN")
            run = await run_record(db, run_id)
            if actor is not None and (actor != agent or actor not in AGENTS or run["phase"] not in ("REPLY", "ADJUDICATE", "SCORING", "DONE")):
                raise LedgerPermissionError("Only own challenges are readable after the challenge barrier.")
            result = await rows(db, "SELECT * FROM challenges WHERE run_id=? AND to_agent=? ORDER BY challenge_id", (run_id, agent))
        return tuple(Challenge(challenge_id=r["challenge_id"], from_agent=r["from_agent"], to_agent=r["to_agent"],
                               finding_id=r["finding_id"], type=r["type"], argument=r["argument"],
                               evidence_ids=json.loads(r["evidence_ids"])) for r in result)

    async def add_reply(self, run_id: str, reply: Reply, *, actor: AgentName | None = None) -> None:
        async with self._write(run_id, actor, {"REPLY"}) as db:
            self._owner(reply, actor)
            target = await one(db, "SELECT * FROM challenges WHERE run_id=? AND challenge_id=?", (run_id, reply.challenge_id))
            if target is None:
                raise LedgerReferenceError("Reply challenge does not exist in this run.")
            if target["to_agent"] != reply.agent:
                raise LedgerPermissionError("Cannot reply to another agent's challenge.")
            if (reply.action == "REVISE") != (reply.new_severity is not None):
                raise LedgerStateError("REVISE requires a new severity; other reply actions cannot set one.")
            await db.execute("INSERT INTO replies(run_id,reply_id,challenge_id,agent,action,text,new_severity) VALUES (?,?,?,?,?,?,?)",
                             (run_id, reply.reply_id, reply.challenge_id, reply.agent, reply.action, reply.text, reply.new_severity))
            if reply.new_severity is not None:
                await self._revision(db, run_id, FindingRevision(
                    revision_id="reply:" + reply.reply_id, finding_id=target["finding_id"],
                    author=reply.agent, severity=reply.new_severity, reason=reply.text,
                    evidence_ids=tuple(json.loads(target["evidence_ids"]))), actor=reply.agent)
            await event(db, run_id, "reply_added", {"agent": reply.agent, "reply_id": reply.reply_id, "action": reply.action})

    async def get_replies(self, run_id: str) -> tuple[Reply, ...]:
        result = await self._read(run_id, "SELECT * FROM replies WHERE run_id=? ORDER BY rowid")
        return tuple(Reply(reply_id=r["reply_id"], challenge_id=r["challenge_id"], agent=r["agent"],
                           action=r["action"], text=r["text"], new_severity=r["new_severity"]) for r in result)

    async def _revision(self, db, run_id, revision, actor=None):
        target = await one(db, "SELECT agent FROM findings WHERE run_id=? AND finding_id=?", (run_id, revision.finding_id))
        if target is None:
            raise LedgerReferenceError("Revision finding does not exist in this run.")
        if revision.author not in (target["agent"], "leo") or (actor is not None and revision.author != actor):
            raise LedgerPermissionError("Only the finding owner or trusted adjudicator can revise severity.")
        await references(db, run_id, revision.evidence_ids)
        await db.execute("INSERT INTO finding_revisions(run_id,revision_id,finding_id,author,severity,reason,evidence_ids) VALUES (?,?,?,?,?,?,?)",
                         (run_id, revision.revision_id, revision.finding_id, revision.author, revision.severity,
                          revision.reason, encode(revision.evidence_ids)))

    async def add_revision(self, run_id: str, revision: FindingRevision, *, actor: AgentName | None = None) -> None:
        async with self._write(run_id, actor, {"REPLY"}) as db:
            await self._revision(db, run_id, revision, actor)
            await event(db, run_id, "finding_revised", {"finding_id": revision.finding_id, "author": revision.author})

    async def get_revisions(self, run_id: str) -> tuple[FindingRevision, ...]:
        result = await self._read(run_id, "SELECT * FROM finding_revisions WHERE run_id=? ORDER BY revision_seq")
        return tuple(FindingRevision(revision_id=r["revision_id"], finding_id=r["finding_id"], author=r["author"],
                                     severity=r["severity"], reason=r["reason"], evidence_ids=json.loads(r["evidence_ids"])) for r in result)

    async def add_verdict(self, run_id: str, verdict: Verdict, *, actor: AgentName | None = None) -> None:
        async with self._write(run_id, actor, {"REPLY"}) as db:
            self._owner(verdict, actor)
            await db.execute("INSERT INTO verdicts VALUES (?,?,?,?,?,?,?)",
                             (run_id, verdict.agent, verdict.score, verdict.confidence, verdict.stance,
                              encode(verdict.flags), verdict.is_stub))
            await event(db, run_id, "verdict_added", {"agent": verdict.agent, "is_stub": verdict.is_stub})

    async def get_verdicts(self, run_id: str) -> tuple[Verdict, ...]:
        result = await self._read(run_id, "SELECT * FROM verdicts WHERE run_id=? ORDER BY agent")
        return tuple(Verdict(agent=r["agent"], score=r["score"], confidence=r["confidence"], stance=r["stance"],
                             flags=json.loads(r["flags_json"]), is_stub=bool(r["is_stub"])) for r in result)

    async def save_decision(self, run_id: str, decision: Decision, adjudication: AdjudicationResult) -> None:
        async with self._write(run_id) as db:
            run = await run_record(db, run_id)
            if run["phase"] != "SCORING":
                raise LedgerStateError("Decisions can only complete a run during scoring.")
            for revision in adjudication.revisions:
                stored = await one(db, "SELECT * FROM finding_revisions WHERE run_id=? AND revision_id=?", (run_id, revision.revision_id))
                if stored is None or (stored["finding_id"], stored["author"], stored["severity"], stored["reason"], tuple(json.loads(stored["evidence_ids"]))) != (revision.finding_id, revision.author, revision.severity, revision.reason, revision.evidence_ids):
                    raise LedgerReferenceError("Apply adjudication revisions before computing and saving the decision.")
            provenance = await one(db, "SELECT is_stub FROM claim_sets WHERE run_id=?", (run_id,))
            stub = decision.is_stub or adjudication.is_stub or bool(provenance and provenance[0])
            stub = stub or bool(await one(db, "SELECT 1 FROM verdicts WHERE run_id=? AND is_stub=1", (run_id,)))
            stub = stub or bool(await one(db, "SELECT 1 FROM findings WHERE run_id=? AND is_stub=1", (run_id,)))
            decision = decision.model_copy(update={"is_stub": stub})
            await db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (run_id, decision.total_score, decision.credibility, decision.confidence,
                              decision.verdict, encode(decision.gates), decision.check_size_usd,
                              adjudication.narrative, encode({"decision": decision.model_dump(),
                                                             "adjudication": adjudication.model_dump()}), stub))
            await db.execute("UPDATE runs SET phase='DONE',status='done' WHERE run_id=?", (run_id,))
            await event(db, run_id, "done", {"phase": "DONE", "status": "done", "is_stub": stub})

    async def get_decision(self, run_id: str) -> Decision:
        result = await self._read(run_id, "SELECT breakdown_json FROM decisions WHERE run_id=?")
        if not result:
            raise LedgerNotFound("Decision is not available yet.")
        return Decision.model_validate(json.loads(result[0][0])["decision"])

    async def get_adjudication(self, run_id: str) -> AdjudicationResult:
        result = await self._read(run_id, "SELECT breakdown_json FROM decisions WHERE run_id=?")
        if not result:
            raise LedgerNotFound("Adjudication is not available yet.")
        return AdjudicationResult.model_validate(json.loads(result[0][0])["adjudication"])

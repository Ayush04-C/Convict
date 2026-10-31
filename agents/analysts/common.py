"""Daksh: shared types and helpers for the pure-Python analyst modules.

Analysts never call HTTP or an LLM. Each check takes (claim, data) where `data` is the
company_data-shaped dict (groups such as `funding` or `revenue` at the top level) and
returns a CheckResult. Missing or null data always yields UNVERIFIABLE, never an error.
Gates are only *proposed* here; confirming a hard gate is the orchestrator's decision.
"""
import math
from dataclasses import dataclass
from typing import Any

from app.schemas import Claim, FindingStatus


@dataclass(frozen=True)
class MetricResult:
    name: str
    value: float | None
    unit: str
    formula: str


@dataclass(frozen=True)
class CheckResult:
    check: str
    status: FindingStatus
    severity: int = 1
    note: str = ""
    claim_id: str | None = None
    evidence_paths: tuple[str, ...] = ()
    metrics: tuple[MetricResult, ...] = ()
    gate: str | None = None


def get(data: Any, *keys: str) -> Any:
    """Null-safe nested dict lookup: get(data, "revenue", "estimated", "upper_bound_usd")."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.replace(",", "").strip())
        except ValueError:
            return None
    else:
        return None
    return result if math.isfinite(result) else None


def claim_amount_usd(claim: Claim | None) -> float | None:
    """The claim's numeric value, only when it is explicitly in USD."""
    if claim is None or claim.unit != "USD":
        return None
    return number(claim.value)


def pct_diff(claimed: float, actual: float | None) -> float | None:
    """(claimed - actual) / actual, or None when actual is missing or not positive."""
    if actual is None or actual <= 0:
        return None
    return (claimed - actual) / actual


def unverifiable(check: str, claim: Claim | None, why: str,
                 evidence_paths: tuple[str, ...] = ()) -> CheckResult:
    return CheckResult(check=check, status="UNVERIFIABLE", severity=1, note=why,
                       claim_id=claim.id if claim else None, evidence_paths=evidence_paths)


def dimension_score(results: list[CheckResult] | tuple[CheckResult, ...]) -> float:
    """100 minus 6 * (severity - 1) per CONTRADICTED check, floored at 0 (low-level doc 9)."""
    penalty = sum(6 * (r.severity - 1) for r in results if r.status == "CONTRADICTED")
    return max(0.0, 100.0 - penalty)


def analyst_confidence(results: list[CheckResult] | tuple[CheckResult, ...]) -> float:
    """Share of checks that could actually be verified; 0 when nothing ran."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.status != "UNVERIFIABLE") / len(results)

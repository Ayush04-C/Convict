"""Daksh: replace with ledger-grounded dispute adjudication and narrative."""
from app.schemas import AdjudicationResult


async def adjudicate(run_id: str) -> AdjudicationResult:
    return AdjudicationResult(narrative="STUB: No disputes adjudicated.", is_stub=True)

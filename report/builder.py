"""Ayush: persistent evidence-backed report builder comes in Stage 8."""
from ledger.repo import LedgerRepository


async def build_report(run_id: str, repo: LedgerRepository) -> dict:
    raise NotImplementedError("Stage 8: decision, evidence dates, claim table and debate log")

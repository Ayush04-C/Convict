"""Owned run upload/status and persisted event replay; reports/chat are later stages."""

import asyncio
import json
import math
from pathlib import Path
import os
import tempfile

import anyio
from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile, Response
from sse_starlette.sse import EventSourceResponse
from starlette.responses import JSONResponse

from app.auth import get_current_user
from app.errors import ApiError
from app.jobs import QueueFull
from app.schemas import Policy
from app.uploads import MAX_UPLOAD_BYTES
from crustdata.client import normalize_domain
from ledger.errors import LedgerNotFound
from ledger.repo import TERMINAL
from report.builder import build_report
from report.markdown import render_markdown

router = APIRouter(prefix="/runs", tags=["Runs"])


async def owned(request, run_id, user):
    try:
        return await request.app.state.ledger.get_run(run_id, user=user)
    except LedgerNotFound:
        raise ApiError(404, "RUN_NOT_FOUND", "Run does not exist.") from None


async def save_upload(deck, directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix="deck-", suffix=".pdf", dir=directory)
    os.close(fd)
    path = Path(name)
    count = 0
    try:
        async with await anyio.open_file(path, "wb") as handle:
            while chunk := await deck.read(128 * 1024):
                count += len(chunk)
                if count > MAX_UPLOAD_BYTES:
                    raise ApiError(413, "DECK_TOO_LARGE", "The deck exceeds the 20 MB limit.")
                await handle.write(chunk)
        if not count:
            raise ApiError(422, "EMPTY_DECK", "The deck is empty.")
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


@router.post("", status_code=202)
async def create_run(
    request: Request,
    deck: UploadFile = File(...),
    company_name: str = Form(...),
    domain: str | None = Form(None),
    max_check_usd: float = Form(...),
    stage: str = Form("unspecified"),
    user: str = Depends(get_current_user),
):
    if not company_name.strip() or len(company_name) > 500 or not stage.strip() or len(stage) > 100:
        raise ApiError(422, "VALIDATION_ERROR", "Company name or stage is invalid.")
    if not math.isfinite(max_check_usd) or max_check_usd < 0:
        raise ApiError(422, "VALIDATION_ERROR", "The policy limit must be finite and nonnegative.")
    try:
        domain = normalize_domain(domain) if domain else None
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "Company domain is invalid.") from None
    if not (deck.filename or "").lower().endswith(".pdf") or deck.content_type not in (
        "application/pdf",
        "application/octet-stream",
    ):
        raise ApiError(415, "PDF_REQUIRED", "Upload a PDF deck.")
    manager = getattr(request.app.state, "jobs", None)
    if manager is None:
        raise ApiError(503, "SERVICE_NOT_READY", "Run execution is not ready.")
    path, run_id = None, None
    try:
        async with manager.admit() as submit:
            path = await save_upload(deck, request.app.state.settings.upload_directory)
            run_id = await request.app.state.ledger.create_run(
                user,
                company_name.strip(),
                domain,
                Policy(max_check_usd=max_check_usd, stage=stage.strip()),
                str(path.resolve()),
            )
            submit(run_id)
    except BaseException as error:
        if path is not None:
            path.unlink(missing_ok=True)
        if run_id is not None:
            await request.app.state.ledger.fail_run(run_id, "SCHEDULING_FAILED")
        if isinstance(error, QueueFull):
            raise ApiError(
                429, "RUN_QUEUE_FULL", "Run capacity is full; try again shortly."
            ) from None
        raise
    finally:
        await deck.close()
    return {
        "run_id": run_id,
        "status": "queued",
        "status_url": f"/runs/{run_id}",
        "events_url": f"/runs/{run_id}/events",
        "mode": request.app.state.settings.mode,
    }


@router.get("/{run_id}")
async def get_run(run_id: str, request: Request, user: str = Depends(get_current_user)):
    repo = request.app.state.ledger
    run = await owned(request, run_id, user)
    decision = await repo.get_decision(run_id) if run["phase"] == "DONE" else None
    failed = await repo.latest_event(run_id, "run_failed")
    resolved = await repo.latest_event(run_id, "resolution_saved")
    failure = json.loads(failed.payload_json)["code"] if failed else None
    resolution = json.loads(resolved.payload_json) if resolved else None
    return {
        "run_id": run_id,
        "status": run["status"],
        "phase": run["phase"],
        "company_name": run["company_name"],
        "company_id": run["crustdata_company_id"],
        "agents": await repo.get_agent_statuses(run_id),
        "decision": decision,
        "failure_code": failure,
        "resolution": resolution,
        "is_stub": decision.is_stub if decision else (await repo.get_claims(run_id)).is_stub,
    }


async def event_stream(repo, run_id, after, *, poll_interval=0.5):
    while True:
        events = await repo.get_events(run_id, after=after)
        for event in events:
            after = event.event_id
            yield {"id": str(event.event_id), "event": event.kind, "data": event.payload_json}
        run = await repo.get_run(run_id)
        if run["phase"] in TERMINAL:
            # Terminal row and final event are committed atomically. Drain any events
            # committed between the previous query and the terminal-state read.
            more = await repo.get_events(run_id, after=after)
            if more:
                continue
            return
        await asyncio.sleep(poll_interval)


@router.get("/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(None),
    user: str = Depends(get_current_user),
):
    await owned(request, run_id, user)
    try:
        cursor = int(last_event_id) if last_event_id is not None else after
        if not 0 <= cursor <= 2**63 - 1:
            raise ValueError
    except ValueError:
        raise ApiError(
            422, "INVALID_CURSOR", "Event cursor must be a nonnegative integer."
        ) from None
    return EventSourceResponse(
        event_stream(request.app.state.ledger, run_id, cursor),
        ping=15,
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/report")
async def report(
    run_id: str, request: Request, format: str = "json", user: str = Depends(get_current_user)
):
    run = await owned(request, run_id, user)
    if run["phase"] not in TERMINAL:
        raise ApiError(409, "REPORT_NOT_READY", "The run is still processing.")
    if format not in ("json", "markdown"):
        raise ApiError(422, "INVALID_FORMAT", "Report format must be json or markdown.")
    result = await build_report(run_id, request.app.state.ledger)
    if format == "markdown":
        return Response(
            render_markdown(result),
            media_type="text/markdown",
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/{run_id}/chat")
async def chat(run_id: str, request: Request, user: str = Depends(get_current_user)):
    await owned(request, run_id, user)
    raise ApiError(501, "CHAT_DEFERRED", "Chat is deferred.")

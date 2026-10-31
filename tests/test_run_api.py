"""Authenticated HTTP flow through the real coordinator and specialists, offline."""

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from app.auth import create_access_token
from app.main import create_app
from app.runs import event_stream
from app.schemas import Policy
from tests.conftest import TEST_PASSWORD
from tests.pipeline_support import dependencies, pdf_bytes


@asynccontextmanager
async def api(auth_settings, tmp_path, *, deps=None, **updates):
    config = auth_settings.model_copy(
        update={
            "database_path": tmp_path / "api.sqlite",
            "upload_directory": tmp_path / "uploads",
            "mode": "live",
            **updates,
        }
    )
    app = create_app(config, dependencies=deps if deps is not None else dependencies())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            client.headers["Authorization"] = "Bearer " + create_access_token(
                config.demo_user, config
            )
            yield app, client


async def upload(client, **updates):
    data = {
        "company_name": "Synthetic",
        "domain": "example.com",
        "max_check_usd": "100000",
        "stage": "seed",
        **updates,
    }
    return await client.post(
        "/runs", data=data, files={"deck": ("synthetic.pdf", pdf_bytes(), "application/pdf")}
    )


async def terminal(client, run):
    async with asyncio.timeout(5):
        while True:
            response = await client.get("/runs/" + run)
            assert response.status_code == 200
            record = response.json()
            if record["status"] in ("done", "failed", "cannot_assess"):
                return record
            await asyncio.sleep(0.01)


async def test_login_upload_progress_status_and_gate_end_to_end(auth_settings, tmp_path):
    async with api(auth_settings, tmp_path) as (app, client):
        login = await client.post(
            "/auth/login", json={"username": auth_settings.demo_user, "password": TEST_PASSWORD}
        )
        assert login.status_code == 200
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        submitted = await upload(client)
        assert submitted.status_code == 202
        body = submitted.json()
        result = await terminal(client, body["run_id"])
        assert result["status"] == "done" and result["company_id"] == 123
        assert result["decision"]["gates"] == ["arr_ratio_over_3"]
        assert result["decision"]["verdict"] == "PASS" and result["decision"]["check_size_usd"] == 0
        assert "deck_path" not in result and "user" not in result
        events = await client.get(body["events_url"])
        assert events.status_code == 200 and "text/event-stream" in events.headers["content-type"]
        assert "event: done" in events.text and "event: gates_confirmed" in events.text
        assert events.headers["cache-control"] == "no-store"
        assert len(list((tmp_path / "uploads").glob("*.pdf"))) == 1


async def test_sse_replays_after_header_cursor_and_closes_on_terminal(auth_settings, tmp_path):
    async with api(auth_settings, tmp_path) as (app, client):
        run = (await upload(client)).json()["run_id"]
        await terminal(client, run)
        stored = await app.state.ledger.get_events(run)
        cursor = stored[3].event_id
        response = await client.get(f"/runs/{run}/events", headers={"Last-Event-ID": str(cursor)})
        ids = [
            int(line.removeprefix("id: "))
            for line in response.text.splitlines()
            if line.startswith("id: ")
        ]
        assert ids == [e.event_id for e in stored if e.event_id > cursor]
        last = await client.get(f"/runs/{run}/events?after={stored[-1].event_id}")
        assert last.status_code == 200 and last.text == ""


@pytest.mark.parametrize(
    "suffix,method", [("", "get"), ("/events", "get"), ("/report", "get"), ("/chat", "post")]
)
async def test_other_owner_is_indistinguishable_from_missing_run(
    auth_settings, tmp_path, suffix, method
):
    async with api(auth_settings, tmp_path) as (app, client):
        run = await app.state.ledger.create_run(
            "someone-else", "Private", None, Policy(max_check_usd=0), "private.pdf"
        )
        for ident in (run, "missing"):
            response = await getattr(client, method)(f"/runs/{ident}{suffix}")
            assert (
                response.status_code == 404 and response.json()["error"]["code"] == "RUN_NOT_FOUND"
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("company_name", " "),
        ("domain", "invalid"),
        ("max_check_usd", "-1"),
        ("max_check_usd", "nan"),
        ("max_check_usd", "inf"),
        ("stage", " "),
    ],
)
async def test_bad_metadata_does_not_schedule_or_store_upload(
    auth_settings, tmp_path, field, value
):
    deps = dependencies()
    async with api(auth_settings, tmp_path, deps=deps) as (_, client):
        response = await upload(client, **{field: value})
        assert response.status_code == 422
        deps.extract.assert_not_awaited()
        assert not list((tmp_path / "uploads").glob("*"))


@pytest.mark.parametrize(
    "filename,mime,content,status",
    [
        ("fake.txt", "text/plain", b"hello", 415),
        ("empty.pdf", "application/pdf", b"", 422),
        ("../unsafe.pdf", "application/pdf", b"%PDF-broken", 202),
    ],
)
async def test_file_validation_generated_names_and_async_parse_failure(
    auth_settings, tmp_path, filename, mime, content, status
):
    async with api(auth_settings, tmp_path) as (_, client):
        response = await client.post(
            "/runs",
            data={"company_name": "Synthetic", "max_check_usd": "0"},
            files={"deck": (filename, content, mime)},
        )
        assert response.status_code == status
        if status == 202:
            result = await terminal(client, response.json()["run_id"])
            assert result["status"] == "failed" and result["failure_code"] == "DECK_REJECTED"
            assert not (tmp_path / "unsafe.pdf").exists()


async def test_actual_file_size_limit_removes_partial_upload(auth_settings, tmp_path, monkeypatch):
    monkeypatch.setattr("app.runs.MAX_UPLOAD_BYTES", 100)
    async with api(auth_settings, tmp_path) as (_, client):
        response = await upload(client)
        assert response.status_code == 413
        assert not list((tmp_path / "uploads").glob("*"))


async def test_chunked_request_body_is_bounded_without_trusting_length(
    auth_settings, tmp_path, monkeypatch
):
    monkeypatch.setattr("app.uploads.MAX_REQUEST_BYTES", 100)

    async def chunks():
        yield b"x" * 80
        yield b"x" * 80

    async with api(auth_settings, tmp_path) as (_, client):
        response = await client.post(
            "/runs", content=chunks(), headers={"Content-Type": "application/octet-stream"}
        )
        assert response.status_code == 413
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


async def test_unauthorized_upload_is_rejected_before_reading_body(auth_settings, tmp_path):
    read = []

    async def chunks():
        read.append(True)
        yield b"private-deck"

    async with api(auth_settings, tmp_path) as (_, client):
        client.headers.pop("Authorization")
        response = await client.post("/runs", content=chunks())
        assert response.status_code == 401 and not read


@pytest.mark.parametrize("cursor", ["-1", "nonsense", str(2**80)])
async def test_sse_invalid_cursor_is_safe(auth_settings, tmp_path, cursor):
    async with api(auth_settings, tmp_path) as (app, client):
        run = await app.state.ledger.create_run(
            auth_settings.demo_user, "Synthetic", None, Policy(max_check_usd=0), "not-used"
        )
        response = await client.get(f"/runs/{run}/events", headers={"Last-Event-ID": cursor})
        assert response.status_code == 422


async def test_report_waits_for_terminal_state_and_chat_remains_deferred(auth_settings, tmp_path):
    async with api(auth_settings, tmp_path) as (app, client):
        run = await app.state.ledger.create_run(
            auth_settings.demo_user, "Synthetic", None, Policy(max_check_usd=0), "not-used"
        )
        assert (await client.get(f"/runs/{run}/report")).status_code == 409
        assert (await client.post(f"/runs/{run}/chat")).status_code == 501
        await app.state.ledger.fail_run(run, "TEST")
        report = await client.get(f"/runs/{run}/report")
        assert report.status_code == 200 and report.json()["decision"] is None


async def test_disconnecting_event_consumer_does_not_cancel_job(auth_settings, tmp_path):
    async with api(auth_settings, tmp_path) as (app, client):
        run = (await upload(client)).json()["run_id"]
        stream = event_stream(app.state.ledger, run, 0, poll_interval=0.001)
        assert (await anext(stream))["id"]
        await stream.aclose()
        assert (await terminal(client, run))["status"] == "done"


async def test_queue_limit_and_shutdown_cancel_queued_and_active_runs(auth_settings, tmp_path):
    deps = dependencies()
    started = asyncio.Event()

    async def slow(deck):
        started.set()
        await asyncio.sleep(10)

    deps.extract = slow
    async with api(auth_settings, tmp_path, deps=deps, max_pending_runs=2) as (app, client):
        first = await upload(client)
        await asyncio.wait_for(started.wait(), 3)
        second = await upload(client)
        third = await upload(client)
        assert first.status_code == second.status_code == 202 and third.status_code == 429
        await app.state.jobs.aclose()
        for item in (first, second):
            assert (await terminal(client, item.json()["run_id"]))["status"] == "failed"
        assert len(list((tmp_path / "uploads").glob("*.pdf"))) == 2


async def test_stub_mode_is_offline_and_clearly_labelled(auth_settings, tmp_path):
    config = auth_settings.model_copy(
        update={"database_path": tmp_path / "stub.sqlite", "upload_directory": tmp_path / "uploads"}
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer " + create_access_token(config.demo_user, config)},
        ) as client:
            run = (await upload(client)).json()["run_id"]
            result = await terminal(client, run)
            assert result["status"] == "done" and result["decision"]["is_stub"]


async def test_failed_run_creation_removes_saved_upload(auth_settings, tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    async with api(auth_settings, tmp_path) as (app, client):
        monkeypatch.setattr(
            app.state.ledger, "create_run", AsyncMock(side_effect=RuntimeError("private-key"))
        )
        response = await upload(client)
        assert response.status_code == 500 and "private-key" not in response.text
        assert not list((tmp_path / "uploads").glob("*"))
        assert app.state.jobs._reserved == 0


async def test_failed_scheduling_is_terminal_and_removes_file(auth_settings, tmp_path, monkeypatch):
    async with api(auth_settings, tmp_path) as (app, client):
        created = []
        original = app.state.ledger.create_run

        async def record(*args, **kwargs):
            run = await original(*args, **kwargs)
            created.append(run)
            return run

        monkeypatch.setattr(app.state.ledger, "create_run", record)

        @asynccontextmanager
        async def reject():
            def submit(run_id):
                raise RuntimeError("private-key")

            yield submit

        monkeypatch.setattr(app.state.jobs, "admit", reject)
        response = await upload(client)
        assert response.status_code == 500 and "private-key" not in response.text
        assert (await app.state.ledger.get_run(created[0]))["status"] == "failed"
        assert not list((tmp_path / "uploads").glob("*"))


async def test_terminal_event_committed_between_queries_is_not_lost(ledger):
    run = await ledger.create_run("tester", "Synthetic", None, Policy(max_check_usd=0), "unused")
    original = ledger.get_run
    called = False

    async def race(run_id):
        nonlocal called
        if not called:
            called = True
            await ledger.fail_run(run_id, "TEST_END")
        return await original(run_id)

    ledger.get_run = race
    events = [event async for event in event_stream(ledger, run, 0, poll_interval=0.001)]
    assert events[-1]["event"] == "done"

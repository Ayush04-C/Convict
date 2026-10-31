"""Offline login/upload/pipeline/status/SSE demo using temporary data and stub specialists."""

import asyncio
import json
from pathlib import Path
import secrets
import tempfile

import httpx
from pwdlib import PasswordHash
import pymupdf

from app.main import create_app
from app.settings import Settings


async def run_demo(directory: Path):
    password = secrets.token_urlsafe(24)
    settings = Settings(
        _env_file=None,
        mode="stub",
        demo_user="offline-demo",
        demo_pass_hash=PasswordHash.recommended().hash(password),
        jwt_secret=secrets.token_urlsafe(48),
        database_path=directory / "api.sqlite",
        upload_directory=directory / "uploads",
    )
    app = create_app(settings)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 40), "SYNTHETIC demo deck; not a real assessment.")
        pdf = doc.tobytes()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://offline"
        ) as client:
            login = await client.post(
                "/auth/login", json={"username": settings.demo_user, "password": password}
            )
            login.raise_for_status()
            client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
            upload = await client.post(
                "/runs",
                data={"company_name": "Synthetic demo", "max_check_usd": "0"},
                files={"deck": ("demo.pdf", pdf, "application/pdf")},
            )
            upload.raise_for_status()
            run = upload.json()["run_id"]
            async with asyncio.timeout(10):
                while True:
                    response = await client.get(f"/runs/{run}")
                    response.raise_for_status()
                    record = response.json()
                    if record["status"] in ("done", "failed", "cannot_assess"):
                        break
                    await asyncio.sleep(0.01)
            events = await client.get(f"/runs/{run}/events")
            events.raise_for_status()
            if (
                record["status"] != "done"
                or not record["decision"]["is_stub"]
                or "event: done" not in events.text
            ):
                raise RuntimeError("Offline API smoke did not complete as expected.")
    return {
        "mode": "OFFLINE SYNTHETIC; NOT A LIVE ASSESSMENT",
        "login_status": login.status_code,
        "upload_status": upload.status_code,
        "run_status": record["status"],
        "agent_phase_records": len(record["agents"]),
        "sse_terminal_event": True,
        "decision_is_stub": record["decision"]["is_stub"],
        "real_api_calls": 0,
    }


def main():
    with tempfile.TemporaryDirectory(prefix="leo-api-") as directory:
        print(json.dumps(asyncio.run(run_demo(Path(directory))), indent=2))


if __name__ == "__main__":
    main()

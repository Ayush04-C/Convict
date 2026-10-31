"""CLI calls the authenticated API and preserves saved scores."""

from types import SimpleNamespace
import httpx
import pytest

from scripts.leo_cli import CliError, checked, execute, parser, validate
from tests.pipeline_support import pdf_bytes
from tests.test_run_api import api, terminal, upload


async def test_cli_run_and_export_do_not_recompute_scores(auth_settings, tmp_path):
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(pdf_bytes())
    async with api(auth_settings, tmp_path) as (app, client):
        args = SimpleNamespace(
            command="run",
            username=auth_settings.demo_user,
            timeout=5,
            company="Synthetic",
            domain="example.com",
            max_check_usd=100000,
            stage="seed",
            deck=pdf,
        )
        from tests.conftest import TEST_PASSWORD

        result = await execute(args, client, password=TEST_PASSWORD)
        assert (
            result["verdict_label"] == "DO NOT INVEST" and result["decision"]["check_size_usd"] == 0
        )
        saved = await app.state.ledger.get_decision(result["run_id"])
        events = await app.state.ledger.get_events(result["run_id"])
        args.command, args.run_id = "report", result["run_id"]
        second = await execute(
            args, client, token=client.headers["Authorization"].removeprefix("Bearer ")
        )
        assert second == result and second["decision"] == saved.model_dump(mode="json")
        assert await app.state.ledger.get_events(result["run_id"]) == events


async def test_report_api_json_and_markdown_preserve_same_outcome(auth_settings, tmp_path):
    async with api(auth_settings, tmp_path) as (_, client):
        run = (await upload(client)).json()["run_id"]
        saved = (await terminal(client, run))["decision"]
        report = await client.get(f"/runs/{run}/report")
        assert report.status_code == 200 and report.json()["decision"] == saved
        assert report.headers["cache-control"] == "no-store"
        markdown = await client.get(f"/runs/{run}/report?format=markdown")
        assert markdown.status_code == 200 and "text/markdown" in markdown.headers["content-type"]
        assert "DO NOT INVEST" in markdown.text
        assert (await client.get(f"/runs/{run}/report?format=html")).status_code == 422


@pytest.mark.parametrize("status", [401, 404, 409, 500])
async def test_cli_errors_never_echo_response_secrets(status):
    with pytest.raises(CliError) as caught:
        await checked(httpx.Response(status, text="private-token"))
    assert str(status) in str(caught.value) and "private-token" not in str(caught.value)


@pytest.mark.parametrize("payload", [[], None, "invalid"])
async def test_cli_malformed_json_is_a_safe_error(payload):
    with pytest.raises(CliError):
        await checked(httpx.Response(200, json=payload))


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/report",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
    ],
)
def test_cli_rejects_credentials_and_non_origin_urls(url):
    args = parser().parse_args(["--base-url", url, "report", "run-id"])
    with pytest.raises(CliError):
        validate(args)


def test_cli_refuses_to_overwrite_reports(tmp_path):
    output = tmp_path / "report.md"
    output.write_text("Keep this")
    args = parser().parse_args(["--output", str(output), "report", "run-id"])
    with pytest.raises(CliError):
        validate(args)
    assert output.read_text() == "Keep this"


@pytest.mark.parametrize("timeout", ["0", "nan", "3601"])
def test_cli_timeout_is_bounded(timeout):
    args = parser().parse_args(["--timeout", timeout, "report", "run-id"])
    with pytest.raises(CliError):
        validate(args)

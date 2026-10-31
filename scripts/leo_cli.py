"""Authenticated CLI for PDF runs and stored reports. Scores are never recalculated here."""

import argparse
import asyncio
from getpass import getpass
import json
import math
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx

from report.markdown import render_markdown


class CliError(Exception):
    pass


async def checked(response):
    if response.is_error:
        raise CliError(f"API request failed (HTTP {response.status_code}).")
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError
        return result
    except ValueError:
        raise CliError("API returned invalid JSON.") from None


async def execute(args, client, *, token=None, password=None):
    if token:
        client.headers["Authorization"] = "Bearer " + token
    else:
        result = await checked(
            await client.post("/auth/login", json={"username": args.username, "password": password})
        )
        client.headers["Authorization"] = "Bearer " + result["access_token"]
    async with asyncio.timeout(args.timeout):
        if args.command == "run":
            data = {
                "company_name": args.company,
                "max_check_usd": str(args.max_check_usd),
                "stage": args.stage,
            }
            if args.domain:
                data["domain"] = args.domain
            with args.deck.open("rb") as deck:
                result = await checked(
                    await client.post(
                        "/runs",
                        data=data,
                        files={"deck": (args.deck.name, deck, "application/pdf")},
                    )
                )
            run_id = result["run_id"]
            while True:
                status = await checked(await client.get(f"/runs/{run_id}"))
                if status["status"] in ("done", "failed", "cannot_assess"):
                    break
                await asyncio.sleep(0.5)
        else:
            run_id = args.run_id
        return await checked(await client.get(f"/runs/{run_id}/report"))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:8000")
    result.add_argument("--username", default="ayush")
    result.add_argument("--timeout", type=float, default=600)
    result.add_argument("--format", choices=("json", "markdown"), default="markdown")
    result.add_argument(
        "--output",
        type=Path,
        help="Create a new private report file; existing files are not overwritten.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Upload a deck, wait for the run and display its report.")
    run.add_argument("--deck", type=Path, required=True)
    run.add_argument("--company", required=True)
    run.add_argument("--domain")
    run.add_argument("--max-check-usd", type=float, required=True)
    run.add_argument("--stage", default="unspecified")
    report = commands.add_parser(
        "report", help="Read an existing owned run without re-running providers."
    )
    report.add_argument("run_id")
    return result


def validate(args):
    url = urlsplit(args.base_url)
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise CliError("Use an HTTP(S) API origin without credentials, paths or query parameters.")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
        raise CliError("Timeout must be between zero and 3600 seconds.")
    if args.command == "run":
        if not args.deck.is_file() or args.deck.suffix.lower() != ".pdf":
            raise CliError("Provide an existing PDF deck.")
        if not 0 < args.deck.stat().st_size <= 20 * 1024 * 1024:
            raise CliError("Deck must be nonempty and at most 20 MB.")
        if not math.isfinite(args.max_check_usd) or args.max_check_usd < 0:
            raise CliError("Policy limit must be finite and nonnegative.")
    if args.output and args.output.exists():
        raise CliError("Output already exists; choose a new report filename.")


def main():
    args = parser().parse_args()
    try:
        validate(args)
        token = os.environ.get("LEO_ACCESS_TOKEN")
        password = None if token else getpass("Demo password (hidden): ")

        async def run():
            async with httpx.AsyncClient(
                base_url=args.base_url, timeout=30, follow_redirects=False
            ) as client:
                return await execute(args, client, token=token, password=password)

        report = asyncio.run(run())
        rendered = (
            json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            if args.format == "json"
            else render_markdown(report)
        )
        if args.output:
            with open(
                args.output,
                "x",
                encoding="utf-8",
                opener=lambda name, flags: os.open(name, flags, 0o600),
            ) as handle:
                handle.write(rendered)
        else:
            print(rendered, end="")
        return 1 if report["status"] == "failed" else 0
    except CliError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, httpx.HTTPError, TimeoutError, ValueError, KeyError):
        print(
            "CLI request failed. Check the API, authentication, input path, timeout and output filename.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

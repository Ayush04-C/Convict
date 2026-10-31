# Leo

Local pitch-deck diligence API, built by Ayush and Daksh.

The foundation includes demo-account JWT authentication, a persistent SQLite ledger,
restricted agent ledger access, the health endpoint, schemas, fixture-backed
specialists, offline demos, and deterministic scoring.
Provider clients, response caching and structured Gemini output are implemented.
Company resolution, isolated context packets and sandbox phase execution are implemented.
PDF extraction, persistent orchestration, authenticated upload, status and SSE are
implemented. Saved company reports are available as JSON/Markdown and through the
CLI. The real lead adjudicator remains Daksh's work; chat is deferred. Stub components
stay explicitly labelled. Reporting does not change scoring formulas or saved scores.

## Quick start

Python 3.11 or later is required. Run commands from this directory:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/init_env.py
python -m scripts.configure_auth
make test
make demo
make run
```

`make run` serves HTTP on localhost:8000; `/health` and `/docs` are available.
Configure your demo password once through the hidden prompt; repeat setup preserves
existing credentials and provider keys. API startup requires valid auth settings.
See [authentication](docs/authentication.md) for manual testing and expected responses.
`make demo` runs offline, needs no server or API key, and prints **stub** results.
No provider credit is spent by tests or demo.
Tests inject isolated credentials and do not require your `.env`.

API startup initializes `DATABASE_PATH` (default `data/leo.sqlite`). Use
`make ledger-init` to initialize it explicitly, or `make ledger-demo` for a separate
persistent stub demonstration. See [ledger](docs/ledger.md) for inspection commands,
phase rules and the handoff interface. No provider API key is needed for this stage.

Run `make provider-demo` for an offline provider/cache/ledger check. See
[providers](docs/providers.md) for live smoke commands, configuration and limits.
The default dependencies include Google GenAI and PyMuPDF so ingestion tests run
in CI when Daksh's branches are merged. `requirements-features.txt` adds optional ADK.

Run `make context-demo` for the offline resolution/context/sandbox handoff, or
`python -m scripts.context_demo --timeout-agent` to verify one failed agent and
three surviving workers. See [resolution/context](docs/resolution-context.md) and
[sandbox](docs/sandbox.md) for contracts, limits and tests. These demos use stub
specialists. Run `make api-demo` for the complete offline login/upload/pipeline/SSE
flow. See [pipeline/API](docs/pipeline-api.md) for Postman fields, live-mode setup,
error behavior and the single-process execution limit.

Use `python -m scripts.leo_cli --help` for authenticated CLI analysis/report export.
The CLI uses the same API and saved decision; it never recalculates a score.

See [contracts](docs/contracts.md), [ownership](docs/ownership.md),
[implementation stages](docs/ayush-stages.md), and [handoff](docs/handoff.md).
Original blueprints are under `docs/reference/`.

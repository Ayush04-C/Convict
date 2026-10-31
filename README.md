# Leo

Local pitch-deck diligence API, built by Ayush and Daksh.

This branch is the **foundation skeleton**. The health endpoint, schemas,
fixture-backed specialists, offline demo, and deterministic scoring are available.
Live extraction, provider access, persistent orchestration, authentication, upload,
SSE, report API and chat still require implementation. Planned endpoints return 501;
they must not be presented as protected or production-ready.

## Quick start

Python 3.11 or later is required. Run commands from this directory:

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/init_env.py
make test
make demo
make run
```

`make run` serves HTTP on localhost:8000; `/health` and `/docs` are available.
`make demo` runs offline, needs no server or API key, and prints **stub** results.
No provider credit is spent by tests or demo.

Daksh also installs `pip install -r requirements-features.txt` for the live SDKs.
SDK/model compatibility is a build-time check, not verified by the skeleton.

See [contracts](docs/contracts.md), [ownership](docs/ownership.md),
[implementation stages](docs/ayush-stages.md), [handoff](docs/handoff.md), and
[Git workflow](docs/git-workflow.md). Original blueprints are under `docs/reference/`.

# File ownership after handoff

Ayush creates all starting files. Then each person edits only their own paths.

| Owner | Paths |
|---|---|
| Ayush | `app/`, `ledger/`, `crustdata/`, `sandbox/`, `report/`, `frontend/` |
| Ayush | `leo/orchestrator.py`, `leo/entity_resolution.py`, `leo/context.py`, `leo/scoring.py` |
| Ayush | `agents/protocol.py`, `agents/base.py`, `agents/registry.py` |
| Ayush | `tests/fakes.py`, `tests/test_contracts.py`, `tests/test_scoring.py`, `tests/test_ledger.py`, `tests/test_sandbox.py`, `tests/test_api.py`, `tests/test_context.py`, `tests/test_crustdata.py`, `tests/test_entity_resolution.py`, `tests/test_orchestrator.py` |
| Daksh | `ingest/`, four concrete `agents/*.py` specialists, `agents/prompts.py`, `agents/analysts/`, `leo/adjudicator.py` |
| Daksh | `tests/test_math.py`, `tests/test_agents_checks.py`, `tests/test_ingest.py`, `tests/test_pdf_check.py`, `tests/test_adjudicator.py` |
| Ayush maintains; coordinate changes | `app/schemas.py`, `app/settings.py`, `agents/protocol.py`, `ledger/models.sql`, root configuration/dependencies, package initializers, baseline fixtures, docs and CI |

Shared parent folders do not mean shared file ownership. Request a change from its
owner instead of editing their file. Daksh adds new fixtures with distinct names.
Real Retool/Zepto responses are not included until captured; synthetic data is labeled.

Authentication additions are Ayush-owned: `scripts/configure_auth.py`,
`tests/conftest.py`, `tests/test_auth.py`, `tests/test_auth_settings.py`,
`tests/test_auth_setup.py`, and `docs/authentication.md`.

Ledger additions are Ayush-owned: `scripts/init_ledger.py`, `scripts/ledger_demo.py`,
`tests/ledger_support.py`, `tests/test_ledger_repository.py`,
`tests/test_ledger_phases.py`, `tests/test_ledger_writer.py`,
`tests/test_ledger_integration.py`, `docs/ledger.md` and `docs/ledger-pr.md`.

Provider additions are Ayush-owned: `tests/test_provider_cache.py`,
`tests/test_provider_settings.py`, `tests/test_provider_integration.py`,
`tests/test_llm.py`, `scripts/provider_demo.py`, `scripts/provider_smoke.py`,
`scripts/gemini_smoke.py`, `docs/providers.md` and `docs/providers-pr.md`.
Shared dependency/CI changes remain coordinated by Ayush.

Resolution/context and sandbox additions are Ayush-owned: `leo/resolution_rules.py`,
`sandbox/scoped_ledger.py`, `tests/test_resolution_integration.py`,
`tests/test_scoped_tools.py`, `tests/test_context_demo.py`, `scripts/context_demo.py`,
`docs/resolution-context.md` and `docs/sandbox.md`.

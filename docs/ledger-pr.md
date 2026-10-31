# feat(ledger): implement persistent SQLite evidence and agent access

Base: `main`

Head: `feat/ayush-ledger`

## Change

Replace ledger placeholders with run-scoped SQLite persistence for claims, evidence,
metrics, context, findings, debate, verdicts, decisions, execution status and events.
Each mutation and its progress event share one transaction. Add bounded phase waits,
versioned initialization, WAL connections, typed errors and rollback on cancellation.

Bind specialist writes and reads to agent identity and phase, preserve append-only
history, validate same-run references, enforce challenge limits and make final
decision persistence atomic with terminal state. Initialize the configured ledger
at API startup; retain unfinished run endpoints as explicit 501 responses.

Add a persisted offline stub demo and CI coverage. Keep offline checks independent
of Daksh's live replacement points. No Daksh-owned implementation files are changed,
and no live provider key or API credit is required.

## Validation

- **160 tests passed locally**, including all prior authentication/foundation tests.
- Ruff and both offline demos passed; configured SQLite initialization succeeded.
- The persistent demo completed with six findings, four verdicts, 43 events,
  explicit stub provenance and zero check sizing, then read its result after reopen.
- Integration tests cover real task cancellation, failed event commits, API startup,
  repeated persistent runs and independence from live feature replacement points.

Hosted CI is not claimed until this branch is pushed and its GitHub Actions run completes.

## Test cases created and passed

**160 passing cases total: 49 new ledger cases and 111 existing regression cases.**

The lists below include every collected test function. Counts include its
parameterized cases, so long synthetic input values are not duplicated here.

### New ledger tests — 49 cases

**`tests/test_ledger_integration.py` — 5 cases**

- `test_decision_and_terminal_state_rollback_when_event_fails` — 1 case
- `test_offline_demo_never_imports_daksh_replacement_points` — 1 case
- `test_persistent_stub_demo_survives_reopen_and_repeat` — 1 case
- `test_real_task_cancellation_releases_transaction` — 1 case
- `test_startup_initializes_configured_database_without_provider_calls` — 1 case

**`tests/test_ledger_phases.py` — 8 cases**

- `test_agent_status_is_current_phase_bound_and_terminal` — 1 case
- `test_cannot_assess_only_during_resolution` — 1 case
- `test_failure_wakes_waiters_and_cannot_reopen` — 1 case
- `test_invalid_wait_timeout_rejected` — 3 cases
- `test_phase_wait_returns_on_change_deadline_and_passed_target` — 1 case
- `test_phases_are_monotonic_and_idempotent` — 1 case

**`tests/test_ledger_repository.py` — 21 cases**

- `test_append_only_and_replace_cannot_erase_finding` — 1 case
- `test_bad_reference_in_second_finding_rolls_back_first` — 1 case
- `test_busy_timeout_is_bounded_and_sanitized` — 1 case
- `test_cancellation_rolls_back_transaction` — 1 case
- `test_claims_context_and_empty_claim_set_roundtrip` — 1 case
- `test_completed_decision_is_atomic_and_preserves_stub_provenance` — 1 case
- `test_cross_run_evidence_and_metrics_rejected` — 1 case
- `test_duplicate_evidence_batch_rolls_back_with_its_event` — 1 case
- `test_events_are_ordered_paginated_and_run_scoped` — 1 case
- `test_every_connection_enforces_constraints_and_initialization_is_additive` — 1 case
- `test_evidence_is_required_for_verified_findings` — 1 case
- `test_evidence_metrics_and_finding_roundtrip` — 1 case
- `test_future_schema_version_is_not_downgraded` — 1 case
- `test_invalid_json_is_rejected_without_writing` — 4 cases
- `test_missing_database_is_not_created` — 1 case
- `test_missing_run_and_missing_context` — 1 case
- `test_progress_event_failure_rolls_back_record` — 1 case
- `test_run_reopens_with_owner_policy_and_bound_parameters` — 1 case

**`tests/test_ledger_writer.py` — 15 cases**

- `test_challenge_limit_cannot_be_bypassed_by_multiple_batches` — 1 case
- `test_challenge_limit_is_atomic_under_concurrent_writes` — 1 case
- `test_challenges_reply_revision_and_verdict_are_persisted` — 1 case
- `test_finished_agent_cannot_write_late_output` — 1 case
- `test_four_agents_can_write_without_cross_run_leaks` — 1 case
- `test_invalid_challenges_are_rejected` — 5 cases
- `test_other_agents_evidence_is_private_until_barrier` — 1 case
- `test_replies_cannot_impersonate_target_or_set_inconsistent_severity` — 1 case
- `test_revisions_are_owned_and_adjudication_must_precede_scoring` — 1 case
- `test_round1_barrier_and_writer_ownership` — 1 case
- `test_writer_is_frozen_and_has_no_destructive_methods` — 1 case

### Existing regression tests — 111 cases

These remain part of the same full-suite CI run; no authentication or
foundation tests were removed to make the ledger pass.

**`tests/test_api.py` — 1 cases**

- `test_health_docs_and_explicit_unimplemented_api` — 1 case

**`tests/test_auth.py` — 54 cases**

- `test_bad_credentials_are_generic` — 4 cases
- `test_cannot_issue_token_for_unknown_user` — 1 case
- `test_every_run_route_requires_authentication` — 5 cases
- `test_invalid_login_body_does_not_echo_input` — 10 cases
- `test_invalid_signed_claims_rejected` — 14 cases
- `test_login_and_authenticated_identity` — 1 case
- `test_malformed_json_is_safe` — 1 case
- `test_missing_or_malformed_authorization` — 6 cases
- `test_missing_required_token_field` — 5 cases
- `test_openapi_exposes_bearer_auth_only_on_protected_routes` — 1 case
- `test_other_algorithms_rejected` — 2 cases
- `test_token_tampering_rejected` — 1 case
- `test_unexpected_errors_are_safe_and_correlated` — 1 case
- `test_unknown_username_still_verifies_password` — 1 case
- `test_wrong_signature_rejected` — 1 case

**`tests/test_auth_settings.py` — 10 cases**

- `test_api_auth_does_not_require_provider_credentials` — 1 case
- `test_empty_environment_is_rejected` — 1 case
- `test_invalid_startup_configuration_is_safe` — 8 cases

**`tests/test_auth_setup.py` — 11 cases**

- `test_cli_password_error_is_actionable_without_echoing_input` — 1 case
- `test_empty_username_uses_default` — 1 case
- `test_existing_credentials_are_never_rotated` — 1 case
- `test_failed_atomic_replace_preserves_original` — 1 case
- `test_interrupted_input_does_not_create_file` — 1 case
- `test_invalid_existing_auth_is_not_silently_overwritten` — 1 case
- `test_invalid_password_leaves_environment_unchanged` — 3 cases
- `test_new_configuration_round_trips_without_plaintext` — 1 case
- `test_provider_values_comments_and_existing_secret_are_preserved` — 1 case

**`tests/test_contracts.py` — 7 cases**

- `test_all_stubs_complete_offline` — 1 case
- `test_context_is_immutable` — 1 case
- `test_founder_claim_is_supported` — 1 case
- `test_invalid_agent_scores_rejected` — 4 cases

**`tests/test_ledger.py` — 1 cases**

- `test_schema_is_repeatable_and_history_is_append_only` — 1 case

**`tests/test_scoring.py` — 27 cases**

- `test_confirmed_gates_force_pass` — 5 cases
- `test_dimension_scores_produce_policy_tiers` — 5 cases
- `test_duplicate_agent_verdict_rejected` — 1 case
- `test_duplicate_primary_uses_least_credit` — 1 case
- `test_effective_revision_changes_severity_cap` — 1 case
- `test_empty_claim_set_does_not_divide_by_zero` — 1 case
- `test_exact_and_fractional_tier_boundaries` — 6 cases
- `test_failed_agent_reduces_confidence_and_keeps_weights` — 1 case
- `test_missing_data_does_not_override_confirmed_gate` — 1 case
- `test_no_verifiable_claims_has_no_sizing` — 1 case
- `test_primary_claim_finding_is_not_double_counted` — 1 case
- `test_profit_document_downgrade` — 1 case
- `test_severity_five_caps_invest` — 1 case
- `test_unknown_claim_reference_rejected` — 1 case

### Workflow coverage

On every push and PR, GitHub Actions installs the constrained dependencies,
runs Ruff, executes `python -m pytest` over all `tests/`, and runs both the
in-memory and temporary SQLite-backed offline demos. Future tests following
pytest discovery conventions are automatically included.

Real specialist/provider integration remains a later check; current ledger
tests use synthetic records and explicit stubs while exercising real SQLite.

## Limits

This is storage and restricted ledger access, not the live orchestrator. Provider
clients/cache, execution timeouts/tool budgets, job recovery, SSE and report APIs
remain later stages. Raw repository methods are privileged; API ownership checks
must precede run-data reads. Software capabilities are not process isolation.

Use a merge commit to retain the focused implementation history. Local .env/database
files and the pre-existing edit to docs/foundation-pr.md are excluded.

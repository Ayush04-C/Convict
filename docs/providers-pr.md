# PR: Implement Crustdata and Gemini provider infrastructure

## Result

Add validated Crustdata access, account-scoped SQLite caching, timestamped dossiers
and structured Gemini output behind the existing interfaces. Live application
startup owns and closes shared provider clients. Analysis/run routes remain
placeholders until the later pipeline/API stages.

## Changes

- Fixed provider host/version, validated inputs and response envelopes, safe typed errors.
- Bounded retries/deadlines, rate-limit backoff and concurrent request coalescing.
- Cache TTLs, credential/parameter isolation, original retrieval dates and run evidence.
- Gemini schema validation, one repair attempt and explicit configured model selection.
- Default Google GenAI/PyMuPDF dependencies and full-suite CI with offline provider demo.
- Opt-in live smoke scripts and provider/handoff documentation.

## Validation

- Full local suite: **251 passed**, no skipped tests.
- Ruff, dependency consistency and offline demos checked.
- Live Crustdata: permissions, Retool identification/enrichment and cached repeat passed.
- Live Gemini FAST `gemini-3.1-flash-lite`: valid structured output, one request.
- Live MID/PRO generation and complete agent orchestration were not tested.
- CI runs the whole suite on pushes and PRs, with no provider keys or live requests.

## Provider test inventory

Each function below is executable coverage; parametrized functions cover multiple
cases. Existing authentication, ledger, contract and scoring tests continue to run.
The provider group contains 91 cases, adding coverage to the previous
160-case suite.

### tests/test_crustdata.py

- `test_identify_headers_body_and_candidate_preservation`
- `test_bad_identifiers_make_no_requests`
- `test_nonretryable_errors_are_sanitized`
- `test_403_credit_error_is_classified_without_leaking_body`
- `test_transient_failure_retries_then_succeeds`
- `test_retry_exhaustion_is_four_total_attempts`
- `test_rate_limit_delay_is_respected`
- `test_retry_after_beyond_deadline_fails_without_sleep`
- `test_transport_error_policy`
- `test_total_timeout_bounds_slow_transport`
- `test_bad_success_shape_is_rejected`
- `test_invalid_json_is_not_cached`
- `test_concurrent_identical_enrichment_hits_network_once`
- `test_cache_separates_credentials_and_field_sets`
- `test_dossier_cache_timestamps_nulls_identity_and_ledger_evidence`
- `test_dossier_never_selects_wrong_or_ambiguous_company`
- `test_jobs_web_and_account_requests`
- `test_failed_fetch_items_are_not_cached`
- `test_closed_client_rejects_calls`

### tests/test_provider_cache.py

- `test_cache_miss_then_hit_retains_date_and_copies_data`
- `test_cache_expiry_is_exact_and_entry_can_refresh`
- `test_keys_separate_parameters_versions_endpoint_and_account`
- `test_corrupt_cache_entries_are_misses`
- `test_future_fetch_time_is_not_treated_as_fresh`
- `test_nonfinite_payload_and_unknown_endpoint_are_rejected`

### tests/test_llm.py

- `test_structured_output_and_explicit_model`
- `test_already_parsed_model_is_validated`
- `test_invalid_output_retries_once_without_echoing_content`
- `test_sdk_validation_exception_also_retries`
- `test_twice_invalid_output_returns_typed_error`
- `test_api_errors_are_typed_and_not_retried`
- `test_blocked_empty_output_is_not_silently_successful`
- `test_timeout_is_global_and_cancellation_propagates`
- `test_invalid_input_model_and_closed_client_never_call_sdk`
- `test_missing_key_is_an_actionable_configuration_error`

### tests/test_provider_settings.py

- `test_individual_provider_can_be_configured_without_other_credentials`
- `test_full_configuration_requires_keys_and_models`
- `test_invalid_provider_limits_are_rejected`

### tests/test_provider_integration.py

- `test_bad_endpoint_arguments_are_rejected_before_network`
- `test_missing_cache_database_fails_before_spending_credits`
- `test_cancelled_waiter_does_not_leak_request_locks`
- `test_provider_demo_is_offline_and_persists_evidence`
- `test_live_lifespan_owns_and_closes_both_clients`
- `test_partial_lifespan_failure_closes_already_created_client`

## Scope

No Daksh-owned source files or real credentials are included. See
`docs/providers.md` for manual smoke commands. Submission work remains deferred.

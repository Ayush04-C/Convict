"""Public failure categories; raw exception strings never enter reports or the ledger."""

import re

from ingest.claim_extractor import ProviderUnavailableError


def extraction_failure(error):
    raw = str(error)
    status = None
    category, message = (
        "extraction_error",
        "PDF claim extraction did not complete. No investment decision was produced.",
    )
    rejected = re.match(r"^(Gemini|BharatCode) rejected the request \((401|403|404)\)", raw)
    busy = re.match(r"^(Gemini|BharatCode) is unavailable or busy \((429|5\d\d)\)", raw)
    if rejected:
        provider, status = rejected[1], int(rejected[2])
        category, message = (
            "configuration",
            f"{provider} rejected extraction (HTTP {status}). Check the configured key, model access and account plan.",
        )
    elif busy:
        status = int(busy[2])
        category = "rate_limit" if status == 429 else "provider_unavailable"
        message = f"{busy[1]} could not process extraction (HTTP {status}). Try again shortly."
    elif raw.startswith(("Gemini did not answer in time;", "BharatCode did not answer in time;")):
        category, message = (
            "timeout",
            "The extraction provider did not answer before its deadline. Try again shortly.",
        )
    elif raw.startswith("The model returned invalid claims twice"):
        category, message = (
            "invalid_response",
            "The extraction model returned invalid or empty claims after two attempts.",
        )
    elif isinstance(error, ProviderUnavailableError):
        category, message = (
            "provider_unavailable",
            "The extraction provider is unavailable, busy, or unreachable. Try again shortly.",
        )
    return {
        "stage": "claim_extraction",
        "category": category,
        "message": message,
        "provider_status": status,
    }

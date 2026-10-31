"""Explicit live Crustdata check. No live calls without --live; never prints keys."""

import argparse
import asyncio
import json

from app.settings import ProviderConfigurationError, Settings
from crustdata.client import CrustdataClient, CrustdataError, company_id, normalize_domain
from crustdata.dossier import build_dossier
from ledger.db import initialize
from ledger.errors import LedgerError


async def check(settings: Settings, domain: str):
    domain = normalize_domain(domain)
    settings.require_live_credentials("crustdata")
    await initialize(settings.database_path)
    # A diagnostic must not loop on billable provider failures.
    settings = settings.model_copy(update={"provider_max_retries": 0})
    async with CrustdataClient(settings, cache_path=settings.database_path) as client:
        permissions = await client.account_permissions()
        endpoints = (
            permissions.get("endpoints", []) if isinstance(permissions, dict) else permissions
        )
        enabled = {e.get("path"): e.get("status") for e in endpoints if isinstance(e, dict)}
        if enabled.get("/company/enrich") == "disabled":
            raise CrustdataError(
                "permission", 403, "Company enrichment is disabled for this account."
            )
        candidates = await client.identify(domains=[domain])
        matches = [
            m["company_data"]
            for row in candidates
            for m in row["matches"]
            if m["confidence_score"] >= 0.9
            and (m["company_data"].get("basic_info") or {}).get("primary_domain") == domain
        ]
        if len(matches) != 1 or company_id(matches[0]) is None:
            raise CrustdataError(
                "response", 200, "Smoke company did not resolve to one exact domain match."
            )
        identifier = company_id(matches[0])
        first = await build_dossier(identifier, client)
        count = client.request_count
        second = await build_dossier(identifier, client)
        if first != second or client.request_count != count:
            raise RuntimeError("Repeated enrichment did not reuse its cached response.")
        return {
            "mode": "LIVE CRUSTDATA",
            "domain": domain,
            "company_id": identifier,
            "permissions": {
                p: enabled.get(p, "not listed")
                for p in (
                    "/company/identify",
                    "/company/enrich",
                    "/job/search",
                    "/web/search/live",
                    "/web/enrich/live",
                )
            },
            "http_requests": count,
            "repeat_enrichment_http_requests": client.request_count - count,
            "fetched_at": first.fetched_at,
            "source_updated_at": first.source_updated_at,
            "gemini": "not called; mock-tested separately",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Authorize the live, potentially billable diagnostic"
    )
    parser.add_argument("--domain", default="retool.com")
    args = parser.parse_args()
    if not args.live:
        print("No requests made. Use --live for permissions, Identify and one cached Enrich check.")
        return
    try:
        result = asyncio.run(check(Settings(), args.domain))
    except (CrustdataError, ProviderConfigurationError, LedgerError) as error:
        raise SystemExit(str(error)) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

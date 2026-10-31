"""Bounded identity gate; web discovery must be confirmed by provider identity data."""

import asyncio
import math
from urllib.parse import urlsplit

from app.schemas import ClaimSet, Resolution
from crustdata.cache import json_text
from crustdata.client import CrustdataError, normalize_domain
from leo.resolution_rules import candidates, choose, domain_or_none, name_matches

SEARCH_DOMAIN_LIMIT = 3


async def resolve_company(
    name: str, domain: str | None, claims: ClaimSet, client, *, timeout: float = 60
) -> Resolution:
    if not isinstance(name, str) or not name.strip() or len(name) > 500:
        raise ValueError("A company name of 1–500 characters is required.")
    if not math.isfinite(timeout) or not 0 < timeout <= 180:
        raise ValueError("Resolution timeout must be positive and at most 180 seconds.")
    domain = domain_or_none(normalize_domain(domain)) if domain is not None else None
    try:
        async with asyncio.timeout(timeout):
            response = (
                await client.identify(domains=[domain])
                if domain
                else await client.identify(names=[name.strip()])
            )
            items = candidates(response)
            selected, summary = choose(items, claims, domain)
            if selected:
                return Resolution(
                    status="resolved",
                    company_id=selected.id,
                    reason="Company identity resolved from provider candidates and deck attributes.",
                    candidates_json=json_text(summary),
                )
            if items and items[0].confidence >= 0.5:
                return Resolution(
                    status="cannot_assess",
                    reason="Candidates remain ambiguous or conflict with the supplied identity.",
                    candidates_json=json_text(summary),
                )
            search = await client.web_search(query=name.strip() + " company", sources=("web",))
            domains = []
            for result in search["results"]:
                url = result.get("url")
                if not isinstance(url, str):
                    continue
                try:
                    parsed = urlsplit(url)
                except ValueError:
                    continue
                if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
                    continue
                found = domain_or_none(parsed.hostname)
                if found and found not in domains and (domain is None or found == domain):
                    domains.append(found)
                if len(domains) == SEARCH_DOMAIN_LIMIT:
                    break
            if domains:
                fallback = candidates(await client.identify(domains=domains))
                fallback = tuple(
                    c for c in fallback if c.domains.intersection(domains) and name_matches(name, c)
                )
                selected, extra = choose(fallback, claims, domain, high_only=True)
                summary.extend(extra)
                if selected:
                    return Resolution(
                        status="resolved",
                        company_id=selected.id,
                        reason="Web-discovered domain confirmed by a clear high-confidence provider match.",
                        candidates_json=json_text(summary),
                    )
            return Resolution(
                status="cannot_assess",
                reason="No company identity could be confirmed within the bounded fallback.",
                candidates_json=json_text(summary),
            )
    except TimeoutError:
        raise CrustdataError("timeout", None, "Company resolution exceeded its deadline.") from None

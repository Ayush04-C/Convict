"""Pure identity comparisons. Missing or contradictory deck attributes give no credit."""

from dataclasses import dataclass
import re

from rapidfuzz.fuzz import ratio

from app.schemas import ClaimSet
from crustdata.cache import json_text
from crustdata.client import CrustdataError, company_id, normalize_domain


def object_group(data, group):
    value = data.get(group)
    return value if isinstance(value, dict) else {}


def normalized(value):
    return " ".join(re.findall(r"\w+", str(value).casefold()))


def domain_or_none(value):
    try:
        return normalize_domain(value).removeprefix("www.")
    except (ValueError, TypeError):
        return None


def name_matches(name, candidate):
    def clean(value):
        return " ".join(
            word
            for word in normalized(value).split()
            if word
            not in {
                "inc",
                "incorporated",
                "ltd",
                "limited",
                "private",
                "pvt",
                "llc",
                "corp",
                "corporation",
            }
        )

    actual = object_group(candidate.data, "basic_info").get("name")
    return isinstance(actual, str) and bool(clean(name)) and ratio(clean(name), clean(actual)) >= 90


@dataclass(frozen=True)
class Candidate:
    id: int
    confidence: float
    data: dict

    @property
    def domains(self):
        basic = object_group(self.data, "basic_info")
        values = basic.get("all_domains")
        values = values if isinstance(values, list) else []
        return frozenset(
            filter(None, (domain_or_none(v) for v in [basic.get("primary_domain"), *values]))
        )


def candidates(response):
    """Deduplicate IDs deterministically, without combining conflicting provider records."""
    result = {}
    if not isinstance(response, list):
        raise CrustdataError("response", 200, "Invalid identity candidate response.")
    for item in response:
        if not isinstance(item, dict) or not isinstance(item.get("matches"), list):
            raise CrustdataError("response", 200, "Invalid identity candidate response.")
        for match in item["matches"]:
            data = match.get("company_data") if isinstance(match, dict) else None
            score = match.get("confidence_score") if isinstance(match, dict) else None
            if not isinstance(data, dict) or type(score) not in (float, int) or not 0 <= score <= 1:
                raise CrustdataError("response", 200, "Invalid identity candidate response.")
            ident = company_id(data)
            if ident is None:
                continue
            candidate = Candidate(ident, float(score), data)
            old = result.get(ident)
            if old is None or (candidate.confidence, json_text(data)) > (
                old.confidence,
                json_text(old.data),
            ):
                result[ident] = candidate
    return tuple(sorted(result.values(), key=lambda c: (-c.confidence, c.id)))


def deck_attributes(claims: ClaimSet):
    attributes = {"year": set(), "hq": set(), "employees": set()}
    for claim in claims.claims:
        value = claim.value
        if claim.topic == "identity_founding":
            years = re.findall(
                r"\b(?:18|19|20)\d{2}\b", str(value if value is not None else claim.text)
            )
            attributes["year"].update(int(y) for y in years)
        elif claim.topic == "identity_hq":
            text = value if isinstance(value, str) else claim.text
            text = re.sub(
                r"^(?:headquartered|headquarters|hq|based)\s*(?:in|at|:)?\s*", "", text, flags=re.I
            )
            if text.strip():
                attributes["hq"].add(normalized(text))
        elif claim.topic == "team_size" and type(value) in (int, float) and value >= 0:
            attributes["employees"].add(value)
    return {key: next(iter(values)) for key, values in attributes.items() if len(values) == 1}


COUNTRIES = {
    "usa": "united states",
    "us": "united states",
    "united states of america": "united states",
    "ind": "india",
    "uk": "united kingdom",
    "gbr": "united kingdom",
}


def matching_attributes(candidate: Candidate, attributes: dict, domain: str | None):
    basic, location = (
        object_group(candidate.data, "basic_info"),
        object_group(candidate.data, "locations"),
    )
    matches = []
    if domain and domain in candidate.domains:
        matches.append("domain")
    if "year" in attributes and str(basic.get("year_founded")) == str(attributes["year"]):
        matches.append("founding_year")
    if "hq" in attributes:
        expected = attributes["hq"]
        country = normalized(location.get("country") or "")
        country = COUNTRIES.get(country, country)
        city = normalized(location.get("city") or "")
        hq = normalized(location.get("headquarters") or "")
        if (country and re.search(r"\b" + re.escape(country) + r"\b", expected)) or expected in (
            city,
            hq,
        ):
            matches.append("headquarters")
    if "employees" in attributes:
        raw = basic.get("employee_count_range")
        bounds = (
            re.fullmatch(r"\s*([\d,]+)\s*[-–]\s*([\d,]+)\s*", raw) if isinstance(raw, str) else None
        )
        if bounds:
            low, high = (int(v.replace(",", "")) for v in bounds.groups())
            if low <= attributes["employees"] <= high:
                matches.append("employee_range")
    return tuple(matches)


def choose(items, claims, domain, *, high_only=False):
    attributes = deck_attributes(claims)
    summaries = [
        {
            "company_id": c.id,
            "confidence": c.confidence,
            "name": object_group(c.data, "basic_info").get("name"),
            "domains": sorted(c.domains),
            "matching_attributes": matching_attributes(c, attributes, domain),
            "domain_conflict": bool(domain and c.domains and domain not in c.domains),
        }
        for c in items
    ]
    eligible = [
        (c, s) for c, s in zip(items, summaries) if c.confidence >= 0.5 and not s["domain_conflict"]
    ]
    if not eligible:
        return None, summaries
    top_score = max(c.confidence for c, _ in eligible)
    top = [(c, s) for c, s in eligible if c.confidence == top_score]
    if top_score >= 0.9 and len(top) == 1:
        c, _ = top[0]
        basic = object_group(c.data, "basic_info")
        if (
            "year" not in attributes
            or basic.get("year_founded") is None
            or str(basic["year_founded"]) == str(attributes["year"])
        ):
            return c, summaries
    if high_only:
        return None, summaries
    ranked = sorted(eligible, key=lambda pair: (-len(pair[1]["matching_attributes"]), pair[0].id))
    first, summary = ranked[0]
    runner_up = len(ranked[1][1]["matching_attributes"]) if len(ranked) > 1 else 0
    if len(summary["matching_attributes"]) - runner_up >= 2:
        return first, summaries
    return None, summaries

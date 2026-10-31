"""Daksh: normalize extracted claims (numbers, units, dates, configured INR conversion).

Pure and offline. INR is converted to USD only with an explicitly configured rate
(INGEST_INR_PER_USD or the inr_per_usd argument). Without one the claim stays in INR,
so later checks report it UNVERIFIABLE instead of comparing mismatched currencies.
Other currencies have no approved rate and are never converted.
"""
import os
import re

from app.schemas import Claim, ClaimSet

MULTIPLIERS = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7,
}
CURRENCIES = {
    "$": "USD", "usd": "USD", "us$": "USD", "dollar": "USD", "dollars": "USD",
    "₹": "INR", "inr": "INR", "rs": "INR", "rs.": "INR", "rupee": "INR", "rupees": "INR",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}
UNITS = {
    "%": "percent", "pct": "percent", "percent": "percent", "percentage": "percent",
    "x": "multiple", "multiple": "multiple",
    "count": "count", "users": "count", "user": "count", "employees": "count",
    "employee": "count", "people": "count", "headcount": "count", "customers": "count",
    "customer": "count", "fte": "count",
    "year": "year", "years": "year", "yr": "year",
}
PERIODS = {
    "yoy": "YoY", "y/y": "YoY", "year over year": "YoY", "year-over-year": "YoY",
    "mom": "MoM", "m/m": "MoM", "month over month": "MoM", "month-over-month": "MoM",
    "qoq": "QoQ", "q/q": "QoQ", "quarter over quarter": "QoQ", "quarter-over-quarter": "QoQ",
    "cagr": "CAGR",
}
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_NUMBER = re.compile(r"^\s*([^\d\-.]*?)\s*(-?[\d,]*\.?\d+)\s*(.*?)\s*$")


def canonical_unit(unit: str | None) -> str | None:
    if unit is None or not unit.strip():
        return None
    key = unit.strip().lower()
    return CURRENCIES.get(key) or UNITS.get(key) or unit.strip()


def parse_amount(raw: str) -> tuple[float, str | None] | None:
    """Parse '$10M', '₹5 crore', '2,50,000', '50%', '3x' into (number, unit hint)."""
    match = _NUMBER.match(raw)
    if not match:
        return None
    prefix, digits, suffix = match.groups()
    try:
        number = float(digits.replace(",", ""))
    except ValueError:
        return None
    unit = CURRENCIES.get(prefix.strip().lower()) if prefix.strip() else None
    if unit is None and prefix.strip():
        return None  # unknown leading text such as "approx": do not guess
    for token in suffix.replace("+", " ").split():
        low = token.lower()
        if low in MULTIPLIERS:
            number *= MULTIPLIERS[low]
        elif low in CURRENCIES:
            unit = unit or CURRENCIES[low]
        elif low in UNITS:
            unit = unit or UNITS[low]
    return number, unit


def normalize_as_of(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", text):
        return text
    quarter = re.fullmatch(r"[Qq]([1-4])\s*['’]?\s*(?:FY)?(\d{2}|\d{4})", text)
    if quarter:
        year = int(quarter.group(2))
        return f"{year + 2000 if year < 100 else year}-Q{quarter.group(1)}"
    month = re.fullmatch(r"([A-Za-z]{3,9})\.?\s*['’,]?\s*(\d{2}|\d{4})", text)
    if month and month.group(1)[:3].lower() in MONTHS:
        year = int(month.group(2))
        return f"{year + 2000 if year < 100 else year}-{MONTHS[month.group(1)[:3].lower()]:02d}"
    slash = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
    if slash and 1 <= int(slash.group(1)) <= 12:
        return f"{slash.group(2)}-{int(slash.group(1)):02d}"
    return text


def normalize_period(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    return PERIODS.get(text.lower(), text)


def _configured_rate() -> float | None:
    raw = os.environ.get("INGEST_INR_PER_USD", "").strip()
    try:
        rate = float(raw)
    except ValueError:
        return None
    return rate if rate > 0 else None


def _normalize_claim(claim: Claim, inr_per_usd: float | None) -> Claim:
    value, unit = claim.value, canonical_unit(claim.unit)
    if isinstance(value, str):
        parsed = parse_amount(value)
        if parsed is not None:
            value, hint = parsed
            unit = unit or hint
    if isinstance(value, (int, float)) and unit == "INR" and inr_per_usd:
        value, unit = value / inr_per_usd, "USD"
    return claim.model_copy(update={
        "value": value, "unit": unit, "as_of": normalize_as_of(claim.as_of),
        "period": normalize_period(claim.period)})


async def normalize_claims(claims: ClaimSet, *, inr_per_usd: float | None = None) -> ClaimSet:
    rate = inr_per_usd if inr_per_usd and inr_per_usd > 0 else _configured_rate()
    return claims.model_copy(update={
        "claims": tuple(_normalize_claim(c, rate) for c in claims.claims)})

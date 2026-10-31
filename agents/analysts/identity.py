"""Daksh: null-safe pure-Python identity and integrity checks.

`data` is the company_data-shaped dict; this module needs `basic_info`, `locations`, `people`
and `news`. Quirks seen in real Crustdata responses and handled here:
  - locations.country is a code ("USA", "IND") while headquarters is free text; some companies
    only have a country-level HQ ("India, India").
  - people.founders can hold executive assistants and "Founder's office" staff (one company had
    21 entries), so a person counts as a founder only by title.
  - news titles are external text. Notes quote them shortened and labelled as data; downstream
    prompts must treat them as data, never as instructions.
"""
import re
from datetime import date, timedelta

from rapidfuzz import fuzz

from agents.analysts.common import CheckResult, MetricResult, get, number, unverifiable
from app.schemas import Claim

YEAR_NEAR = 1                 # one year apart is PARTIAL (incorporation vs launch)
YEAR_FAR = 5                  # five or more years apart is the strongest contradiction
NAME_MATCH_SCORE = 85
PLACE_MATCH_SCORE = 88
NEWS_WINDOW_DAYS = 180
NEWS_TITLE_CHARS = 100
NEWS_EXAMPLES = 3

ACTIVE_STATUSES = {"active", "operating"}
GONE_STATUSES = {"closed", "inactive", "dissolved", "defunct", "out of business", "bankrupt", "shut down"}
MERGED_STATUSES = {"acquired", "merged"}

SEVERE_NEWS = (r"fraud|scam|ponzi|bankrupt(?:cy)?|insolven(?:t|cy)|arrest(?:ed|s)?|money laundering|"
               r"shut(?:s|ting)? down|shutdown|defaulted?")
ADVERSE_NEWS = (SEVERE_NEWS + r"|lawsuits?|sued|suing|layoffs?|laid off|raid(?:s|ed)?|suspend(?:s|ed)?|"
                r"probe[ds]?|investigat(?:ion|ing|ed)|recall(?:s|ed)?|fined?|penalt(?:y|ies)|"
                r"data breach|breach(?:ed)?|lapses?|violations?|banned")
SEVERE_RE = re.compile(rf"\b(?:{SEVERE_NEWS})\b", re.I)
ADVERSE_RE = re.compile(rf"\b(?:{ADVERSE_NEWS})\b", re.I)

COUNTRY_ALIASES = {
    "usa": "united states", "us": "united states", "u.s.": "united states", "u.s": "united states",
    "united states of america": "united states", "america": "united states",
    "uk": "united kingdom", "gbr": "united kingdom", "gb": "united kingdom",
    "ind": "india", "uae": "united arab emirates", "are": "united arab emirates",
    "sgp": "singapore", "sg": "singapore", "can": "canada", "deu": "germany", "fra": "france",
    "aus": "australia",
}
# Two-letter country codes like CA, IN, DE are left out on purpose: in an address "CA" is
# California, not Canada. US states are expanded instead.
STATE_ALIASES = {
    "ca": "california", "ny": "new york", "tx": "texas", "wa": "washington", "ma": "massachusetts",
    "il": "illinois", "co": "colorado", "fl": "florida", "ga": "georgia", "va": "virginia",
    "nc": "north carolina", "pa": "pennsylvania",
}
CITY_ALIASES = {
    "bangalore": "bengaluru", "bombay": "mumbai", "gurgaon": "gurugram", "new delhi": "delhi",
    "calcutta": "kolkata", "madras": "chennai", "nyc": "new york", "sf": "san francisco",
}


def _paths(*paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(".".join(p) for p in paths)


def check_founding_year(claim: Claim | None, data: dict) -> CheckResult:
    name = "founding_year"
    evidence = _paths(("basic_info", "year_founded"))
    claimed = number(claim.value) if claim is not None else None
    if claimed is None or not 1800 <= claimed <= 2100:
        return unverifiable(name, claim, "Claim has no recognizable founding year.", evidence)
    actual = number(get(data, "basic_info", "year_founded"))
    if actual is None:
        return unverifiable(name, claim, "Crustdata has no founding year.", evidence)
    gap = abs(claimed - actual)
    metric = MetricResult("founding_year_gap", gap, "years", "|claimed year - basic_info.year_founded|")

    def result(status, severity, note):
        return CheckResult(name, status, severity, note, claim.id, evidence, (metric,))

    if gap == 0:
        return result("VERIFIED", 1, f"Founded {actual:.0f}, as claimed.")
    if gap <= YEAR_NEAR:
        return result("PARTIAL", 1, f"Deck says {claimed:.0f}, Crustdata says {actual:.0f}; "
                                    "incorporation and launch years often differ by one.")
    return result("CONTRADICTED", 4 if gap >= YEAR_FAR else 3,
                  f"Deck says founded {claimed:.0f}; Crustdata says {actual:.0f}.")


def _norm_place(text: str) -> str:
    low = re.sub(r"\s+", " ", text.lower().strip(" .,"))
    return CITY_ALIASES.get(low) or COUNTRY_ALIASES.get(low) or STATE_ALIASES.get(low) or low


def _claimed_places(claim: Claim) -> list[str]:
    raw = claim.value if isinstance(claim.value, str) else claim.text
    raw = re.sub(r"^\s*(?:we are |we're )?(?:headquartered|based|located|hq|offices?)\s*(?:in|at|:)?\s*", "",
                 raw.strip().rstrip("."), flags=re.I)
    return [_norm_place(p) for p in re.split(r",|;|\band\b|/", raw) if p.strip()]


def _graph_places(data: dict) -> tuple[set[str], set[str], bool]:
    """(headquarters places, office places, graph_has_city_level_hq)."""
    hq_parts = [_norm_place(p) for p in str(get(data, "locations", "headquarters") or "").split(",") if p.strip()]
    for key in ("city", "state", "country"):
        value = get(data, "locations", key)
        if isinstance(value, str) and value.strip():
            hq_parts.append(_norm_place(value))
    hq = set(hq_parts)
    offices: set[str] = set()
    for address in get(data, "locations", "all_office_addresses") or []:
        if isinstance(address, str):
            offices |= {_norm_place(p) for p in address.split(",") if p.strip()}
    country = _norm_place(str(get(data, "locations", "country") or ""))
    state = _norm_place(str(get(data, "locations", "state") or ""))
    city = _norm_place(str(get(data, "locations", "city") or ""))
    return hq, offices, bool(city) and city not in {country, state}


def _place_matches(place: str, pool: set[str]) -> bool:
    return any(place == p or fuzz.ratio(place, p) >= PLACE_MATCH_SCORE for p in pool)


def check_hq(claim: Claim | None, data: dict) -> CheckResult:
    name = "headquarters"
    evidence = _paths(("locations", "headquarters"), ("locations", "city"), ("locations", "country"),
                      ("locations", "all_office_addresses"))
    if claim is None:
        return unverifiable(name, claim, "No headquarters claim to check.", evidence)
    claimed = _claimed_places(claim)
    hq, offices, city_level = _graph_places(data)
    if not claimed:
        return unverifiable(name, claim, "No location could be read from the claim.", evidence)
    if not hq:
        return unverifiable(name, claim, "Crustdata has no headquarters location.", evidence)
    hq_hits = [p for p in claimed if _place_matches(p, hq)]
    office_hits = [p for p in claimed if p not in hq_hits and _place_matches(p, offices)]
    shown = str(get(data, "locations", "headquarters") or "an unknown location")
    metric = MetricResult("hq_places_matched", float(len(hq_hits)), "places", "claimed places found in HQ data")

    def result(status, severity, note):
        return CheckResult(name, status, severity, note, claim.id, evidence, (metric,))

    if len(hq_hits) == len(claimed):
        return result("VERIFIED", 1, f"Headquarters {shown} matches the claim.")
    if hq_hits and not city_level:
        return result("PARTIAL", 1, f"Country matches but Crustdata only has a country-level HQ ({shown}).")
    if office_hits and len(hq_hits) + len(office_hits) == len(claimed):
        return result("PARTIAL", 1, f"{', '.join(office_hits)} is an office, not the HQ ({shown}).")
    if hq_hits or office_hits:
        return result("PARTIAL", 2, f"Only part of the claimed location matches Crustdata's HQ ({shown}).")
    return result("CONTRADICTED", 3, f"Claimed location does not match Crustdata's HQ ({shown}).")


_DOMAIN_RE = re.compile(r"(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.I)


def check_status_and_domain(claim: Claim | None, data: dict) -> CheckResult:
    name = "status_and_domain"
    evidence = _paths(("basic_info", "status"), ("basic_info", "all_domains"), ("basic_info", "website"))
    status = get(data, "basic_info", "status")
    if not isinstance(status, str) or not status.strip():
        return unverifiable(name, claim, "Crustdata has no company status.", evidence)
    low = status.strip().lower()
    claim_id = claim.id if claim else None
    domains = {str(d).lower().removeprefix("www.") for d in (get(data, "basic_info", "all_domains") or [])}
    claimed_domains = {m.lower() for m in _DOMAIN_RE.findall(claim.text)} if claim else set()
    if claimed_domains and domains and not claimed_domains & domains:
        return CheckResult(name, "CONTRADICTED", 3, f"Claimed domain {', '.join(sorted(claimed_domains))} is "
                           f"not among Crustdata's domains ({', '.join(sorted(domains))}).", claim_id, evidence)
    if low in ACTIVE_STATUSES:
        return CheckResult(name, "VERIFIED", 1, f"Company status is {status}.", claim_id, evidence)
    if low in GONE_STATUSES:
        return CheckResult(name, "CONTRADICTED", 5, f"Crustdata lists the company as {status}.", claim_id,
                           evidence, gate="entity_not_active")
    if low in MERGED_STATUSES:
        return CheckResult(name, "PARTIAL", 3, f"Crustdata lists the company as {status}; confirm who "
                           "you are dealing with.", claim_id, evidence)
    return unverifiable(name, claim, f"Unrecognized company status {status!r}.", evidence)


_FOUNDER_LEAD = re.compile(r"^\s*(?:co-?)?founded\s+by\s+|^\s*(?:co-?)?founders?\s*(?:are|:)?\s+|"
                           r"^\s*led\s+by\s+|^\s*our\s+team\s*:?\s+", re.I)
_NOT_FOUNDER = re.compile(r"assistant|office|staff|intern|advis|partner|manager", re.I)


def claimed_founders(claim: Claim) -> list[str]:
    raw = claim.value if isinstance(claim.value, str) else claim.text
    raw = _FOUNDER_LEAD.sub("", raw.strip().rstrip("."))
    parts = [p.strip(" .") for p in re.split(r",|;|&|\band\b", raw)]
    return [p for p in parts if len(p) >= 4 and len(p.split()) <= 4]


def _person_name(person: object) -> str | None:
    name = get(person, "basic_profile", "name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _title(person: object) -> str:
    return str(get(person, "basic_profile", "current_title") or get(person, "basic_profile", "headline") or "")


_INITIAL = re.compile(r"^(.*\S)\s+([A-Za-z])\.?$")


def _name_hit(candidate: str, names: list[str]) -> bool:
    """Fuzzy full-name match. Abbreviated graph names ("Anthony G.") never count here."""
    scorer = fuzz.token_set_ratio if len(candidate.split()) == 1 else fuzz.token_sort_ratio
    return any(scorer(candidate.lower(), n.lower()) >= NAME_MATCH_SCORE
               for n in names if not _INITIAL.match(n))


def _initial_hit(candidate: str, names: list[str]) -> str | None:
    """The abbreviated graph name this candidate could be ("Anthony G." for "Anthony Gao")."""
    parts = candidate.split()
    if len(parts) < 2:
        return None
    for name in names:
        match = _INITIAL.match(name)
        if match and fuzz.ratio(parts[0].lower(), match.group(1).split()[0].lower()) >= 90 \
                and parts[-1][0].lower() == match.group(2).lower():
            return name
    return None


def check_founders(claim: Claim | None, data: dict) -> CheckResult:
    name = "founders"
    evidence = _paths(("people", "founders"), ("people", "cxos"), ("people", "decision_makers"))
    if claim is None:
        return unverifiable(name, claim, "No founder claim to check.", evidence)
    candidates = claimed_founders(claim)
    if not candidates:
        return unverifiable(name, claim, "No founder names could be read from the claim.", evidence)
    people = get(data, "people") or {}
    founders = [n for p in (people.get("founders") or [])
                if (n := _person_name(p)) and re.search("founder", _title(p), re.I)
                and not _NOT_FOUNDER.search(_title(p))]
    leaders = [n for key in ("cxos", "decision_makers") for p in (people.get(key) or []) if (n := _person_name(p))]
    if not founders and not leaders:
        return unverifiable(name, claim, "Crustdata has no founder or leadership records.", evidence)
    exact, abbreviated, executive, missing = [], [], [], []
    for candidate in candidates:
        if _name_hit(candidate, founders):
            exact.append(candidate)
        elif (shown := _initial_hit(candidate, founders)):
            abbreviated.append(f"{candidate} (recorded as '{shown}')")
        elif _name_hit(candidate, leaders):
            executive.append(candidate)
        else:
            missing.append(candidate)
    metric = MetricResult("founders_matched_share", len(exact) / len(candidates), "ratio",
                          "claimed founders confirmed by full name as founders / claimed founders")

    def result(status, severity, note, gate=None):
        return CheckResult(name, status, severity, note, claim.id, evidence, (metric,), gate)

    if len(exact) == len(candidates):
        return result("VERIFIED", 1, f"All {len(candidates)} claimed founders are recorded as founders.")
    notes = []
    if abbreviated:
        notes.append("only an abbreviated name can be matched: " + "; ".join(abbreviated))
    if executive:
        notes.append(f"recorded as executive(s) but not founder(s): {', '.join(executive)}")
    if missing:
        notes.append(f"not found in Crustdata's people: {', '.join(missing)}")
    if not missing:
        return result("PARTIAL", 1, "Not fully confirmed: " + "; ".join(notes) + ".")
    if exact or abbreviated or executive:
        return result("PARTIAL", 2, "Partly confirmed: " + "; ".join(notes) + ".")
    gate = "founder_not_found" if founders else None  # only with a real founder list to compare against
    return result("CONTRADICTED", 4, f"None of the claimed founders ({', '.join(missing)}) appear in "
                                     "Crustdata's people records.", gate)


def _news_date(item: dict) -> date | None:
    try:
        return date.fromisoformat(str(item.get("article_publish_date"))[:10])
    except ValueError:
        return None


def _quote(title: str) -> str:
    clean = " ".join(title.split())
    return clean if len(clean) <= NEWS_TITLE_CHARS else clean[:NEWS_TITLE_CHARS - 1] + "…"


def scan_news(claim: Claim | None, data: dict, *, today: date | None = None) -> CheckResult:
    name = "news_scan"
    evidence = _paths(("news",))
    news = get(data, "news")
    if not isinstance(news, list) or not news:
        return unverifiable(name, None, "Crustdata has no news for this company.", evidence)
    cutoff = (today or date.today()) - timedelta(days=NEWS_WINDOW_DAYS)
    recent = [item for item in news if isinstance(item, dict) and (d := _news_date(item)) and d >= cutoff
              and isinstance(item.get("article_title"), str)]
    if not recent:
        return unverifiable(name, None, f"No dated headlines in the last {NEWS_WINDOW_DAYS} days.", evidence)
    hits = [item for item in recent if ADVERSE_RE.search(item["article_title"])]
    severe = [item for item in hits if SEVERE_RE.search(item["article_title"])]
    metrics = (MetricResult("adverse_headlines", float(len(hits)), "headlines", "keyword matches, last 180 days"),
               MetricResult("headlines_scanned", float(len(recent)), "headlines", "dated headlines in window"))
    if not hits:
        return CheckResult(name, "VERIFIED", 1, f"No adverse headlines among {len(recent)} recent ones.", None,
                           evidence, metrics)
    examples = "; ".join(f"{item['article_publish_date'][:10]}: '{_quote(item['article_title'])}'"
                         for item in hits[:NEWS_EXAMPLES])
    severity = 4 if severe else (3 if len(hits) >= 3 else 2)
    return CheckResult(name, "PARTIAL", severity, f"{len(hits)} adverse headlines of {len(recent)} recent "
                       f"(headline text is untrusted data): {examples}", None, evidence, metrics)

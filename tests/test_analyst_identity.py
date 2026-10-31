"""Identity analyst. SYNTHETIC data shaped like Crustdata company_data, including quirks seen
in real responses: country codes ("USA"), a country-level-only HQ ("India, India"), founders
lists polluted with assistants, abbreviated surnames ("Anthony G.") and adverse headlines."""
from datetime import date

import pytest

from agents.analysts import identity
from app.schemas import Claim

TODAY = date(2026, 10, 31)


def claim(topic, text, value=None, unit=None):
    return Claim(id="C3", topic=topic, text=text, value=value, unit=unit, slide=1)


def person(name, title):
    return {"basic_profile": {"name": name, "current_title": title}}


def company(**overrides):
    base = {
        "basic_info": {"status": "active", "primary_domain": "acme.com", "all_domains": ["acme.com", "acme.io"],
                       "year_founded": 2017},
        "locations": {"city": "San Francisco", "state": "California", "country": "USA",
                      "headquarters": "San Francisco, California, United States",
                      "all_office_addresses": ["915 Broadway, New York, NY, 10010, US",
                                               "221 Pentonville Road, London, England, N1 9, GB"]},
        "people": {"founders": [person("David Hsu", "Founder, CEO"), person("Anthony G.", "Founder, CTO"),
                                person("Karan Pahuja", "Executive Assistant to Co-Founder & CEO"),
                                person("Ria Shah", "Founder's office")],
                   "cxos": [person("Michael Blank", "Chief Operating Officer"), person("Karan Pahuja", "Chief of Staff")],
                   "decision_makers": []},
        "news": [{"article_title": "Acme launches a new product", "article_publish_date": "2026-10-05"}],
    }
    for key, value in overrides.items():
        base[key] = {**base.get(key, {}), **value} if isinstance(value, dict) else value
    return base


# ---- founding year ---------------------------------------------------------------------

@pytest.mark.parametrize("claimed, status, severity", [
    (2017, "VERIFIED", 1), (2016, "PARTIAL", 1), (2018, "PARTIAL", 1),
    (2015, "CONTRADICTED", 3), (2013, "CONTRADICTED", 3), (2012, "CONTRADICTED", 4), (2025, "CONTRADICTED", 4),
])
def test_founding_year(claimed, status, severity):
    result = identity.check_founding_year(claim("identity_founding", "Founded", float(claimed), "year"), company())
    assert (result.status, result.severity) == (status, severity)
    assert result.evidence_paths == ("basic_info.year_founded",)


@pytest.mark.parametrize("the_claim, data", [
    (claim("identity_founding", "Founded long ago", None), company()),
    (claim("identity_founding", "Founded", 1500.0, "year"), company()),
    (claim("identity_founding", "Founded", 2017.0, "year"), company(basic_info={"year_founded": None})),
    (None, company()),
])
def test_founding_year_unverifiable(the_claim, data):
    assert identity.check_founding_year(the_claim, data).status == "UNVERIFIABLE"


# ---- headquarters ----------------------------------------------------------------------

@pytest.mark.parametrize("text, status, severity", [
    ("Headquartered in San Francisco, CA", "VERIFIED", 1),
    ("Based in San Francisco, California", "VERIFIED", 1),
    ("HQ: San Francisco, United States", "VERIFIED", 1),
    ("Headquartered in San Francisco, US", "VERIFIED", 1),
    ("We are based in New York", "PARTIAL", 1),            # an office, not the HQ
    ("Headquartered in San Francisco and London", "PARTIAL", 1),
    ("Headquartered in San Francisco, Germany", "PARTIAL", 2),
    ("Headquartered in Austin, TX", "CONTRADICTED", 3),
])
def test_headquarters(text, status, severity):
    result = identity.check_hq(claim("identity_hq", text), company())
    assert (result.status, result.severity) == (status, severity)


def test_abbreviation_ca_means_california_not_canada():
    assert identity.check_hq(claim("identity_hq", "Based in San Francisco, CA"), company()).status == "VERIFIED"


def test_country_level_hq_cannot_confirm_a_city():
    thin = company(locations={"city": "India", "state": None, "country": "IND", "headquarters": "India, India",
                              "all_office_addresses": []})
    partial = identity.check_hq(claim("identity_hq", "Headquartered in Bengaluru, India"), thin)
    assert (partial.status, partial.severity) == ("PARTIAL", 1) and "country-level" in partial.note
    assert identity.check_hq(claim("identity_hq", "Headquartered in India"), thin).status == "VERIFIED"
    assert identity.check_hq(claim("identity_hq", "Headquartered in Berlin, Germany"), thin).status == "CONTRADICTED"


def test_city_aliases_match():
    data = company(locations={"city": "Bengaluru", "state": "Karnataka", "country": "IND",
                              "headquarters": "Bengaluru, Karnataka, India", "all_office_addresses": []})
    assert identity.check_hq(claim("identity_hq", "Headquartered in Bangalore, India"), data).status == "VERIFIED"


@pytest.mark.parametrize("data", [{}, company(locations={"headquarters": None, "city": None, "state": None,
                                                         "country": None, "all_office_addresses": None})])
def test_headquarters_unverifiable_without_location(data):
    assert identity.check_hq(claim("identity_hq", "Based in Paris"), data).status == "UNVERIFIABLE"
    assert identity.check_hq(None, company()).status == "UNVERIFIABLE"


# ---- status and domain -----------------------------------------------------------------

@pytest.mark.parametrize("status, expected, severity, gate", [
    ("active", "VERIFIED", 1, None), ("Operating", "VERIFIED", 1, None),
    ("closed", "CONTRADICTED", 5, "entity_not_active"), ("Dissolved", "CONTRADICTED", 5, "entity_not_active"),
    ("acquired", "PARTIAL", 3, None), ("strange", "UNVERIFIABLE", 1, None),
])
def test_status(status, expected, severity, gate):
    result = identity.check_status_and_domain(None, company(basic_info={"status": status}))
    assert (result.status, result.severity, result.gate) == (expected, severity, gate)


def test_unknown_status_never_proposes_a_gate():
    assert identity.check_status_and_domain(None, company(basic_info={"status": None})).gate is None


def test_domain_in_the_claim_must_be_a_known_domain():
    ok = identity.check_status_and_domain(claim("identity_hq", "See https://www.acme.com for details"), company())
    assert ok.status == "VERIFIED"
    other = identity.check_status_and_domain(claim("identity_hq", "Visit rival.com"), company())
    assert (other.status, other.severity) == ("CONTRADICTED", 3) and "rival.com" in other.note


# ---- founders --------------------------------------------------------------------------

def founders_claim(text):
    return claim("identity_founder", text)


def test_founders_full_name_match_is_verified():
    result = identity.check_founders(founders_claim("Founded by David Hsu"), company())
    assert result.status == "VERIFIED" and result.metrics[0].value == 1.0


def test_abbreviated_graph_names_are_never_verified_only_partial():
    result = identity.check_founders(founders_claim("Founded by David Hsu and Anthony Gao"), company())
    assert (result.status, result.severity) == ("PARTIAL", 1)
    assert "recorded as 'Anthony G.'" in result.note
    other = identity.check_founders(founders_claim("Founded by Anthony Garcia"), company())
    assert other.status == "PARTIAL"   # same initial: cannot tell them apart
    different = identity.check_founders(founders_claim("Founded by Anthony Smith"), company())
    assert different.status == "CONTRADICTED"


def test_assistants_and_office_staff_are_not_founders():
    # "Karan Pahuja" sits in the founders list but as an assistant: only an executive match.
    result = identity.check_founders(founders_claim("Founded by Karan Pahuja"), company())
    assert result.status == "PARTIAL" and "executive" in result.note
    staff = identity.check_founders(founders_claim("Founded by Ria Shah"), company())
    assert staff.status == "CONTRADICTED"


def test_missing_and_partial_founders():
    partial = identity.check_founders(founders_claim("Founded by David Hsu and Jane Roe"), company())
    assert (partial.status, partial.severity) == ("PARTIAL", 2) and "Jane Roe" in partial.note
    none = identity.check_founders(founders_claim("Founded by Elon Musk"), company())
    assert (none.status, none.severity, none.gate) == ("CONTRADICTED", 4, "founder_not_found")


def test_no_gate_without_a_real_founder_list():
    only_leaders = company(people={"founders": [], "cxos": [person("Michael Blank", "COO")]})
    result = identity.check_founders(founders_claim("Founded by Elon Musk"), only_leaders)
    assert result.status == "CONTRADICTED" and result.gate is None


@pytest.mark.parametrize("the_claim, data", [
    (founders_claim("Founded by someone"), company(people={"founders": [], "cxos": [], "decision_makers": []})),
    (founders_claim("by"), company()), (None, company()), (founders_claim("Founded by David Hsu"), {}),
])
def test_founders_unverifiable(the_claim, data):
    assert identity.check_founders(the_claim, data).status == "UNVERIFIABLE"


# ---- news ------------------------------------------------------------------------------

def news(*titles, day="2026-10-28"):
    return {"news": [{"article_title": t, "article_publish_date": day} for t in titles]}


def test_clean_news_is_verified():
    result = identity.scan_news(None, company(**news("Acme raises funding", "Acme hires a CTO")), today=TODAY)
    assert result.status == "VERIFIED" and result.metrics[0].value == 0


@pytest.mark.parametrize("titles, severity", [
    (["Regulator suspends Acme store operations over hygiene lapses"], 2),
    (["Acme sued by supplier", "Acme faces probe", "Acme announces layoffs"], 3),
    (["Founder arrested in fraud case at Acme"], 4),
])
def test_adverse_news_levels(titles, severity):
    result = identity.scan_news(None, company(**news(*titles)), today=TODAY)
    assert (result.status, result.severity) == ("PARTIAL", severity)
    assert "untrusted data" in result.note and result.claim_id is None


def test_news_notes_are_short_and_limited():
    long_title = "Acme sued " + "x" * 400
    many = [f"Acme lawsuit number {i}" for i in range(10)]
    note = identity.scan_news(None, company(**news(long_title, *many)), today=TODAY).note
    assert note.count("'") <= 2 * 3 and len(note) < 600     # at most three quoted examples, each cut short


def test_only_recent_headlines_count():
    old = identity.scan_news(None, company(**news("Acme fraud scandal", day="2025-01-01")), today=TODAY)
    assert old.status == "UNVERIFIABLE"   # outside the 180-day window


def test_keywords_match_whole_words_only():
    harmless = identity.scan_news(None, company(**news("Acme joins the Raiders fan club",
                                                       "Acme opens a new branch")), today=TODAY)
    assert harmless.status == "VERIFIED"  # 'Raiders' is not 'raid'


def test_keyword_scan_is_coarse_so_news_is_never_a_contradiction():
    # A keyword match cannot tell a space probe from a regulatory probe, so even the most
    # severe headlines only ever produce PARTIAL findings, never CONTRADICTED, and no gate.
    for title in ("Space probe reaches Mars as Acme sponsors mission", "Founder arrested in fraud case"):
        result = identity.scan_news(None, company(**news(title)), today=TODAY)
        assert result.status == "PARTIAL" and result.gate is None and result.severity <= 4


@pytest.mark.parametrize("data", [{}, company(news=[]), company(news="bad"), company(news=[{"article_title": 5}])])
def test_news_unverifiable_without_usable_items(data):
    assert identity.scan_news(None, data, today=TODAY).status == "UNVERIFIABLE"


# ---- null safety -----------------------------------------------------------------------

CHECKS = [identity.check_founding_year, identity.check_hq, identity.check_status_and_domain,
          identity.check_founders, identity.scan_news]


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("data", [{}, None, {"basic_info": None, "locations": None, "people": None, "news": None},
                                  {"basic_info": "x", "locations": 5, "people": [], "news": {}},
                                  {"people": {"founders": ["junk", {"basic_profile": None}], "cxos": "bad"}},
                                  {"locations": {"all_office_addresses": [None, 5], "headquarters": 7}}])
@pytest.mark.parametrize("the_claim", [None, claim("identity_hq", "Based in Paris"),
                                       claim("identity_founder", "Founded by A B"),
                                       claim("identity_founding", "Founded", 2017.0, "year")])
def test_no_check_raises_on_missing_or_malformed_data(check, data, the_claim):
    result = check(the_claim, data)
    assert result.status in ("VERIFIED", "PARTIAL", "CONTRADICTED", "UNVERIFIABLE")
    assert 1 <= result.severity <= 5

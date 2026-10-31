"""Daksh: generate the three SYNTHETIC demo pitch decks. Deterministic; ASCII text only.

    python -m scripts.make_demo_decks [output_dir]        # default: demo/decks

  01_honest_retool.pdf         every figure matches Crustdata's data for Retool
  02_inflated_retool.pdf       same layout, deliberately false figures
  03_fake_company_orrery.pdf   a company that does not exist: the resolver must refuse to assess it

Every slide carries a visible disclaimer. The Retool decks are test fixtures built from public company
data and are NOT statements by Retool; the inflated one is labelled as deliberately false.
"""
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

WIDTH, HEIGHT = 960, 540
INK, ACCENT, GRAY, PALE = (0.10, 0.12, 0.18), (0.12, 0.35, 0.85), (0.45, 0.47, 0.52), (0.93, 0.95, 0.99)
WARN = (0.75, 0.15, 0.15)
MARGIN = 60


@dataclass(frozen=True)
class Slide:
    kicker: str
    title: str
    bullets: tuple[str, ...] = ()
    callouts: tuple[tuple[str, str], ...] = ()   # (big figure, label)


@dataclass(frozen=True)
class Deck:
    filename: str
    company: str
    tagline: str
    subtitle: str
    disclaimer: str
    slides: tuple[Slide, ...]
    expected: tuple[str, ...]                     # phrases the tests require in the text layer


QUESTIONS = Slide("Thank you", "Questions", ("We are glad to go deeper on any slide.",))

HONEST = Deck(
    filename="01_honest_retool.pdf",
    company="Retool",
    tagline="The fastest way to build internal software",
    subtitle="Investor update",
    disclaimer="SYNTHETIC DEMO DECK for software testing only. Not issued by Retool; figures are illustrative. "
               "Not investment advice.",
    slides=(
        Slide("The problem", "Internal tools take too long to build",
              ("Engineering teams spend months on admin screens, forms and permissions",
               "Most of that work repeats from company to company",
               "Teams fall back on spreadsheets and half-built tools")),
        Slide("The solution", "Build internal apps in days, not months",
              ("A visual builder that connects to any database or API",
               "Permissions, audit logs and hosting are built in",
               "Engineers stay in control with code where they need it")),
        Slide("Company snapshot", "Who we are",
              ("Founded in 2017", "Headquartered in San Francisco, California", "Founded by David Hsu")),
        Slide("Traction", "Steady usage, a lean team",
              ("Used by more than 5,000 companies", "We hire selectively and keep the team lean"),
              (("1.2M", "monthly website visitors"), ("1", "open role today"))),
        Slide("Business model", "Subscription software",
              ("Pricing per builder and per end user", "Revenue comes mainly from mid-size and large companies"),
              (("$35M", "annual recurring revenue (ARR)"),)),
        Slide("Team", "A focused company",
              ("Engineering is our largest team", "Employees across San Francisco, New York and London"),
              (("About 400", "employees"),)),
        Slide("Funding", "Well funded since 2017",
              ("Latest round: Series C of $45 million in July 2022",
               "Backed by Sequoia Capital, Stripe and Y Combinator"),
              (("$141M", "raised in total"),)),
        Slide("Market", "Companies keep building internal software",
              ("Low-code and internal tools are our market", "AI makes internal tools faster to build",
               "Security and governance matter more every year")),
        Slide("Outlook", "What comes next",
              ("Focus on larger customers and profitability", "We are not raising a new round this year")),
        QUESTIONS,
    ),
    expected=("Founded in 2017", "Headquartered in San Francisco, California", "Founded by David Hsu",
              "1.2M", "monthly website visitors", "open role today", "$35M", "About 400", "$141M",
              "Series C of $45 million in July 2022", "Sequoia Capital, Stripe and Y Combinator"),
)

INFLATED = Deck(
    filename="02_inflated_retool.pdf",
    company="Retool",
    tagline="The fastest way to build internal software",
    subtitle="Investor update",
    disclaimer="SYNTHETIC DEMO DECK with DELIBERATELY FALSE figures, made to test fact-checking. "
               "Not issued by Retool. Not investment advice.",
    slides=(
        HONEST.slides[0],
        HONEST.slides[1],
        Slide("Company snapshot", "Who we are",
              ("Founded in 2012", "Headquartered in Austin, Texas")),
        Slide("Traction", "Explosive growth",
              ("Used by more than 5,000 enterprise customers", "Website traffic is up 300% year over year"),
              (("12M", "monthly website visitors"), ("120", "open roles, and we are hiring aggressively"))),
        Slide("Business model", "Subscription software at scale",
              ("Revenue is growing 3x year over year", "Revenue comes mainly from large enterprises"),
              (("$200M", "annual recurring revenue (ARR)"),)),
        Slide("Team", "A fast-growing company",
              ("Headcount grew 100% year over year", "Founded by David Hsu and Jonas Wexford-Pryce"),
              (("1,200", "employees"),)),
        Slide("Funding", "Top-tier investors",
              ("Latest round: Series D of $150 million in 2026", "Backed by Tiger Global and SoftBank"),
              (("$400M", "raised in total"),)),
        HONEST.slides[7],
        Slide("The ask", "Raising now",
              ("Raising $300 million", "At a $5 billion valuation")),
        QUESTIONS,
    ),
    expected=("Founded in 2012", "Headquartered in Austin, Texas", "12M", "120", "$200M", "1,200", "$400M",
              "Series D of $150 million in 2026", "Tiger Global and SoftBank", "100% year over year",
              "At a $5 billion valuation", "Jonas Wexford-Pryce"),
)

FAKE = Deck(
    filename="03_fake_company_orrery.pdf",
    company="Orrery Vantage Systems GmbH",
    tagline="Predictive maintenance for industrial fleets",
    subtitle="Series B investor presentation",
    disclaimer="SYNTHETIC DEMO DECK for software testing only. This company does not exist.",
    slides=(
        Slide("The problem", "Unplanned downtime is expensive",
              ("Fleet operators lose millions to unexpected machine failures",
               "Scheduled maintenance wastes parts and technician time")),
        Slide("The solution", "Predict failures before they happen",
              ("Sensors and machine learning flag faults weeks in advance",
               "Maintenance teams get a ranked, plain-language work list")),
        Slide("Company snapshot", "Who we are",
              ("Founded in 2019", "Headquartered in Munich, Germany",
               "Founded by Lena Hartmann and Tobias Brandt")),
        Slide("Traction", "Growing industrial adoption",
              ("Used by 40 industrial customers across Europe",),
              (("300K", "monthly website visitors"), ("12", "open roles today"))),
        Slide("Business model", "Annual contracts per machine",
              ("Revenue grew 150% year over year",),
              (("$4M", "annual recurring revenue (ARR)"),)),
        Slide("Team", "Engineers and domain experts",
              ("Headcount grew 60% year over year",),
              (("85", "employees"),)),
        Slide("Funding", "Backed by European investors",
              ("Latest round: Series A of $12 million in 2024",
               "Backed by Northlight Ventures and Alpine Growth Partners"),
              (("$18M", "raised in total"),)),
        Slide("The ask", "Raising a Series B",
              ("Raising $25 million", "At a $150 million valuation")),
        QUESTIONS,
    ),
    expected=("Orrery Vantage Systems GmbH", "Founded in 2019", "Headquartered in Munich, Germany", "85",
              "$18M", "Raising $25 million"),
)

DECKS = (HONEST, INFLATED, FAKE)


def _box(page, rect, text, size, font="helv", color=INK, align=0):
    leftover = page.insert_textbox(pymupdf.Rect(*rect), text, fontsize=size, fontname=font, color=color,
                                   align=align)
    if leftover < 0:
        raise ValueError(f"Text does not fit its box: {text[:50]!r}")


def _footer(page, deck, number):
    page.draw_line((MARGIN, HEIGHT - 46), (WIDTH - MARGIN, HEIGHT - 46), color=PALE, width=1)
    _box(page, (MARGIN, HEIGHT - 42, WIDTH - MARGIN - 40, HEIGHT - 8), deck.disclaimer, 9, color=WARN)
    _box(page, (WIDTH - MARGIN - 30, HEIGHT - 42, WIDTH - MARGIN, HEIGHT - 8), str(number), 10, "hebo",
         GRAY, align=2)


def _title_slide(doc, deck):
    page = doc.new_page(width=WIDTH, height=HEIGHT)
    page.draw_rect(pymupdf.Rect(0, 0, WIDTH, HEIGHT), color=None, fill=PALE)
    page.draw_rect(pymupdf.Rect(0, 0, 14, HEIGHT), color=None, fill=ACCENT)
    _box(page, (MARGIN + 10, 150, WIDTH - MARGIN, 250), deck.company, 54 if len(deck.company) < 20 else 40,
         "hebo", INK)
    _box(page, (MARGIN + 10, 262, WIDTH - MARGIN, 310), deck.tagline, 24, color=ACCENT)
    _box(page, (MARGIN + 10, 330, WIDTH - MARGIN, 370), deck.subtitle, 16, color=GRAY)
    _footer(page, deck, 1)


def _content_slide(doc, deck, slide, number):
    page = doc.new_page(width=WIDTH, height=HEIGHT)
    page.draw_rect(pymupdf.Rect(0, 0, 12, HEIGHT), color=None, fill=ACCENT)
    _box(page, (MARGIN, 26, WIDTH - MARGIN, 58), slide.kicker.upper(), 12, "hebo", ACCENT)
    _box(page, (MARGIN, 60, WIDTH - MARGIN, 130), slide.title, 32, "hebo", INK)
    top = 150
    if slide.callouts:
        gap = 30
        width = (WIDTH - 2 * MARGIN - gap * (len(slide.callouts) - 1)) / len(slide.callouts)
        for i, (figure, label) in enumerate(slide.callouts):
            x = MARGIN + i * (width + gap)
            page.draw_rect(pymupdf.Rect(x, top, x + width, top + 130), color=None, fill=PALE)
            _box(page, (x + 18, top + 8, x + width - 10, top + 78), figure, 40, "hebo", ACCENT)
            _box(page, (x + 18, top + 80, x + width - 10, top + 128), label, 16, color=INK)
        top += 160
    if slide.bullets:
        _box(page, (MARGIN, top, WIDTH - MARGIN, HEIGHT - 60), "\n\n".join("- " + b for b in slide.bullets),
             20, color=INK)
    _footer(page, deck, number)


def build(deck: Deck, path: Path) -> None:
    doc = pymupdf.open()
    _title_slide(doc, deck)
    for number, slide in enumerate(deck.slides, start=2):
        _content_slide(doc, deck, slide, number)
    doc.set_metadata({"title": f"{deck.company} (synthetic demo deck)", "author": "Leo demo fixtures",
                      "creationDate": "", "modDate": ""})
    doc.save(str(path), deflate=True, garbage=3)
    doc.close()


def main(out_dir: str = "demo/decks") -> list[Path]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for deck in DECKS:
        path = target / deck.filename
        build(deck, path)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for written in main(*sys.argv[1:2]):
        print(f"wrote {written} ({written.stat().st_size // 1024} KB)")

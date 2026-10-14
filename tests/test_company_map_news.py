"""
tests/test_company_map_news.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The startup-news classifier, the funding extractor and the RSS parser.

Most titles are copied verbatim from the recorded feeds in
``tests/fixtures/company_map/rss_*.xml``; the rest are written to pin one
rule each (an amount format, a negative). No database, no network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from domain.news import INR_PER_USD, FundingFacts, classify, extract_funding, founders_from_text
from services.company_map.news_feeds import (
    FEEDS,
    MAX_FEED_BYTES,
    USER_AGENT,
    fetch_feed,
    parse_feed,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "company_map"

CLUESO = "Clueso Bags $10 Mn To Automate Enterprise Video Creation With AI Agents"
MESA = "Mesa School of Business raises Rs 110 Cr co-led by Dharana Capital and Elevation Capital"


# ── classify ─────────────────────────────────────────────────────────────────


def test_clueso_title_is_funding() -> None:
    assert classify(CLUESO) == "funding"


def test_clueso_funding_facts() -> None:
    facts = extract_funding(CLUESO, None)
    assert isinstance(facts, FundingFacts)
    assert facts.subject == "Clueso"
    assert facts.amount_text == "$10 Mn"
    assert facts.amount_usd == 10_000_000


def test_mesa_school_funding_facts() -> None:
    assert classify(MESA) == "funding"
    facts = extract_funding(MESA, None)
    assert facts.subject == "Mesa School of Business"
    assert facts.amount_text == "Rs 110 Cr"
    assert facts.amount_usd == pytest.approx(110e7 / INR_PER_USD)
    assert facts.investors == ("Dharana Capital", "Elevation Capital")


def test_inr_per_usd_is_the_fixed_constant() -> None:
    assert INR_PER_USD == 88.0


def test_freshworks_is_an_acquisition() -> None:
    assert classify("Freshworks To Acquire Torii To Expand AI Spend Governance Offerings") == (
        "acquisition"
    )


@pytest.mark.parametrize(
    "title",
    [
        "Razorpay CEO on raising the bar",
        "Layoffs at X as funding dries up",
        "How to raise a seed round",
        # real titles from the recorded feeds
        "Exclusive: Traya In Talks To Raise Around ₹300 Cr From Verlinvest",
        "Urban Company Eyes Fresh Fundraise A Year After IPO As InstaHelp Losses Mount",
        "Sarvam plans big fundraise; HCLTech's Q2 profit jumps",
        "SoftBank junk bond sale raises stakes for CEO Son's all-in AI bet",
        "Fintech funding rises 14% to $2.2 billion in Jan-Sep; deal count falls 41%: Tracxn",
        "Blackstone in talks to invest $400-500 million in defence tech firm Accord",
        "UC Hungry For Funds Again, DailyObjects’ D2C Playbook & More",
        "Fundamentum Frontier Advisors Marks First Close Of ₹3,000 Cr Deeptech-Focused Fund",
        "Citi and Credendo close $328 million deal to finance IndiGo’s 8 Airbus jets",
        "Groww Q2: Profit Jumps 54% YoY To ₹725 Cr, Revenue Rises 47%",
        "Startup raises concerns over new data rules",
        # same verbs, but an order, a contract or a tax demand, not a round
        "L&T bags Rs 1,200 Cr order from NHAI",
        "Zomato gets Rs 400 Cr GST demand notice",
        "Infosys secures $1.5 Bn deal from European retailer",
        "Tata Power lands ₹500 Cr contract for solar park",
        "Why this founder raised $5 Mn and still shut down?",
    ],
)
def test_non_funding_titles_are_not_funding(title: str) -> None:
    assert classify(title) != "funding"
    assert extract_funding(title, None).subject is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("IonQ to buy cybersecurity firm Elisity in up to $1.3 billion deal", "acquisition"),
        ("Nvidia buys stakes in four French tech startups: Report", "other"),
        ("Qualcomm sells nearly 1% stake in Shadowfax Technologies for Rs 157 crore", "other"),
        ("Google launches flagship AI model Gemini 4 Argon for developers, businesses", "launch"),
        ("The Personal Agents Era Comes With Caveats", "other"),
        (
            "Sunil Mittal takes on Elon Musk, says Bharti is ahead of Starlink, ready to launch",
            "other",
        ),
    ],
)
def test_other_categories(title: str, expected: str) -> None:
    assert classify(title) == expected


# ── extract_funding on real titles ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "subject", "amount_text", "amount_usd"),
    [
        (
            "Spill Games Raises $10.5 Mn To Scale AI-Led Mobile Gaming Studio",
            "Spill Games",
            "$10.5 Mn",
            10_500_000,
        ),
        (
            "Circle Raises ₹25 Cr To Expand Its Pre-Owned Goods Marketplace Beyond Bengaluru",
            "Circle",
            "₹25 Cr",
            25e7 / 88,
        ),
        (
            "GreenCell Mobility Raises ₹330 Cr To Expand Electric Bus Fleet",
            "GreenCell Mobility",
            "₹330 Cr",
            330e7 / 88,
        ),
        (
            "Agentic AI Startup RevRag.AI Nets $2.5 Mn To Build AI Relationship Managers "
            "For Lenders And Insurers",
            "RevRag.AI",
            "$2.5 Mn",
            2_500_000,
        ),
        (
            "Manufacturing Startup ThinkMetal Raises ₹27 Cr To Initiate Commercialisation",
            "ThinkMetal",
            "₹27 Cr",
            27e7 / 88,
        ),
        (
            "M Pallonji backed Protec General secures around  ₹300 crore to expand operations",
            "Protec General",
            "₹300 crore",
            300e7 / 88,
        ),
        (
            "KL Rahul-backed Boldfit secures $42 million to boost retail presence across India",
            "Boldfit",
            "$42 million",
            42_000_000,
        ),
        (
            "Agentic video startup Clueso raises $10M in Series A funding round",
            "Clueso",
            "$10M",
            10_000_000,
        ),
        (
            "Bengaluru-based fintech startup Jupiter secures $1.5 Bn",
            "Jupiter",
            "$1.5 Bn",
            1_500_000_000,
        ),
        ("Acme Robotics gets INR 50 Cr in seed funding", "Acme Robotics", "INR 50 Cr", 50e7 / 88),
        ("Krishi Labs lands Rs 5 lakh grant funding", "Krishi Labs", "Rs 5 lakh", 5e5 / 88),
        ("Nexa closes ₹200 Crore Series B round", "Nexa", "₹200 Crore", 200e7 / 88),
    ],
)
def test_real_funding_titles(title: str, subject: str, amount_text: str, amount_usd: float) -> None:
    assert classify(title) == "funding"
    facts = extract_funding(title, None)
    assert facts.subject == subject
    assert facts.amount_text == amount_text
    assert facts.amount_usd == pytest.approx(amount_usd)


def test_investors_after_from() -> None:
    title = "Speciality Chemicals Startup NuSynth Raises ₹36.5 Cr From Avaana Capital, Shastra VC"
    facts = extract_funding(title, None)
    assert facts.subject == "NuSynth"
    assert facts.amount_usd == pytest.approx(36.5e7 / 88)
    assert facts.investors == ("Avaana Capital", "Shastra VC")


def test_investor_first_title_names_the_company_after_in() -> None:
    title = (
        "Amansa Investments, SBI Mutual Fund, others invest Rs 413 Cr in Tonbo Imaging ahead of IPO"
    )
    assert classify(title) == "funding"
    facts = extract_funding(title, None)
    assert facts.subject == "Tonbo Imaging"
    assert facts.amount_text == "Rs 413 Cr"
    assert facts.investors == ("Amansa Investments", "SBI Mutual Fund")


def test_single_investor_first_title() -> None:
    facts = extract_funding("The Wealth Company AIF invests Rs 400 crore in Boldfit", None)
    assert facts.subject == "Boldfit"
    assert facts.investors == ("The Wealth Company AIF",)


def test_person_subject_is_left_for_the_fallback() -> None:
    title = (
        "[Update] Exclusive: Former Dyson India Boss Ankit Jain Bags Funding From BEENEXT "
        "For New Venture"
    )
    assert classify(title) == "funding"
    facts = extract_funding(title, None)
    assert facts.subject is None
    assert facts.amount_text is None
    assert facts.investors == ("BEENEXT",)


def test_round_comes_from_title() -> None:
    facts = extract_funding(
        "Agentic video startup Clueso raises $10M in Series A funding round", None
    )
    assert facts.round == "Series A"
    assert extract_funding("Acme raises $2 Mn in pre-seed round", None).round == "Pre-seed"
    assert extract_funding("Acme raises $4 Mn in Pre-Series A round", None).round == "Pre-Series A"
    assert extract_funding("Acme raises Rs 20 Cr in venture debt", None).round == "Debt"


def test_snippet_supplies_round_and_investors() -> None:
    facts = extract_funding(
        CLUESO,
        "The Series A round was led by Accel, with participation from Lightspeed and "
        "existing investors.",
    )
    assert facts.round == "Series A"
    assert facts.investors == ("Accel", "Lightspeed")


@pytest.mark.parametrize(
    ("snippet", "investors"),
    [
        # inc42 cuts its descriptions mid-name: a cut name is no name
        (
            "Pre-owned goods marketplace Circle has raised ₹25 Cr (about $2.6 Mn) in a seed "
            "funding round led by Nexus Venture…",
            (),
        ),
        # ...but the names before a cut separator are whole
        (
            "Bengaluru-based NuSynth Technologies has raised ₹36.5 Cr (about $4 Mn) in a seed "
            "funding round co-led by Avaana Capital and…",
            ("Avaana Capital",),
        ),
        (
            "The round was led by Accel, Elevation Capital and Ligh...",
            ("Accel", "Elevation Capital"),
        ),
    ],
)
def test_truncated_snippet_never_yields_a_partial_name(
    snippet: str, investors: tuple[str, ...]
) -> None:
    facts = extract_funding("Circle Raises ₹25 Cr To Expand", snippet)
    assert facts.investors == investors
    assert facts.round == "Seed" or "seed" not in snippet


def test_with_participation_from_extends_investors() -> None:
    facts = extract_funding(
        "Zeno raises $3 Mn led by Peak XV with participation from Blume Ventures and Titan Capital",
        None,
    )
    assert facts.subject == "Zeno"
    assert facts.investors == ("Peak XV", "Blume Ventures", "Titan Capital")


def test_non_funding_title_extracts_nothing() -> None:
    assert extract_funding("Freshworks To Acquire Torii", None) == FundingFacts(
        subject=None, round=None, amount_text=None, amount_usd=None, investors=()
    )


# ── founders ─────────────────────────────────────────────────────────────────


def test_founders_from_text() -> None:
    assert founders_from_text("founded by Asha Rao and Vikram Iyer in 2021") == [
        "Asha Rao",
        "Vikram Iyer",
    ]


def test_cofounders_list() -> None:
    text = "The startup was co-founded by Meera Nair, Rohan Das and K. Suresh Kumar in 2019."
    assert founders_from_text(text) == ["Meera Nair", "Rohan Das", "K. Suresh Kumar"]


def test_founded_in_year_by() -> None:
    # real ET Tech snippet
    text = "Founded in 2018 by Pallav Bihani, Boldfit offers sportswear, athletic footwear."
    assert founders_from_text(text) == ["Pallav Bihani"]


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "founded in 2021 in Bengaluru",
        "founded by former Flipkart executives",
        # real YourStory snippet: an institution, not a person
        "The startup founded by IIT Madras alumni plans to use the funding to scale.",
        "founded by Google Cloud veterans",
    ],
)
def test_no_founders(text: str | None) -> None:
    assert founders_from_text(text) == []


# ── feeds ────────────────────────────────────────────────────────────────────


def test_feeds_are_the_four_publishers() -> None:
    assert FEEDS == {
        "inc42": "https://inc42.com/feed/",
        "yourstory": "https://yourstory.com/feed",
        "ettech": "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",
        "mint": "https://www.livemint.com/rss/companies",
    }


@pytest.mark.parametrize("source", sorted(FEEDS))
def test_parse_recorded_feed(source: str) -> None:
    items = parse_feed((FIXTURES / f"rss_{source}.xml").read_bytes(), source)
    assert len(items) >= 10
    for item in items:
        assert item.source == source
        parts = urlsplit(item.url)
        assert parts.scheme == "https" and parts.netloc
        assert item.title and "<" not in item.title
        assert item.published_at is not None and item.published_at.tzinfo is not None
        assert item.snippet is None or (len(item.snippet) <= 600 and "<" not in item.snippet)
    assert len({item.url for item in items}) == len(items)


def test_inc42_snippet_is_text_not_html() -> None:
    items = parse_feed((FIXTURES / "rss_inc42.xml").read_bytes(), "inc42")
    clueso = next(i for i in items if i.title.startswith("Clueso Bags"))
    assert clueso.title == CLUESO
    assert clueso.snippet and "img" not in clueso.snippet and "&#" not in clueso.snippet
    assert clueso.published_at == datetime(2026, 10, 13, 11, 58, 44, tzinfo=UTC)


def test_recorded_funding_titles_classify() -> None:
    titles = {
        i.title
        for source in FEEDS
        for i in parse_feed((FIXTURES / f"rss_{source}.xml").read_bytes(), source)
        if classify(i.title) == "funding"
    }
    assert {
        CLUESO,
        "Spill Games Raises $10.5 Mn To Scale AI-Led Mobile Gaming Studio",
        "Agentic video startup Clueso raises $10M in Series A funding round",
        "KL Rahul-backed Boldfit secures $42 million to boost retail presence across India",
        "The Wealth Company AIF invests Rs 400 crore in Boldfit",
    } <= titles


_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Acme raises $1 Mn</title><link>https://example.com/a</link>
<description>&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;</description>
<pubDate>Tue, 13 Oct 2026 11:58:44 +0530</pubDate></item>
<item><title>Insecure</title><link>http://example.com/b</link>
<pubDate>Tue, 13 Oct 2026 11:58:44 +0000</pubDate></item>
<item><title>Relative</title><link>/c</link></item>
<item><title></title><link>https://example.com/d</link></item>
</channel></rss>"""


def test_parse_rss_keeps_only_absolute_https_items() -> None:
    items = parse_feed(_RSS.encode(), "inc42")
    assert [i.url for i in items] == ["https://example.com/a"]
    item = items[0]
    assert item.title == "Acme raises $1 Mn"
    assert item.snippet == "Hello & welcome"
    assert item.published_at == datetime(2026, 10, 13, 6, 28, 44, tzinfo=UTC)


def test_parse_atom_entries() -> None:
    atom = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
<entry><title type="html">Acme &lt;b&gt;bags&lt;/b&gt; $2 Mn</title>
<link rel="alternate" href="https://example.com/acme"/>
<summary>Short summary.</summary><updated>2026-10-13T09:00:00Z</updated></entry>
</feed>"""
    [item] = parse_feed(atom, "mint")
    assert item.url == "https://example.com/acme"
    assert item.title == "Acme bags $2 Mn"
    assert item.snippet == "Short summary."
    assert item.published_at == datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def test_naive_dates_are_treated_as_utc() -> None:
    xml = (
        b"<rss><channel><item><title>A</title><link>https://example.com/a</link>"
        b"<pubDate>Tue, 13 Oct 2026 11:58:44 -0000</pubDate></item></channel></rss>"
    )
    [item] = parse_feed(xml, "mint")
    assert item.published_at == datetime(2026, 10, 13, 11, 58, 44, tzinfo=UTC)


def test_unparseable_date_is_none() -> None:
    xml = (
        b"<rss><channel><item><title>A</title><link>https://example.com/a</link>"
        b"<pubDate>yesterday</pubDate></item></channel></rss>"
    )
    [item] = parse_feed(xml, "mint")
    assert item.published_at is None


def test_long_description_is_truncated() -> None:
    body = "word " * 400
    xml = (
        f"<rss><channel><item><title>A</title><link>https://example.com/a</link>"
        f"<description>{body}</description></item></channel></rss>"
    ).encode()
    [item] = parse_feed(xml, "mint")
    assert item.snippet is not None and len(item.snippet) <= 600


@pytest.mark.parametrize(
    "xml",
    [
        b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "boom">]>'
        b"<rss><channel><item><title>&x;</title><link>https://example.com/a</link>"
        b"</item></channel></rss>",
        b'<?xml version="1.0"?><!doctype rss SYSTEM "http://example.com/rss.dtd">'
        b"<rss><channel><item><title>A</title><link>https://example.com/a</link>"
        b"</item></channel></rss>",
        b"<rss><channel><!ENTITY a 'b'><item><title>A</title>"
        b"<link>https://example.com/a</link></item></channel></rss>",
        # the same declaration in UTF-16 must not slip past a bytes check
        '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE rss [<!ENTITY x "y">]>'
        "<rss><channel><item><title>&x;</title><link>https://example.com/a</link>"
        "</item></channel></rss>".encode("utf-16"),
    ],
)
def test_doctype_or_entity_declaration_is_rejected(xml: bytes) -> None:
    assert parse_feed(xml, "inc42") == []


def test_oversized_feed_is_rejected() -> None:
    item = b"<item><title>A</title><link>https://example.com/a</link></item>"
    xml = b"<rss><channel>" + item + b" " * MAX_FEED_BYTES + b"</channel></rss>"
    assert parse_feed(xml, "inc42") == []


@pytest.mark.parametrize("xml", [b"", b"not xml", b"<rss><channel><item>", b"\xff\xfe\x00"])
def test_malformed_xml_is_empty(xml: bytes) -> None:
    assert parse_feed(xml, "inc42") == []


# ── fetch ────────────────────────────────────────────────────────────────────


async def test_fetch_feed_identifies_itself_and_returns_the_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<rss/>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_feed(client, "inc42") == b"<rss/>"
    assert str(seen[0].url) == FEEDS["inc42"]
    assert seen[0].headers["User-Agent"] == USER_AGENT


async def test_fetch_feed_stops_at_the_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_FEED_BYTES + 1))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await fetch_feed(client, "mint")


async def test_fetch_feed_raises_on_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_feed(client, "ettech")

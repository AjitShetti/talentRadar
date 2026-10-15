"""
domain/event_relevance.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Whether an event belongs on the Bengaluru tech list, and the evidence why.

:func:`assess` answers two questions about one :class:`EventFacts` and returns
a :class:`Verdict`: a verdict with reasons, never a score.

* **Where** decides ``store``. An in-person event is stored when its
  coordinates fall inside Bengaluru, or, with no coordinates, when its address,
  venue or locality resolves to Bengaluru. An online event is stored only with
  an India signal: an Indian source, an India timezone, or "India", "IST" or
  "Bengaluru" in the text. Stated coordinates are decisive: a point in San
  Francisco is not rescued by an address that says Bengaluru. An event that is
  not stored is not looked at again.
* **What** decides ``listed``. A stored event is listed when it is technology,
  in a format worth going to, and not excluded. Online, only a hackathon,
  workshop or conference qualifies (a webinar meets nobody); a hiring event
  needs a host that is a tech company on the map, which keeps fee-charging
  placement drives out.

Every table below is a tuple of words matched whole-word and case-insensitively
over ``title + " " + snippet``, so "AI" is not "AIrline" and "rust" is not
"Rustic". A space or hyphen inside a word matches either ("open source",
"open-source", "Hands-On", "hands on").

The reasons are the evidence behind ``listed``, in display order: what makes it
tech (a GDG chapter, a trusted calendar, Eventbrite's technology category, or
else the topics it names), where it is, and who hosts it. ``scope.key`` is the
display name of a trusted Luma calendar: its reason reads "<key> calendar", so
the code that builds the scope must put the calendar's name there.

Pure: no I/O, no clock, no database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from domain.areas import area_for_point, area_for_text, in_bengaluru
from domain.event_facts import EventFacts, SourceScope
from domain.event_time import as_utc, effective_end
from domain.geo import mentions_foreign_country, resolve_city

FORMATS: tuple[str, ...] = (
    "hackathon",
    "conference",
    "demo",
    "workshop",
    "talk",
    "mixer",
    "meetup",
    "hiring",
    "other",
)

TOPICS: tuple[str, ...] = (
    "ai",
    "data",
    "cloud",
    "devops",
    "web",
    "mobile",
    "security",
    "product",
    "design",
    "startups",
    "opensource",
    "hardware",
    "web3",
    "quantum",
    "languages",
    "career",
)

#: How a topic reads in a reason ("GDG chapter · AI · in Koramangala").
TOPIC_LABELS: dict[str, str] = {
    "ai": "AI",
    "data": "Data",
    "cloud": "Cloud",
    "devops": "DevOps",
    "web": "Web",
    "mobile": "Mobile",
    "security": "Security",
    "product": "Product",
    "design": "Design",
    "startups": "Startups",
    "opensource": "Open source",
    "hardware": "Hardware",
    "web3": "Web3",
    "quantum": "Quantum",
    "languages": "Languages",
    "career": "Career",
}

#: Only these formats are worth attending when the event is online.
ONLINE_FORMATS: tuple[str, ...] = ("hackathon", "workshop", "conference")

#: An event that runs longer than this is a course, not an event.
MAX_DURATION = timedelta(days=3)

#: Eventbrite's own taxonomy: category 102 is Science & Technology, subcategory 2004 High Tech.
EVENTBRITE_TECH_CATEGORY = "102"
EVENTBRITE_TECH_SUBCATEGORY = "2004"

#: The first format with a match wins, in this order. "other" is what is left.
FORMAT_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hackathon", ("hackathon", "hackathons", "buildathon", "buildathons", "jam")),
    (
        "conference",
        ("conference", "conferences", "summit", "conf", "devfest", "fest", "i/o extended"),
    ),
    ("demo", ("demo day", "demo days", "demo night", "demo nights", "showcase")),
    (
        "workshop",
        (
            "workshop",
            "workshops",
            "hands-on",
            "101",
            "bootcamp",
            "codelab",
            "codelabs",
        ),
    ),
    ("talk", ("talk", "talks", "fireside", "panel", "ama", "-1 to")),
    (
        "mixer",
        (
            "mixer",
            "social",
            "networking",
            "connect",
            "meet & greet",
            "meet and greet",
        ),
    ),
    ("meetup", ("meetup", "meetups", "meet-up", "community", "chapter")),
    (
        "hiring",
        (
            "job fair",
            "job fairs",
            "career fair",
            "walk-in drive",
            "walk-in drives",
            "walk-in interview",
            "walk-in interviews",
            "hiring event",
            "hiring events",
            "hiring drive",
            "recruitment drive",
            "placement drive",
            "placement drives",
        ),
    ),
)

#: One table per topic. An event may name none, or several.
TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "ai": (
        "ai",
        "ml",
        "llm",
        "llms",
        "genai",
        "gen ai",
        "generative ai",
        "agents",
        "agentic",
        "machine learning",
        "deep learning",
        "neural network",
        "neural networks",
        "gemini",
        "openai",
        "chatgpt",
        "gpt",
        "computer vision",
        "nlp",
        "mlops",
        "rag",
        "langchain",
        "prompt engineering",
    ),
    "data": (
        "data",
        "analytics",
        "database",
        "databases",
        "sql",
        "postgres",
        "postgresql",
    ),
    "cloud": (
        "cloud",
        "aws",
        "gcp",
        "serverless",
        "kubernetes",
        "k8s",
    ),
    "devops": (
        "devops",
        "sre",
        "site reliability",
        "kubernetes",
        "k8s",
        "docker",
        "terraform",
        "gitops",
        "observability",
        "ci/cd",
        "platform engineering",
    ),
    "web": (
        "frontend",
        "front end",
        "backend",
        "back end",
        "full stack",
        "fullstack",
        "react",
        "nextjs",
        "next.js",
        "vue",
        "angular",
        "svelte",
        "nodejs",
        "node.js",
        "html",
        "css",
        "api",
        "apis",
        "webassembly",
        "wasm",
        "web development",
        "web dev",
    ),
    "mobile": (
        "android",
        "ios",
        "flutter",
        "kotlin",
        "react native",
        "swiftui",
        "jetpack compose",
        "mobile app",
        "mobile apps",
        "mobile development",
    ),
    "security": (
        "security",
        "cybersecurity",
        "infosec",
        "appsec",
        "devsecops",
        "pentest",
        "penetration testing",
        "owasp",
        "ctf",
        "zero trust",
        "bug bounty",
        "encryption",
        "malware",
    ),
    "product": ("product",),
    "design": ("design", "designer", "designers", "ux", "ui", "figma"),
    "startups": (
        "startup",
        "startups",
        "founder",
        "founders",
        "saas",
        "venture capital",
        "vc",
        "fundraising",
        "y combinator",
        "pre-seed",
    ),
    "opensource": ("open source", "oss", "foss", "linux", "hacktoberfest"),
    "hardware": (
        "hardware",
        "embedded",
        "robotics",
        "robots",
        "iot",
        "semiconductor",
        "semiconductors",
        "fpga",
        "arduino",
        "raspberry pi",
        "firmware",
        "pcb",
        "physical ai",
    ),
    "web3": (
        "web3",
        "blockchain",
        "crypto",
        "cryptocurrency",
        "ethereum",
        "solana",
        "solidity",
        "defi",
        "bitcoin",
        "smart contract",
        "smart contracts",
    ),
    "quantum": ("quantum", "qiskit", "qubit", "qubits"),
    "languages": (
        "python",
        "rust",
        "golang",
        "java",
        "kotlin",
        "swift",
        "typescript",
        "javascript",
        "haskell",
        "elixir",
        "php",
        "c++",
        "c#",
    ),
    "career": (
        "career",
        "careers",
        "resume",
        "resumes",
        "mock interview",
        "mock interviews",
        "interview prep",
        "interview preparation",
        "mentorship",
        "mentoring",
        "job search",
        "freshers",
        "job fair",
        "hiring event",
        "placement drive",
        "walk-in drive",
    ),
}

#: Topics whose every word says "technology". "product", "design" and "career" are
#: not among them: a "design workshop" can be about interiors, a "career talk" about law.
_TECH_TOPICS: tuple[str, ...] = tuple(
    topic for topic in TOPICS if topic not in {"product", "design", "career"}
)

#: Technology words that name no single topic.
_GENERAL_TECH_WORDS: tuple[str, ...] = (
    "tech",
    "techie",
    "techies",
    "developer",
    "developers",
    "engineering",
    "software",
    "coding",
    "programming",
    "hackathon",
    "hackathons",
    "buildathon",
    "buildathons",
    "fintech",
    "ux",
    "figma",
    "product management",
    "product manager",
    "product managers",
    "product mixer",
    "product design",
    "product designer",
    "product designers",
)

#: Any of these in the title or snippet makes an event technology: the general words
#: and the words of every technology topic.
TECH_WORDS: tuple[str, ...] = _GENERAL_TECH_WORDS + tuple(
    word for topic in _TECH_TOPICS for word in TOPIC_WORDS[topic]
)

#: Paid courses are not events: these words exclude only when the event is known not to be free.
PAID_COURSE_WORDS: tuple[str, ...] = (
    "certification",
    "course",
    "courses",
    "training program",
    "training programs",
    "training programme",
    "masterclass",
    "bootcamp",
)

#: (reason, words). The first pattern with a match is the reason; the order is the
#: order they are tried in.
EXCLUDE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "free demo class",
        (
            "free demo class",
            "free demo classes",
            "free demo session",
            "demo class",
            "demo classes",
        ),
    ),
    (
        "placement guarantee",
        (
            "placement guarantee",
            "placement guaranteed",
            "guaranteed placement",
            "job guarantee",
            "100% placement",
        ),
    ),
    ("trading", ("trading", "forex", "stock market", "share market", "intraday")),
    (
        "astrology",
        ("astrology", "astrologer", "astrologers", "horoscope", "vastu", "numerology", "tarot"),
    ),
    (
        "network marketing",
        (
            "network marketing",
            "mlm",
            "multi-level marketing",
            "multilevel marketing",
            "direct selling",
        ),
    ),
    ("dating", ("speed dating", "singles", "blind date")),
)

#: Excluded only when no technology word is present.
WELLNESS_WORDS: tuple[str, ...] = (
    "yoga",
    "meditation",
    "wellness",
    "mindfulness",
    "reiki",
    "sound healing",
    "breathwork",
    "pranayama",
)

#: Asia/Calcutta is the old name tz databases still serve for Asia/Kolkata.
_INDIA_TIMEZONES = frozenset({"Asia/Kolkata", "Asia/Calcutta"})
_INDIA_WORDS: tuple[str, ...] = ("india", "bengaluru", "bangalore")


# ── matching ─────────────────────────────────────────────────────────────────

# A space or hyphen between two word characters inside a table entry matches either.
_BETWEEN_WORDS = re.compile(r"(?<=\w)[\s-]+(?=\w)")
# The idiom "of course" says nothing about a course.
_OF_COURSE = re.compile(r"\bof\s+course\b", re.IGNORECASE)
# "IST" is only India's time zone in capitals; in lower case it is German for "is".
_IST = re.compile(r"(?<!\w)IST(?!\w)")


def _alternative(word: str) -> str:
    return r"[\s-]+".join(re.escape(part) for part in _BETWEEN_WORDS.split(word))


def _compile(words: tuple[str, ...]) -> re.Pattern[str]:
    """One whole-word, case-insensitive pattern for ``words``."""
    alternatives = "|".join(_alternative(word) for word in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)


_FORMAT_PATTERNS = tuple((name, _compile(words)) for name, words in FORMAT_WORDS)
_TOPIC_PATTERNS = {topic: _compile(words) for topic, words in TOPIC_WORDS.items()}
_TECH = _compile(TECH_WORDS)
_PAID_COURSE = _compile(PAID_COURSE_WORDS)
_EXCLUDE = tuple((reason, _compile(words)) for reason, words in EXCLUDE_PATTERNS)
_WELLNESS = _compile(WELLNESS_WORDS)
_INDIA_TEXT = _compile(_INDIA_WORDS)


def _text(facts: EventFacts) -> str:
    return f"{facts.title} {facts.snippet or ''}"


def event_format(text: str) -> str:
    """The first format in :data:`FORMAT_WORDS` order that ``text`` names, else "other"."""
    for name, pattern in _FORMAT_PATTERNS:
        if pattern.search(text):
            return name
    return "other"


def event_topics(text: str) -> tuple[str, ...]:
    """The topics ``text`` names, in :data:`TOPICS` order. An event may have none."""
    return tuple(topic for topic in TOPICS if _TOPIC_PATTERNS[topic].search(text))


# ── what: tech, exclusions ───────────────────────────────────────────────────


def _declared_tech(facts: EventFacts, scope: SourceScope) -> str | None:
    """The reason when the source itself vouches that the event is technology."""
    if facts.source == "gdg" or scope.key.lower().startswith("gdg"):
        return "GDG chapter"
    if scope.trusted_tech:
        return f"{scope.key} calendar"
    taxonomy = facts.taxonomy
    if (
        str(taxonomy.get("category", "")) == EVENTBRITE_TECH_CATEGORY
        or str(taxonomy.get("subcategory", "")) == EVENTBRITE_TECH_SUBCATEGORY
    ):
        return "Eventbrite: Science & Technology"
    return None


def is_tech(facts: EventFacts, scope: SourceScope) -> bool:
    """True when the source vouches for the event or its text names technology."""
    return _declared_tech(facts, scope) is not None or _TECH.search(_text(facts)) is not None


def excluded(facts: EventFacts) -> str | None:
    """Why the event is not an event worth listing (a paid course, a trading seminar), else None."""
    text = _OF_COURSE.sub(" ", _text(facts))
    if facts.is_free is False and _PAID_COURSE.search(text):
        return "paid course"
    for reason, pattern in _EXCLUDE:
        if pattern.search(text):
            return reason
    if _WELLNESS.search(text) and not _TECH.search(text):
        return "wellness"
    if effective_end(facts.starts_at, facts.ends_at) - as_utc(facts.starts_at) > MAX_DURATION:
        return "longer than 3 days"
    return None


# ── where ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Place:
    reason: str
    area: str | None = None


def _place_texts(facts: EventFacts) -> tuple[str, ...]:
    return tuple(text for text in (facts.address, facts.venue_name, facts.locality) if text)


def _text_names_bengaluru(text: str) -> bool:
    """An address that says Bengaluru, or names one of its areas and no other place."""
    city = resolve_city(text)
    if city is not None:
        return city == "Bengaluru"
    return area_for_text(text) is not None and not mentions_foreign_country(text)


def _area_in_texts(texts: tuple[str, ...]) -> str | None:
    return next((area for area in map(area_for_text, texts) if area is not None), None)


def _in_person_place(facts: EventFacts) -> _Place | None:
    texts = _place_texts(facts)
    if facts.lat is not None and facts.lng is not None:
        if not in_bengaluru(facts.lat, facts.lng):
            return None
        # An address that names the area outranks the nearest centroid, which is a guess.
        area = _area_in_texts(texts) or area_for_point(facts.lat, facts.lng)
    else:
        if not any(_text_names_bengaluru(text) for text in texts):
            return None
        area = _area_in_texts(texts)
    return _Place(f"in {area}" if area else "in Bengaluru", area)


def _online_place(facts: EventFacts, scope: SourceScope) -> _Place | None:
    text = _text(facts)
    india = (
        scope.india
        or (facts.timezone or "") in _INDIA_TIMEZONES
        or _INDIA_TEXT.search(text) is not None
        or _IST.search(text) is not None
    )
    return _Place("online · India") if india else None


def _place(facts: EventFacts, scope: SourceScope) -> _Place | None:
    """Where the event is, or ``None`` when it is not Bengaluru or online from India."""
    if facts.is_online:
        return _online_place(facts, scope)
    return _in_person_place(facts)


# ── the verdict ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Verdict:
    """What :func:`assess` decided, with the evidence.

    ``store``: the event is in Bengaluru, or online from India. ``listed``: it is also
    technology, worth going to and not excluded. ``reasons`` is the evidence behind
    ``listed``, in display order; it is empty when the event is not stored.
    """

    store: bool
    listed: bool
    format: str
    topics: tuple[str, ...]
    area: str | None
    reasons: tuple[str, ...]


def _host_name(facts: EventFacts) -> str:
    named = (name.strip() for name in (facts.organiser_name, *facts.hosts) if name)
    return next((name for name in named if name), "a company on the map")


def assess(
    facts: EventFacts, scope: SourceScope, *, host_is_listed_company: bool = False
) -> Verdict:
    """The relevance verdict for one event as one source states it.

    ``host_is_listed_company`` is true when the event's host resolved to a tech company
    on the company map; a hiring event is listed only then.
    """
    text = _text(facts)
    fmt = event_format(text)
    topics = event_topics(text)
    place = _place(facts, scope)
    if place is None:
        return Verdict(False, False, fmt, topics, None, ())

    declared = _declared_tech(facts, scope)
    keyword = _TECH.search(text)
    tech = declared is not None or keyword is not None
    reasons: list[str] = []
    if declared is not None:
        reasons.append(declared)
    reasons.extend(TOPIC_LABELS[topic] for topic in topics)
    if declared is None and keyword is not None and not topics:
        reasons.append(f"mentions {' '.join(keyword.group(0).lower().split())}")
    reasons.append(place.reason)
    if host_is_listed_company:
        reasons.append(f"hosted by {_host_name(facts)}")

    if facts.is_online:
        format_ok = fmt in ONLINE_FORMATS
    else:
        format_ok = fmt != "hiring" or host_is_listed_company
    listed = tech and format_ok and excluded(facts) is None
    return Verdict(True, listed, fmt, topics, place.area, tuple(reasons))

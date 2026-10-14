"""
domain/news.py
~~~~~~~~~~~~~~
What a startup-news headline says: its category, and for a funding story who
raised, how much, in which round and from whom. Pure and rule-based; the
rules were written against real titles from the four recorded feeds
(``tests/fixtures/company_map/rss_*.xml``).

* **Category** comes from the title alone: ``funding``, ``acquisition``,
  ``launch`` or ``other``. A title is ``funding`` only when a company *has*
  raised: a conjugated funding verb ("raises", "bags", "secures", "nets", ...)
  followed by an amount or a funding word, or "X invests <amount> in Y".
  Talks, plans, how-tos, questions, opinion pieces, layoffs and idioms
  ("raising the bar", "raises stakes") are never funding: a wrong funding row
  creates a company and moves its stage.
* **Subject** is the text before the funding verb, with the headline's
  framing removed (see :func:`_clean_subject`). When what is left reads like a
  person ("Former Dyson India Boss Ankit Jain") or a bare descriptor
  ("Fintech startup"), the subject is ``None`` and the caller may fall back to
  an LLM.
* **Amount** keeps the text as printed (``$10 Mn``, ``Rs 110 Cr``) and an
  approximate USD value at the fixed :data:`INR_PER_USD`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INR_PER_USD = 88.0


@dataclass(frozen=True)
class FundingFacts:
    """What a funding headline (and its snippet) states. Every field may be empty."""

    subject: str | None
    round: str | None
    amount_text: str | None
    amount_usd: float | None
    investors: tuple[str, ...]


_EMPTY = FundingFacts(subject=None, round=None, amount_text=None, amount_usd=None, investors=())

# ── amounts ──────────────────────────────────────────────────────────────────

_AMOUNT_SRC = (
    r"(?<![A-Za-z])(?P<cur>US\$|\$|USD|INR|Rs\.?|₹)\s*"
    r"(?P<num>\d+(?:,\d+)*(?:\.\d+)?)"
    r"(?:\s*(?P<unit>million|mn|m|billion|bn|b|crores?|cr|lakhs?|lacs?|k)\b)?"
)
_AMOUNT = re.compile(_AMOUNT_SRC, re.I)
_QUALIFIERS = (
    r"(?:(?:around|about|nearly|almost|over|close\s+to|up\s+to|upto|more\s+than|"
    r"an\s+additional|additional|another|fresh|new|its|an?|first|undisclosed)\s+)*"
)
_LEADING_AMOUNT = re.compile(_QUALIFIERS + _AMOUNT_SRC, re.I)
_SNIPPET_AMOUNT = re.compile(
    r"\b(?:raised|raises|secured|secures|bagged|bags|netted|nets|received|garnered|"
    r"closed|mopped\s+up|picked\s+up)\s+" + _QUALIFIERS + _AMOUNT_SRC,
    re.I,
)
_MULTIPLIERS: dict[str, float] = {
    "k": 1e3,
    "m": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "cr": 1e7,
    "crore": 1e7,
    "crores": 1e7,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
}
_INR = {"inr", "rs", "rs.", "₹"}


def _amount(match: re.Match[str]) -> tuple[str, float]:
    """The amount as printed (whitespace collapsed) and its approximate USD value."""
    printed = match.string[match.start("cur") : match.end()]
    text = re.sub(r"\s+", " ", printed).strip()
    value = float(match.group("num").replace(",", ""))
    unit = (match.group("unit") or "").lower()
    value *= _MULTIPLIERS.get(unit, 1.0)
    if match.group("cur").lower() in _INR:
        value /= INR_PER_USD
    return text, value


# ── funding headlines ────────────────────────────────────────────────────────

_VERBS = (
    r"raises|raised|bags|bagged|secures|secured|nets|netted|lands|landed|gets|got|"
    r"receives|received|picks\s+up|picked\s+up|mops\s+up|mopped\s+up|closes|closed"
)
_VERB_FIRST = re.compile(rf"^(?P<subject>.+?)\s+(?P<verb>{_VERBS})\s+(?P<rest>.+)$", re.I)
_INVESTOR_FIRST = re.compile(
    rf"^(?P<investors>.+?)\s+(?:invests?|invested)\s+{_QUALIFIERS}"
    rf"(?P<amount>{_AMOUNT_SRC})\s+(?:in|into)\s+(?P<subject>.+)$",
    re.I,
)
# A funding word must appear within the first few words after the verb when
# no amount follows it directly ("Bags Funding From BEENEXT").
_FUNDING_WORD = re.compile(
    r"\b(?:funding|funds|round|pre-?seed|seed|series|investment|backing|debt|grant|"
    r"financing)\b",
    re.I,
)
# "closes" is a deal verb as often as a funding one ("close $328 million deal
# to finance ..."): it needs a round word, an amount is not enough.
_ROUND_WORD = re.compile(r"\b(?:round|funding|series|pre-?seed|seed)\b", re.I)
_RANGE_WORDS = 6
# What follows the amount when the money is not a round: "bags Rs 1,200 Cr
# order", "gets Rs 400 Cr GST demand", "secures $1.5 Bn deal".
_NOT_A_ROUND = re.compile(
    r"^(?:\S+\s+){0,2}?(?:orders?|contracts?|deals?|tenders?|demand|notice|"
    r"penalty|fine|tax|gst|refund|revenue|sales|profit|loss|dividend|subsidy|"
    r"compensation|damages|salary|package|valuation|market\s+cap|mandate|bid)\b",
    re.I,
)

# Headline shapes that are never a completed round, whatever verb they carry.
_EDITORIAL = re.compile(
    r"^\s*(?:how|why|what|when|where|who|should|can|could|will|is|are|do|does)\b"
    r"|\?\s*$"
    r"|\b(?:opinion|op-ed|column|podcast|explainer)\b"
    r"|^\s*(?:view|analysis)\s*:",
    re.I,
)
_NEVER_FUNDING = re.compile(
    r"\b(?:layoffs?|lay\s*offs?|lays\s+off|laid\s+off)\b"
    r"|\brais(?:e|es|ed|ing)\s+(?:the\s+)?(?:bar|stakes|concerns?|questions?|eyebrows|"
    r"alarms?|red\s+flags?|hopes|doubts|voice|roof|pitch)\b",
    re.I,
)
# Words in front of the verb that make it a plan or a rumour, not a round.
_SPECULATIVE = re.compile(
    r"\b(?:in\s+talks|talks|plans?|planning|eyes|eyeing|mulls?|weighs|considers?|"
    r"set\s+to|looks?\s+to|seeks?|seeking|may|could|likely|reportedly|to)\b",
    re.I,
)


@dataclass(frozen=True)
class _Match:
    subject: str
    rest: str
    amount: re.Match[str] | None
    investors: tuple[str, ...]


def _funding_match(title: str) -> _Match | None:
    """The parts of a completed-funding headline, or ``None``."""
    text = re.sub(r"\s+", " ", title).strip()
    if not text or _EDITORIAL.search(text) or _NEVER_FUNDING.search(text):
        return None

    verb_first = _VERB_FIRST.match(text)
    if verb_first and not _SPECULATIVE.search(verb_first.group("subject")):
        rest = verb_first.group("rest")
        head = " ".join(rest.split()[:_RANGE_WORDS])
        amount = _LEADING_AMOUNT.match(rest)
        if verb_first.group("verb").lower().startswith("clos"):
            ok = bool(_ROUND_WORD.search(" ".join(rest.split()[:8])))
        elif amount is not None:
            ok = not _NOT_A_ROUND.match(rest[amount.end() :].lstrip())
        else:
            ok = bool(_FUNDING_WORD.search(head))
        if ok:
            return _Match(verb_first.group("subject"), rest, amount, ())

    investor_first = _INVESTOR_FIRST.match(text)
    if investor_first and not _SPECULATIVE.search(investor_first.group("investors")):
        amount = _AMOUNT.search(investor_first.group("amount"))
        backers = _split_names(investor_first.group("investors"))
        subject = re.split(_SUBJECT_TAIL, investor_first.group("subject"), maxsplit=1)[0]
        return _Match(subject, "", amount, backers)
    return None


# ── the subject ──────────────────────────────────────────────────────────────

_SUBJECT_TAIL = re.compile(
    r"\s+(?:ahead\s+of|to|for|as|amid|at|after|via|through|in|on)\b|\s*[,:;|(]", re.I
)
_LEAD_TAGS = re.compile(r"^(?:\s*\[[^\]]*\]\s*)+")
_LEAD_LABELS = re.compile(
    r"^(?:\s*(?:exclusive|breaking|update|funding(?:\s+alert)?|just\s+in|scoop)\s*[:|–—-]\s*)+",
    re.I,
)
_BACKED = re.compile(r"\S*\bbacked\s+", re.I)
_BASED = re.compile(r"\S+-based\s+", re.I)
_DESCRIPTOR = (
    r"start-?ups?|platforms?|company|firm|maker|providers?|brand|unicorn|marketplace|"
    r"major|giant|player|operator|manufacturer|developer"
)
_DESCRIPTOR_SPLIT = re.compile(rf"\b(?:{_DESCRIPTOR})\s+(?=\S)", re.I)
_DESCRIPTOR_LAST = re.compile(rf"\b(?:{_DESCRIPTOR})$", re.I)
_PERSON = re.compile(
    r"\b(?:former|boss|ceo|cto|coo|cfo|founders?|co-?founders?|chairman|chairperson|"
    r"promoter|entrepreneur|executives?)\b|\bex-",
    re.I,
)
_SUBJECT_STRIP = " -–—:,'\"‘’“”"


def _clean_subject(raw: str) -> str | None:
    """The company a headline names before its funding verb, or ``None``.

    Removed, in order: leading ``[Update]``-style tags and ``Exclusive:``-style
    labels; everything up to an "X-backed"/"X backed" ("KL Rahul-backed
    Boldfit" → "Boldfit"); everything up to a "<place>-based"; and a leading
    descriptor ending in startup, platform, company, firm, maker, provider,
    brand, unicorn, marketplace, major, giant, player, operator, manufacturer
    or developer ("Agentic AI Startup RevRag.AI" → "RevRag.AI"), but only when
    a name follows it ("Urban Company" stays whole). A subject that names a
    person, ends in a descriptor, holds a comma, or runs past 6 words or 60
    characters is ``None``.
    """
    subject = _LEAD_TAGS.sub("", raw)
    subject = _LEAD_LABELS.sub("", subject).strip()
    if _PERSON.search(subject):
        return None
    subject = _BACKED.split(subject)[-1]
    subject = _BASED.split(subject)[-1]
    subject = _DESCRIPTOR_SPLIT.split(subject)[-1]
    subject = subject.strip(_SUBJECT_STRIP)
    if (
        not subject
        or len(subject) > 60
        or len(subject.split()) > 6
        or any(ch in subject for ch in ",;:|")
        or not subject[0].isalnum()
        or _DESCRIPTOR_LAST.search(subject)
    ):
        return None
    return subject


# ── investors ────────────────────────────────────────────────────────────────

_TITLE_INVESTORS = re.compile(
    r"\b(?:co-led\s+by|led\s+by|with\s+participation\s+from|participation\s+(?:from|of)|"
    r"backed\s+by|from)\s+",
    re.I,
)
_SNIPPET_INVESTORS = re.compile(
    r"\b(?:round|funding|investment|raise|tranche)\b[^.;]{0,40}?\b(?:co-led|led)\s+by\s+"
    r"|\bparticipation\s+(?:from|of)\s+"
    r"|\b(?:funding|investment|capital|backing|round)\s+from\s+",
    re.I,
)
_LIST_END = re.compile(
    r"[.;:()\[\]|]|\s[-–—]\s|,?\s+(?:to|for|in|as|at|amid|along|alongside|with|which|who|"
    r"while|among|this|that|valuing|taking|bringing|via|through|on|after|ahead|besides|"
    r"including|under)\b",
    re.I,
)
_LIST_SPLIT = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*", re.I)
_NOT_A_NAME = re.compile(
    r"^(?:others?|other\s+investors|existing|several|various|many|a\s+clutch|a\s+host|"
    r"its|their|his|her|founders?|the\s+founders?)\b",
    re.I,
)
_EXISTING_PREFIX = re.compile(r"^(?:its\s+)?existing\s+investors?\s+(?=[A-Z])", re.I)


def _split_names(text: str, *, drop_last: bool = False) -> tuple[str, ...]:
    names: list[str] = []
    parts = _LIST_SPLIT.split(text)
    for part in parts[:-1] if drop_last else parts:
        name = _EXISTING_PREFIX.sub("", part.strip(_SUBJECT_STRIP)).strip(_SUBJECT_STRIP)
        if (
            not name
            or (not name[0].isupper() and not name[0].isdigit())
            or _NOT_A_NAME.match(name)
            or len(name.split()) > 6
        ):
            continue
        names.append(name)
    return tuple(names)


_TRUNCATED = re.compile(r"\s*(?:…|\.{3,})\s*$")
_TRAILING_SEP = re.compile(r"(?:\s*,|\s+and|\s*&)\s*$", re.I)


def _investors_after(pattern: re.Pattern[str], text: str) -> list[str]:
    """Names listed after each match of ``pattern``.

    Feeds cut descriptions mid-word ("led by Nexus Venture…"). A list that
    runs into that cut loses its last name, unless the cut fell right after a
    separator ("co-led by Avaana Capital and…"), where every name is whole.
    """
    cut = _TRUNCATED.search(text)
    body = text[: cut.start()] if cut else text
    found: list[str] = []
    for match in pattern.finditer(body):
        tail = body[match.end() :]
        end = _LIST_END.search(tail)
        segment = tail[: end.start()] if end else tail
        drop_last = False
        if cut and end is None:
            trimmed = _TRAILING_SEP.sub("", segment)
            drop_last = trimmed == segment
            segment = trimmed
        found.extend(_split_names(segment, drop_last=drop_last))
    return found


def _dedupe(names: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return tuple(out)


# ── rounds ───────────────────────────────────────────────────────────────────

_ROUND = re.compile(
    r"\b(?:(?P<preseed>pre[- ]?seed)|pre[- ]series[- ]?(?P<pre>[a-k])|series[- ]?(?P<series>[a-k])"
    r"|(?P<seed>seed)|(?P<bridge>bridge)|(?P<debt>debt))\b",
    re.I,
)


def _round(text: str | None) -> str | None:
    """The round a text names, as displayed ("Series A", "Pre-seed", "Debt")."""
    if not text:
        return None
    match = _ROUND.search(text)
    if not match:
        return None
    if match.group("preseed"):
        return "Pre-seed"
    if match.group("pre"):
        return f"Pre-Series {match.group('pre').upper()}"
    if match.group("series"):
        return f"Series {match.group('series').upper()}"
    if match.group("seed"):
        return "Seed"
    if match.group("bridge"):
        return "Bridge"
    return "Debt"


# ── public API ───────────────────────────────────────────────────────────────

_ACQUISITION = re.compile(
    r"\b(?:acquires?|acquired|acquiring|acquisition\s+of|acqui-?hires?|acqui-?hired|"
    r"to\s+buy|buys|bought|merges\s+with|merged\s+with|takes\s+over|snaps\s+up)\b",
    re.I,
)
# Buying a minority stake, shares or bonds is an investment, not an acquisition.
_STAKE = re.compile(
    r"^\s+(?:an?\s+|nearly\s+|about\s+|around\s+)?(?:\d+(?:\.\d+)?%\s+)?(?:minority\s+)?"
    r"(?:stakes?|shares|bonds)\b",
    re.I,
)
_LAUNCH = re.compile(
    r"\b(?:launches|launched|unveils|unveiled|rolls\s+out|rolled\s+out|debuts|"
    r"introduces|introduced)\b",
    re.I,
)


def classify(title: str) -> str:
    """``funding``, ``acquisition``, ``launch`` or ``other``, from the title alone."""
    if _funding_match(title):
        return "funding"
    text = re.sub(r"\s+", " ", title).strip()
    if _EDITORIAL.search(text):
        return "other"
    for match in _ACQUISITION.finditer(text):
        if not _STAKE.match(text[match.end() :]):
            return "acquisition"
    if _LAUNCH.search(text):
        return "launch"
    return "other"


def extract_funding(title: str, snippet: str | None) -> FundingFacts:
    """Subject, round, amount and investors of a funding headline.

    Everything comes from the title first; the snippet only fills a missing
    amount (after a funding verb), a missing round, and adds investors named
    after "round ... led by", "participation from" or "funding from". A title
    that is not funding yields empty facts.
    """
    match = _funding_match(title)
    if match is None:
        return _EMPTY

    amount_text: str | None = None
    amount_usd: float | None = None
    amount = match.amount
    if amount is None and snippet:
        amount = _SNIPPET_AMOUNT.search(snippet)
    if amount is not None:
        amount_text, amount_usd = _amount(amount)

    investors = [*match.investors, *_investors_after(_TITLE_INVESTORS, match.rest)]
    if snippet:
        investors.extend(_investors_after(_SNIPPET_INVESTORS, snippet))

    return FundingFacts(
        subject=_clean_subject(match.subject),
        round=_round(title) or _round(snippet),
        amount_text=amount_text,
        amount_usd=amount_usd,
        investors=_dedupe(investors),
    )


_FOUNDED_BY = re.compile(r"\b(?i:co-?founded|founded)(?:\s+(?i:in)\s+\d{4})?\s+(?i:by)\s+")
_NAME_TOKEN = r"(?!(?:And|In|The|Of|At|For|With|From|On|To|By|As)\b)(?:[A-Z]\.|[A-Z][a-zA-Z'’-]+)"
_NAME = re.compile(rf"{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,3}}")
_NAME_SEP = re.compile(r"\s*,\s*(?:and\s+)?|\s+and\s+|\s*&\s*")
_ACRONYM = re.compile(r"\b[A-Z]{2,}\b")
# "founded by IIT Madras alumni", "founded by Google Cloud veterans": a group, not people.
_GROUP_NOUN = re.compile(
    r"\s+(?:alumni|graduates|engineers|executives|veterans|professionals|students|team|"
    r"brothers|sisters|siblings|friends|couple|employees|ex-employees|duo|trio|group|"
    r"members|leaders|bankers|doctors|researchers|techies|founders)\b"
)


def founders_from_text(text: str | None) -> list[str]:
    """Founders named after "founded by", "co-founded by" or "founded in 2018 by".

    Each name is 2-4 capitalised words. A single word ("founded by Google"), a
    lower-case phrase ("founded by former Flipkart executives"), an acronym
    ("IIT Madras") or a name followed by a group noun ("Google Cloud
    veterans") names nobody.
    """
    if not text:
        return []
    found: list[str] = []
    for match in _FOUNDED_BY.finditer(text):
        pos = match.end()
        while True:
            name = _NAME.match(text, pos)
            if not name or _ACRONYM.search(name.group(0)) or _GROUP_NOUN.match(text, name.end()):
                break
            found.append(name.group(0))
            sep = _NAME_SEP.match(text, name.end())
            if not sep:
                break
            pos = sep.end()
    return list(_dedupe(found))

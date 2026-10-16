"""
scripts/fixture_privacy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Keeps real people out of recorded fixtures.

A source feed names hosts, organisers and sometimes a speaker. A fixture is
committed, so ``scripts/capture_fixtures.py`` passes every iCal feed through
:func:`pseudonymise_ics` before it is written: nobody's real name or address
reaches the repository, and the tests read the same shape of data.

Standard library only, so a test can import it without pulling in the scrapers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

#: Communities, companies and venues whose names are public, so a recording may
#: keep them. Everything else a feed names as a host or an organiser is taken
#: to be a person. Compared case-folded.
PUBLIC_ORGANISATIONS: frozenset[str] = frozenset(
    name.casefold()
    for name in (
        "Team Lossfunk",
        "Swift Bengaluru",
        "South Park Commons",
        "AI Garage",
        "Lottie Community",
        "Paytm Community",
        "QuantumX",
        "Edit Lobby",
        "The Product Folks",
        "The Product Folks 🚀",
        "The Product Folks x Ether by Zomato",
    )
)

PLACEHOLDER_EMAIL = "organiser@example.com"

_FOLD = re.compile(r"\r?\n[ \t]")
_NEWLINE = re.compile(r"\r?\n")
_ORGANIZER_CN = re.compile(r'^ORGANIZER[^:]*?;CN=(?:"([^"]*)"|([^;:]*))', re.IGNORECASE)
_HOSTED_BY = re.compile(r"Hosted by (.+?)(?:\\n|$)")
_HOST_SEPARATOR = re.compile(r"\s*(?:\\,|,)\s*|\s+&\s+")
_OTHERS_COUNT = re.compile(r"\d+\s+others?", re.IGNORECASE)
_MAILTO = re.compile(r'(mailto:)[^\s;"]+', re.IGNORECASE)
# A link's host and its path; text ends at white space or the backslash of an escape like "\n".
_LINK_PATH = re.compile(r"(https?://[^/\s\\]+)(/[^\s\\]*)")
_PSEUDONYM = re.compile(r"Host \d+ Quill")
_FOLD_AT = 75  # octets in a content line
_NAME_PART_MIN = 4  # a shorter first name is too likely to be an ordinary word


def _unfold(text: str) -> list[str]:
    return _NEWLINE.split(_FOLD.sub("", text).rstrip("\r\n"))


def _fold(line: str) -> str:
    """``line`` in chunks of at most 75 octets, never splitting a UTF-8 character."""
    chunks: list[str] = []
    chunk: list[str] = []
    size = 0
    limit = _FOLD_AT
    for char in line:
        octets = len(char.encode("utf-8"))
        if size + octets > limit:
            chunks.append("".join(chunk))
            chunk, size, limit = [], 0, _FOLD_AT - 1  # a fold costs the next line one octet
        chunk.append(char)
        size += octets
    chunks.append("".join(chunk))
    return "\n ".join(chunks)


def event_people(text: str) -> list[str]:
    """Every organiser and "Hosted by" name in an iCal feed, once each, in feed order."""
    names: list[str] = []
    for line in _unfold(text):
        organiser = _ORGANIZER_CN.match(line)
        if organiser is not None:
            quoted, bare = organiser.groups()
            names.append((quoted if quoted is not None else bare).strip())
        for hosted in _HOSTED_BY.finditer(line):
            for part in _HOST_SEPARATOR.split(hosted.group(1)):
                if _OTHERS_COUNT.fullmatch(part.strip()) is None:
                    names.append(part.strip())
    seen: set[str] = set()
    unique: list[str] = []
    for name in names:
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            unique.append(name)
    return unique


def _word_pattern(words: Iterable[str], *, ignore_case: bool) -> re.Pattern[str]:
    """Whole-word alternation of ``words``, longest first so a full name beats its first name.

    Feed text keeps its line breaks as the two characters ``\\n``, so a name that opens a
    line follows an "n" that is not part of it: ``\\n`` counts as a word edge.
    """
    ordered = sorted(set(words), key=len, reverse=True)
    body = "|".join(re.escape(word) for word in ordered)
    return re.compile(
        rf"(?:(?<!\w)|(?<=\\[nN]))(?:{body})(?!\w)", re.IGNORECASE if ignore_case else 0
    )


def pseudonymise_ics(text: str, *, extra_names: Sequence[str] = ()) -> str:
    """``text`` with every person's name and every e-mail address replaced.

    The people are the organisers and the "Hosted by" names, plus ``extra_names``
    (people only a description mentions, which no rule can find). Each gets a
    fictional name, "Host 1 Quill", "Host 2 Quill" and so on in order of
    appearance, so the same input always gives the same output. The rename is
    case-insensitive and runs over the whole file, titles and descriptions
    included, across the 75-octet folds a long line is split by; a person's
    first name on its own is renamed too ("Host 1"), and a part of a name in a
    link's path becomes "host". Organisations in :data:`PUBLIC_ORGANISATIONS`
    and venues stay. Every ``mailto:`` address becomes
    :data:`PLACEHOLDER_EMAIL`. Names already pseudonymised are left, so running
    it on its own output changes nothing.
    """
    people: dict[str, str] = {}  # name, case-folded -> its pseudonym
    spelling: dict[str, str] = {}  # name, case-folded -> the name as written
    for raw in [*event_people(text), *extra_names]:
        name = raw.strip()
        key = name.casefold()
        if (
            name
            and key not in people
            and key not in PUBLIC_ORGANISATIONS
            and _PSEUDONYM.fullmatch(name) is None
        ):
            people[key] = f"Host {len(people) + 1} Quill"
            spelling[key] = name

    organisation_words = {word for org in PUBLIC_ORGANISATIONS for word in org.split()}
    first_names: dict[str, str] = {}  # first name as written -> "Host N"
    for number, name in enumerate(spelling.values(), start=1):
        first = name.split()[0] if " " in name else ""
        if len(first) >= _NAME_PART_MIN and first.casefold() not in organisation_words:
            first_names.setdefault(first, f"Host {number}")
    name_parts = {
        part.casefold()
        for name in spelling.values()
        for part in re.split(r"\W+", name)
        if len(part) >= _NAME_PART_MIN
    }

    lines = _unfold(text)
    if people:
        full = _word_pattern(people, ignore_case=True)
        lines = [full.sub(lambda m: people[m.group(0).casefold()], line) for line in lines]
    if first_names:
        # Case-sensitive, as written: "May" the month is not a person.
        alone = _word_pattern(first_names, ignore_case=False)
        lines = [alone.sub(lambda m: first_names[m.group(0)], line) for line in lines]
    if name_parts:
        parts = "|".join(map(re.escape, sorted(name_parts)))
        in_slug = re.compile(rf"(?<![A-Za-z0-9])(?:{parts})(?![A-Za-z0-9])", re.IGNORECASE)
        # A link's path may carry a name too ("/binny"): "host" keeps it a valid slug.
        lines = [
            _LINK_PATH.sub(lambda m: m.group(1) + in_slug.sub("host", m.group(2)), line)
            for line in lines
        ]
    lines = [_MAILTO.sub(rf"\g<1>{PLACEHOLDER_EMAIL}", line) for line in lines]
    return "".join(_fold(line) + "\n" for line in lines)

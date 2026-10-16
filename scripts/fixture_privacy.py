"""
scripts/fixture_privacy.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Keeps real people out of recorded fixtures.

A source feed names hosts, organisers and sometimes a speaker. A fixture is
committed, so ``scripts/capture_fixtures.py`` passes every iCal feed through
:func:`pseudonymise_ics` before it is written: nobody's real name or address
reaches the repository, and the tests read the same shape of data.

A GDG page names far more people (the chapter team and its avatars, speakers,
organisers), so it is not pseudonymised but cut down: :func:`reduce_gdg_chapter`
and :func:`reduce_gdg_event` keep only the fields ``services/events/gdg.py``
reads, and rename every person in what is kept. :func:`bare_json_page` writes
that back as a minimal page, and :func:`person_names`, :func:`personal_keys` and
:func:`emails_in` are the checks the contract tests run over the result.

Standard library only, so a test can import it without pulling in the scrapers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

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
        "Google Developer Groups",
        "GDG Bangalore",
        "GDG Cloud Bengaluru",
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


# ── GDG pages ────────────────────────────────────────────────────────────────

#: The chapter fields a recording keeps: where the chapter is, and nothing about who runs it.
_GDG_CHAPTER_KEEP = (
    "id",
    "title",
    "chapter_location",
    "city",
    "state",
    "country",
    "country_name",
    "is_city",
    "is_active",
    "latitude",
    "longitude",
    "url",
)
#: The fields of a listed event that the parser reads, plus the chapter it belongs to.
_GDG_LISTING_KEEP = (
    "chapter_id",
    "title",
    "start_date",
    "url",
    "event_type_title",
    "description_short",
)
_GDG_LISTS = ("upcomingEvents", "pastEvents")
_EVENT_URL = re.compile(r"(https://gdg\.community\.dev/events/details/)([a-z0-9-]+)(/?)")
#: schema.org properties that hold a person or an organisation.
_ROLE_KEYS = frozenset(
    {
        "organizer",
        "performer",
        "attendee",
        "speaker",
        "author",
        "creator",
        "contributor",
        "founder",
        "member",
        "employee",
    }
)
#: Keys that mark a personal record, a photo or a profile; a recording has none of them.
PERSONAL_KEYS = frozenset(
    {
        "chapterTeam",
        "user",
        "avatar",
        "cropped_avatar_url",
        "photo",
        "image",
        "email",
        "telephone",
        "first_name",
        "last_name",
        "given_name",
        "family_name",
        "givenName",
        "familyName",
        "sameAs",
        "linkedin_page",
        "twitter_handle",
        "instagram_handle",
        "facebook_page",
        "flickr_page",
        "attendee",
    }
)
_ORGANISATION_SUFFIXES = ("Organization", "Corporation", "LocalBusiness", "NGO")
_EMAIL = re.compile(r"(?:mailto:\s*)?[\w.+'-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
_BARE_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_SLUG_NAME_PART_MIN = 3  # a slug has no capital letters to tell a name from a word; be stricter
_JSON_SCRIPT = re.compile(
    r'<script\b[^>]*\btype="application/(?:ld\+)?json"[^>]*>(.*?)</script>', re.DOTALL
)


class _People:
    """The people a page names, each given a fictional name, and the renaming that follows.

    Pseudonyms run "Host 1 Quill", "Host 2 Quill" in the order the people are added, so the
    same input always gives the same output. A name that is already a pseudonym or a public
    organisation is not a person here, which keeps the reducers idempotent.
    """

    def __init__(self, names: Iterable[str] = ()) -> None:
        self._pseudonyms: dict[str, str] = {}  # full name, case-folded -> its pseudonym
        self._written: list[str] = []  # the names as written
        self._rename: tuple[re.Pattern[str] | None, re.Pattern[str] | None] | None = None
        self._first: dict[str, str] = {}
        self._slug_parts: re.Pattern[str] | None = None
        for name in names:
            self.add(name)

    def add(self, raw: str) -> None:
        name = " ".join(raw.split())
        key = name.casefold()
        if (
            name
            and key not in self._pseudonyms
            and key not in PUBLIC_ORGANISATIONS
            and _PSEUDONYM.fullmatch(name) is None
        ):
            self._pseudonyms[key] = f"Host {len(self._pseudonyms) + 1} Quill"
            self._written.append(name)
            self._rename = None
            self._slug_parts = None

    def name(self, raw: str) -> str:
        """The pseudonym of ``raw``, or ``raw`` itself when it is not a person to hide."""
        key = " ".join(raw.split()).casefold()
        return self._pseudonyms.get(key, raw)

    def _compile(self) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
        if self._rename is None:
            organisation_words = {w for org in PUBLIC_ORGANISATIONS for w in org.split()}
            self._first = {}
            for number, name in enumerate(self._written, start=1):
                first = name.split()[0] if " " in name else ""
                if len(first) >= _NAME_PART_MIN and first.casefold() not in organisation_words:
                    self._first.setdefault(first, f"Host {number}")
            full = _word_pattern(self._pseudonyms, ignore_case=True) if self._pseudonyms else None
            alone = _word_pattern(self._first, ignore_case=False) if self._first else None
            self._rename = (full, alone)
        return self._rename

    def text(self, value: str) -> str:
        """``value`` with every e-mail address and every person's name replaced."""
        value = _EMAIL.sub(PLACEHOLDER_EMAIL, value)
        full, alone = self._compile()
        if full is not None:
            value = full.sub(lambda m: self._pseudonyms[m.group(0).casefold()], value)
        if alone is not None:
            value = alone.sub(lambda m: self._first[m.group(0)], value)
        return value

    def slug(self, value: str) -> str:
        """``value`` with each part of a person's name, as a whole word, replaced by "host"."""
        if self._slug_parts is None:
            parts = {
                part.casefold()
                for name in self._written
                for part in re.split(r"\W+", name)
                if len(part) >= _SLUG_NAME_PART_MIN
            }
            if not parts:
                return value
            body = "|".join(map(re.escape, sorted(parts, key=len, reverse=True)))
            self._slug_parts = re.compile(
                rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", re.IGNORECASE
            )
        return self._slug_parts.sub("host", value)


def scrub_gdg_slug(slug: str, people: Sequence[str]) -> str:
    """``slug`` with each part of any of ``people``'s names replaced by "host"; still a valid slug."""
    return _People(people).slug(slug)


def _scrub_event_url(url: str, people: _People) -> str:
    """An event address with a person's name taken out of its slug; any other string is unchanged."""
    match = _EVENT_URL.fullmatch(url)
    if match is None:
        return url
    return f"{match.group(1)}{people.slug(match.group(2))}{match.group(3)}"


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def gdg_chapter_people(blob: object) -> list[str]:
    """The names of the chapter team a chapter page lists, in page order, once each."""
    team = _dict(_dict(_dict(_dict(blob).get("props")).get("pageProps")).get("prerenderData")).get(
        "chapterTeam"
    )
    names: list[str] = []
    for member in team if isinstance(team, list) else []:
        user = _dict(_dict(member).get("user"))
        parts = (user.get("first_name"), user.get("last_name"))
        name = " ".join(" ".join(p.split()) for p in parts if isinstance(p, str) and p.strip())
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    return names


def _scrub_listing(entry: object, people: _People) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    kept: dict[str, Any] = {}
    for key in _GDG_LISTING_KEEP:
        if key not in entry:
            continue
        value = entry[key]
        if isinstance(value, str):
            value = _scrub_event_url(value, people) if key == "url" else people.text(value)
        kept[key] = value
    return kept


def reduce_gdg_chapter(blob: object, *, redact: Sequence[str] = ()) -> dict[str, Any]:
    """A chapter page's Next.js data cut down to what the parser reads, with no person in it.

    Kept: the chapter's name and place, and each listed event's title, start, address, type
    and one-line description. Dropped: the team, sponsors, page blocks, social links, tracking
    query and every image. The team's names (and ``redact``'s, for people only the prose
    mentions) are replaced wherever the kept text names them, in a link's slug as "host";
    every e-mail address becomes :data:`PLACEHOLDER_EMAIL`. ``ValueError`` when the data has
    no ``upcomingEvents.results``: a recording without one proves nothing.
    """
    page = _dict(_dict(blob).get("props")).get("pageProps")
    prerender = _dict(page).get("prerenderData") if isinstance(page, dict) else None
    upcoming = _dict(prerender).get("upcomingEvents") if isinstance(prerender, dict) else None
    if not isinstance(upcoming, dict) or not isinstance(upcoming.get("results"), list):
        raise ValueError("no props.pageProps.prerenderData.upcomingEvents.results in the page data")
    assert isinstance(page, dict) and isinstance(prerender, dict)  # narrowed by the check above

    people = _People([*gdg_chapter_people(blob), *redact])
    chapter = _dict(page.get("chapterData"))
    lists: dict[str, Any] = {}
    for kind in _GDG_LISTS:
        listed = prerender.get(kind)
        if not isinstance(listed, dict) or not isinstance(listed.get("results"), list):
            continue
        results = [_scrub_listing(entry, people) for entry in listed["results"]]
        block: dict[str, Any] = {}
        if isinstance(listed.get("count"), int):
            block["count"] = listed["count"]
        block["results"] = [entry for entry in results if entry is not None]
        lists[kind] = block
    slug = page.get("chapterSlug")
    return {
        "props": {
            "pageProps": {
                "chapterSlug": slug if isinstance(slug, str) else None,
                "chapterData": {
                    key: people.text(chapter[key])
                    if isinstance(chapter[key], str)
                    else chapter[key]
                    for key in _GDG_CHAPTER_KEEP
                    if key in chapter
                },
                "prerenderData": lists,
            }
        }
    }


def _types(node: dict[str, Any]) -> list[str]:
    kinds = node.get("@type")
    return [
        kind for kind in (kinds if isinstance(kinds, list) else [kinds]) if isinstance(kind, str)
    ]


def _is_organisation(node: dict[str, Any]) -> bool:
    return any(kind.endswith(_ORGANISATION_SUFFIXES) for kind in _types(node))


def _entries(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else [value]


def gdg_event_people(block: dict[str, Any]) -> list[str]:
    """Every person named under an organizer, performer, attendee… key, in key order."""
    names: list[str] = []
    for key, value in block.items():
        if key not in _ROLE_KEYS:
            continue
        for entry in _entries(value):
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict) and not _is_organisation(entry):
                name = entry.get("name")
                if isinstance(name, str):
                    names.append(name)
    return names


def _scrub_role(value: object, people: _People) -> Any:
    """An organizer or performer value: a person keeps only a pseudonym, an organisation its name."""
    reduced: list[Any] = []
    for entry in _entries(value):
        if isinstance(entry, str):
            reduced.append({"@type": "Person", "name": people.name(entry)})
        elif isinstance(entry, dict) and _is_organisation(entry):
            kept = _typed(entry)
            if isinstance(entry.get("name"), str):
                kept["name"] = people.text(entry["name"])
            if isinstance(entry.get("url"), str):
                kept["url"] = people.text(entry["url"])
            reduced.append(kept)
        elif isinstance(entry, dict):
            person: dict[str, Any] = {"@type": "Person"}
            if isinstance(entry.get("name"), str) and entry["name"].strip():
                person["name"] = people.name(entry["name"])
            reduced.append(person)
    if not reduced:
        return None
    return reduced if isinstance(value, list) else reduced[0]


_ADDRESS_KEEP = (
    "streetAddress",
    "addressLocality",
    "postalCode",
    "addressRegion",
    "addressCountry",
)


def _typed(node: dict[str, Any]) -> dict[str, Any]:
    """A new dict that carries ``node``'s ``@type``, when it has one."""
    return {"@type": node["@type"]} if "@type" in node else {}


def _scrub_place(place: object, people: _People) -> dict[str, Any] | None:
    if not isinstance(place, dict):
        return None
    kept = _typed(place)
    if "VirtualLocation" in _types(place):
        return kept  # the join link may carry a meeting passcode
    if isinstance(place.get("name"), str):
        kept["name"] = people.text(place["name"])
    address = place.get("address")
    if isinstance(address, str):
        kept["address"] = people.text(address)
    elif isinstance(address, dict):
        kept["address"] = _typed(address) | {
            key: people.text(address[key])
            for key in _ADDRESS_KEEP
            if isinstance(address.get(key), str)
        }
    return kept


def _scrub_offer(offer: object, people: _People) -> dict[str, Any] | None:
    if not isinstance(offer, dict):
        return None
    kept = _typed(offer)
    if isinstance(offer.get("url"), str):
        kept["url"] = people.text(offer["url"])
    return kept


def _scrub_each(value: object, scrub: Callable[[object], dict[str, Any] | None]) -> Any:
    """``scrub`` over one value or a list of them, keeping the container; ``None`` if nothing is left."""
    reduced = [r for r in (scrub(entry) for entry in _entries(value)) if r is not None]
    if not reduced:
        return None
    return reduced if isinstance(value, list) else reduced[0]


def reduce_gdg_event(block: object, *, redact: Sequence[str] = ()) -> dict[str, Any]:
    """An event page's schema.org ``Event`` cut down to what the parser reads, with no person in it.

    Kept: name, dates, description, status, attendance mode, the venue and its postal address,
    the registration link, and the organiser and performer as organisations or as pseudonyms.
    Dropped: every other property (images, ratings, attendees…) and anything a person carries
    beyond a name (link, e-mail, telephone, photo, profiles). Every person named under an
    organizer, performer or attendee key, and every name in ``redact``, is replaced wherever the
    kept text names them. ``ValueError`` when ``block`` is not an ``Event``.
    """
    if not isinstance(block, dict) or not any(kind.endswith("Event") for kind in _types(block)):
        raise ValueError("not a schema.org Event block")
    people = _People([*redact, *gdg_event_people(block)])
    reduced: dict[str, Any] = {}
    for key in ("@context", "@type"):
        if key in block:
            value = block[key]
            reduced[key] = people.text(value) if isinstance(value, str) else value
    for key in (
        "name",
        "startDate",
        "endDate",
        "description",
        "eventStatus",
        "eventAttendanceMode",
    ):
        if isinstance(block.get(key), str):
            reduced[key] = people.text(block[key])
    for key in ("organizer", "performer"):
        if key in block:
            role = _scrub_role(block[key], people)
            if role is not None:
                reduced[key] = role
    if "location" in block:
        location = _scrub_each(block["location"], lambda entry: _scrub_place(entry, people))
        if location is not None:
            reduced["location"] = location
    if "offers" in block:
        offers = _scrub_each(block["offers"], lambda entry: _scrub_offer(entry, people))
        if offers is not None:
            reduced["offers"] = offers
    return reduced


def bare_json_page(blob: object, *, script_type: str, script_id: str | None = None) -> bytes:
    """``blob`` as the one script of a bare page: ``<html><body><script>…</script></body></html>``.

    ``<`` is written as the JSON escape ``\\u003c``, so no string in the data can close the
    script early or open a tag, and the page reads back to the same data.
    """
    body = json.dumps(blob, indent=1, ensure_ascii=False).replace("<", "\\u003c")
    ident = f' id="{script_id}"' if script_id else ""
    return f'<html><body><script type="{script_type}"{ident}>{body}</script></body></html>'.encode()


def embedded_json(html: bytes) -> list[object]:
    """The JSON of every ``application/json`` and ``application/ld+json`` script, in page order."""
    text = html.decode("utf-8", errors="replace")
    return [json.loads(match.group(1)) for match in _JSON_SCRIPT.finditer(text)]


def person_names(node: object) -> list[str]:
    """Every person's name in parsed JSON: a ``Person``, an organizer/performer/… that is not an
    organisation, and a record with ``first_name`` / ``last_name``. In document order."""
    names: list[str] = []

    def walk(value: object, in_role: bool) -> None:
        if isinstance(value, dict):
            person = "Person" in _types(value) or (in_role and not _is_organisation(value))
            name = value.get("name")
            if person and isinstance(name, str) and name.strip():
                names.append(name)
            parts = (value.get("first_name"), value.get("last_name"))
            full = " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
            if full:
                names.append(full)
            for key, child in value.items():
                walk(child, key in _ROLE_KEYS)
        elif isinstance(value, list):
            for child in value:
                walk(child, in_role)

    walk(node, False)
    return names


def personal_keys(node: object) -> list[str]:
    """Every key in parsed JSON that marks a personal record, photo or profile (:data:`PERSONAL_KEYS`)."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, child in node.items():
            if key in PERSONAL_KEYS:
                found.append(key)
            found.extend(personal_keys(child))
    elif isinstance(node, list):
        for child in node:
            found.extend(personal_keys(child))
    return found


def emails_in(node: object) -> list[str]:
    """Every e-mail address in the strings and keys of parsed JSON, in document order."""
    found: list[str] = []
    if isinstance(node, str):
        found.extend(_BARE_EMAIL.findall(node))
    elif isinstance(node, dict):
        for key, child in node.items():
            found.extend(_BARE_EMAIL.findall(key))
            found.extend(emails_in(child))
    elif isinstance(node, list):
        for child in node:
            found.extend(emails_in(child))
    return found

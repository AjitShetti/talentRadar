"""
services/company_map/website.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
What a company's own homepage says about it.

``extract_site_facts`` is pure: one HTML string in, a ``SiteFacts`` out. It
reads the meta description, the visible text, a Bengaluru address and the
people the page itself names next to a founder/CEO/CTO/chief role. A person is
only ever taken from a profile link on the page with a name printed beside it
— nothing is guessed, and a card with a role but no readable name is skipped.

``read_site`` is the thin I/O around it. It asks robots.txt first (with the
same client), fetches the homepage with a 5 s budget and a 300 KB body cap,
accepts only ``text/html``, and reads at most one ``/contact*`` page when the
homepage printed no address. It never raises: a disallowed, failing, slow or
non-HTML site is ``None``, and the caller treats that as "no site text".
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Comment, Tag

from services.company_map.types import PersonCandidate, SiteFacts

logger = logging.getLogger(__name__)

USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"
TIMEOUT_SECONDS = 5.0
MAX_BODY_BYTES = 300 * 1024
MAX_ROBOTS_BYTES = 64 * 1024

TEXT_LIMIT = 4_000
ADDRESS_LIMIT = 300
DESCRIPTION_LIMIT = 500
TITLE_LIMIT = 200
ROLE_TITLE_LIMIT = 80
MAX_LINKS = 10

# Never part of what a visitor reads.
_NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "object")
# Menus repeat on every page and say nothing about the company.
_MENU_TAGS = ("nav",)

_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "ol",
        "p",
        "section",
        "table",
        "tbody",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

_WS = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ── address ──────────────────────────────────────────────────────────────────
_PIN = re.compile(r"\b560\d{3}\b")
_CITY = re.compile(r"\b(?:Bengaluru|Bangalore)\b", re.IGNORECASE)
# How far up from the pincode a block may grow before it is no longer "the
# address" but the whole page.
_BLOCK_TEXT_LIMIT = 2_000
_ADDRESS_LOOKBACK = 250
# "Registered Address: …", "Head office - …"
_ADDRESS_LABEL = re.compile(r"\b(?:address|office)\s*(?::|\s[-–—])\s*", re.IGNORECASE)
# A sentence end that is not an abbreviation ("Opp.", "No.", "Ltd.") or a
# visual separator.
_SENTENCE_END = re.compile(r"(?<=[a-z]{3}[.!?])\s+|\s[|•·✽]\s")
_ADDRESS_TAIL = re.compile(r"[\s,\-–]*(?:Karnataka)?(?:[\s,\-–]*India)?\.?", re.IGNORECASE)

# ── people ───────────────────────────────────────────────────────────────────
_LINKEDIN_PROFILE = re.compile(
    r"^https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#\s]+)", re.IGNORECASE
)
_X_PROFILE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_X_RESERVED = frozenset(
    {
        "intent",
        "share",
        "home",
        "i",
        "search",
        "hashtag",
        "explore",
        "settings",
        "login",
        "signup",
        "messages",
        "notifications",
        "privacy",
        "tos",
    }
)
_ROLE = re.compile(r"\b(?:co-?\s?founder|founder|ceo|cto|chief)\b", re.IGNORECASE)
_FOUNDER = re.compile(r"\b(?:co-?\s?founder|founder)\b", re.IGNORECASE)
# A card is small; anything bigger is a section that merely mentions a role.
_CARD_TEXT_LIMIT = 300
_CARD_MAX_LEVELS = 5
_NAME_SPLIT = re.compile(r"\s*[,|–—]\s*|\s+-\s+")
_NOT_A_NAME = frozenset(
    {
        "linkedin",
        "twitter",
        "x",
        "follow",
        "follow us",
        "connect",
        "profile",
        "view profile",
        "read more",
        "more",
        "team",
        "our team",
        "leadership",
        "about",
        "about us",
        "contact",
        "contact us",
        "bio",
        "website",
    }
)


# ── text helpers ─────────────────────────────────────────────────────────────


def _clean(value: str | None, limit: int) -> str:
    """One line of text: no control characters, single spaces, bounded."""
    text = _CONTROL.sub(" ", value or "")
    return _WS.sub(" ", text).strip()[:limit].strip()


def _attr(tag: Tag, name: str) -> str:
    value = tag.get(name)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return " ".join(value)


def _text_of(node: Tag) -> str:
    return _WS.sub(" ", " ".join(node.stripped_strings)).strip()


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# ── extract_site_facts pieces ────────────────────────────────────────────────


def _meta_description(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("meta"):
        if not isinstance(tag, Tag):
            continue
        if _attr(tag, "name").strip().lower() == "description":
            text = _clean(_attr(tag, "content"), DESCRIPTION_LIMIT)
            if text:
                return text
    for tag in soup.find_all("meta"):
        if not isinstance(tag, Tag):
            continue
        if _attr(tag, "property").strip().lower() == "og:description":
            text = _clean(_attr(tag, "content"), DESCRIPTION_LIMIT)
            if text:
                return text
    return None


def _title(soup: BeautifulSoup) -> str | None:
    if soup.title is None:
        return None
    return _clean(soup.title.get_text(" "), TITLE_LIMIT) or None


def _block_of(node: Tag | None) -> Tag | None:
    while node is not None and node.name not in _BLOCK_TAGS:
        node = node.parent
    return node


def _address_from_block(text: str) -> str | None:
    """The address inside one block's text, or ``None``."""
    if len(text) <= ADDRESS_LIMIT:
        return text if _PIN.search(text) and _CITY.search(text) else None
    for match in _PIN.finditer(text):
        start = max(0, match.start() - _ADDRESS_LOOKBACK)
        before = text[start : match.start()]
        labels = list(_ADDRESS_LABEL.finditer(before))
        ends = list(_SENTENCE_END.finditer(before))
        if labels:
            start += labels[-1].end()
        elif ends:
            start += ends[-1].end()
        elif start > 0:
            space = text.find(" ", start)
            start = space + 1 if 0 <= space < match.start() else start
        tail = _ADDRESS_TAIL.match(text, match.end())
        end = tail.end() if tail else match.end()
        candidate = text[start:end].strip(" ,;:-–")
        if _CITY.search(candidate) and len(candidate) <= ADDRESS_LIMIT:
            return candidate
    return None


def _whole_address(block: Tag) -> Tag:
    """Grow a one-line block (``<p>Bengaluru 560034</p>``) to the lines above it.

    An address is often one ``<p>``/``<span>`` per line. The enclosing block is
    still the address while it stays short and holds no links — a link means
    it has become the footer around the address.
    """
    while block.name != "address":
        parent = _block_of(block.parent)
        if parent is None or parent.name == "body" or parent.find("a") is not None:
            break
        if len(_text_of(parent)) > ADDRESS_LIMIT:
            break
        block = parent
    return block


def _address(root: Tag) -> tuple[str | None, str | None]:
    """The first block holding both a Bengaluru city name and a 560xxx pincode."""
    for string in root.find_all(string=_PIN):
        block = _block_of(string.parent)
        while block is not None:
            text = _text_of(block)
            if len(text) > _BLOCK_TEXT_LIMIT:
                block = None
                break
            if _CITY.search(text):
                break
            block = _block_of(block.parent)
        if block is None:
            continue
        block = _whole_address(block)
        address = _address_from_block(_text_of(block))
        if address:
            pin = _PIN.search(address)
            return address, pin.group(0) if pin else None
    return None, None


def _profile(href: str) -> tuple[str, str] | None:
    """``("linkedin" | "x", canonical_url)`` for a person's profile link."""
    linkedin = _LINKEDIN_PROFILE.match(href)
    if linkedin:
        return "linkedin", f"https://www.linkedin.com/in/{linkedin.group(1)}/"
    x = _X_PROFILE.match(href)
    if x and x.group(1).lower() not in _X_RESERVED:
        return "x", f"https://x.com/{x.group(1)}"
    return None


def _profile_links(node: Tag, base_url: str) -> Iterable[tuple[Tag, str, str]]:
    for anchor in node.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        found = _profile(urljoin(base_url, _attr(anchor, "href").strip()))
        if found:
            yield anchor, found[0], found[1]


def _card_for(anchor: Tag, platform: str, url: str, base_url: str) -> Tag | None:
    """The largest small block around ``anchor`` that holds only this person."""
    card: Tag | None = None
    node = anchor.parent
    for _ in range(_CARD_MAX_LEVELS):
        if node is None or node.name in ("body", "html", "[document]"):
            break
        if len(_text_of(node)) > _CARD_TEXT_LIMIT:
            break
        others = {
            other_url
            for _, other_platform, other_url in _profile_links(node, base_url)
            if other_platform == platform and other_url != url
        }
        if others:
            break
        card = node
        node = node.parent
    return card


def _as_name(text: str) -> str | None:
    """``text`` if it reads as a printed person's name, else ``None``."""
    candidate = _clean(text, 80)
    if _ROLE.search(candidate):
        candidate = _clean(_NAME_SPLIT.split(candidate)[0], 80)
    if not 2 <= len(candidate) <= 60 or candidate.lower() in _NOT_A_NAME:
        return None
    if _ROLE.search(candidate) or re.search(r"[\d@/:]", candidate):
        return None
    words = candidate.split(" ")
    if not 1 <= len(words) <= 5 or not all(w[0].isalpha() for w in words):
        return None
    return candidate


def _role_title(card: Tag) -> str | None:
    roles = [_clean(s, ROLE_TITLE_LIMIT + 1) for s in card.stripped_strings if _ROLE.search(s)]
    roles = [r for r in roles if len(r) <= ROLE_TITLE_LIMIT]
    return min(roles, key=len) if roles else None


@dataclass
class _Person:
    name: str
    kind: str
    title: str
    linkedin_url: str | None = None
    x_url: str | None = None


def _people(root: Tag, base_url: str) -> tuple[PersonCandidate, ...]:
    found: dict[str, _Person] = {}
    for anchor, platform, url in _profile_links(root, base_url):
        card = _card_for(anchor, platform, url, base_url)
        if card is None:
            continue
        title = _role_title(card)
        if title is None:
            continue
        name = _as_name(anchor.get_text(" "))
        if name is None:
            heading = card.find(_HEADING_TAGS)
            if isinstance(heading, Tag):
                name = _as_name(heading.get_text(" "))
        if name is None:
            continue  # never guess who a profile belongs to
        person = found.setdefault(
            name.lower(),
            _Person(name=name, kind="founder" if _FOUNDER.search(title) else "leader", title=title),
        )
        if platform == "linkedin" and person.linkedin_url is None:
            person.linkedin_url = url
        elif platform == "x" and person.x_url is None:
            person.x_url = url
    return tuple(
        PersonCandidate(
            name=p.name,
            kind=p.kind,
            title=p.title,
            linkedin_url=p.linkedin_url,
            x_url=p.x_url,
            source_url=base_url,
            source_kind="site",
        )
        for p in found.values()
    )


def _links(root: Tag, base_url: str) -> tuple[str, ...]:
    """Same-site ``/about*`` and ``/contact*`` pages, absolute, no query/fragment."""
    site = _host(base_url)
    links: list[str] = []
    for anchor in root.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        parts = urlsplit(urljoin(base_url, _attr(anchor, "href").strip()))
        if parts.scheme not in ("http", "https") or _host(parts.geturl()) != site:
            continue
        if not parts.path.lower().startswith(("/about", "/contact")):
            continue
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        if url not in links:
            links.append(url)
        if len(links) == MAX_LINKS:
            break
    return tuple(links)


def extract_site_facts(html: str, base_url: str) -> SiteFacts:
    """Read one page. Pure: no I/O, never raises on odd markup."""
    soup = BeautifulSoup(html, "lxml")
    title = _title(soup)
    description = _meta_description(soup)

    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(_NOISE_TAGS):
        if isinstance(tag, Tag):
            tag.decompose()

    root: Tag = soup.body if soup.body is not None else soup
    address, pincode = _address(root)
    people = _people(root, base_url)
    links = _links(root, base_url)

    for tag in root.find_all(_MENU_TAGS):
        if isinstance(tag, Tag):
            tag.decompose()
    text = _clean(" ".join(root.stripped_strings), TEXT_LIMIT)

    return SiteFacts(
        title=title,
        description=description,
        text=text,
        address=address,
        pincode=pincode,
        people=people,
        links=links,
    )


# ── read_site ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Fetched:
    status: int
    url: str
    body: str


def _content_type(response: httpx.Response) -> str:
    value = str(response.headers.get("content-type", ""))
    return value.split(";")[0].strip().lower()


def _is_html(response: httpx.Response) -> bool:
    return response.is_success and _content_type(response) == "text/html"


def _decode(body: bytes, encoding: str | None) -> str:
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    read_if: Callable[[httpx.Response], bool],
) -> _Fetched:
    """GET ``url``; read at most ``max_bytes`` of the body, and only if ``read_if``."""
    async with asyncio.timeout(TIMEOUT_SECONDS):
        async with client.stream(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as response:
            if not read_if(response):
                return _Fetched(status=response.status_code, url=str(response.url), body="")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) >= max_bytes:
                    break  # stop reading; leaving the block closes the stream
            return _Fetched(
                status=response.status_code,
                url=str(response.url),
                body=_decode(bytes(body[:max_bytes]), response.charset_encoding),
            )


async def _robots(client: httpx.AsyncClient, url: str) -> RobotFileParser | None:
    """The site's rules; allow-all when robots.txt is a 404; ``None`` otherwise."""
    parts = urlsplit(url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    fetched = await _fetch(
        client, robots_url, max_bytes=MAX_ROBOTS_BYTES, read_if=lambda r: r.is_success
    )
    if fetched.status == 404:
        lines: list[str] = []  # no robots.txt: no rules, everything allowed
    elif 200 <= fetched.status < 300:
        lines = fetched.body.splitlines()
    else:
        return None  # 401/403/5xx: we cannot tell, so we do not fetch
    parser = RobotFileParser(robots_url)
    parser.parse(lines)  # also marks the rules as read, which can_fetch requires
    return parser


async def _page(client: httpx.AsyncClient, url: str) -> SiteFacts | None:
    fetched = await _fetch(client, url, max_bytes=MAX_BODY_BYTES, read_if=_is_html)
    if not fetched.body:
        return None
    return extract_site_facts(fetched.body, fetched.url)


async def _read_site(client: httpx.AsyncClient, url: str) -> SiteFacts | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    rules = await _robots(client, url)
    if rules is None or not rules.can_fetch(USER_AGENT, url):
        return None
    facts = await _page(client, url)
    if facts is None or facts.address is not None:
        return facts

    contact = next(
        (link for link in facts.links if urlsplit(link).path.lower().startswith("/contact")),
        None,
    )
    if contact is None or not rules.can_fetch(USER_AGENT, contact):
        return facts
    try:
        contact_facts = await _page(client, contact)
    except Exception as exc:  # the homepage alone is still a result
        logger.info("company site contact page %s unreadable: %s", contact, type(exc).__name__)
        return facts
    if contact_facts is None or contact_facts.address is None:
        return facts
    return dataclasses.replace(facts, address=contact_facts.address, pincode=contact_facts.pincode)


async def read_site(client: httpx.AsyncClient, url: str) -> SiteFacts | None:
    """The homepage's facts, or ``None`` when disallowed, failing or not HTML."""
    try:
        return await _read_site(client, url)
    except Exception as exc:  # a scheduled step must never raise
        logger.info("company site %s unreadable: %s", url, type(exc).__name__)
        return None

"""
domain/ical.py
~~~~~~~~~~~~~~
The subset of RFC 5545 (iCalendar) that Events needs, in both directions.

* :func:`parse_ical` reads a feed (Luma publishes one per Discover city and per
  calendar) into :class:`IcalEvent` values. It unfolds lines, unescapes text,
  and understands the four ``DTSTART`` shapes a feed uses: UTC (``…Z``),
  ``TZID=Asia/Kolkata``, floating (taken as IST) and ``VALUE=DATE`` (00:00
  IST). It never raises: oversize or malformed input is an empty list, and a
  ``VEVENT`` with no ``UID`` or no readable ``DTSTART`` is skipped rather than
  given a made-up start.
* :func:`build_ics` writes the single-event file behind "Add to calendar":
  CRLF line endings, lines folded at 75 octets without splitting a UTF-8
  character, text escaped so a title cannot inject a property, times in UTC.

There is no timezone database here (see :mod:`domain.event_time`), so a
``TZID`` other than UTC and the Indian zone names is not guessed at: that
event is skipped.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time

from domain.event_time import IST, as_utc

MAX_ICAL_BYTES = 1_048_576

_PRODID = "-//TalentRadar//Events//EN"
_LINE_LIMIT = 75

_UTC_TZIDS = frozenset({"UTC", "ETC/UTC", "GMT", "ETC/GMT", "Z"})
_IST_TZIDS = frozenset({"ASIA/KOLKATA", "ASIA/CALCUTTA", "INDIA STANDARD TIME"})


@dataclass(frozen=True)
class IcalEvent:
    """One ``VEVENT``, as the feed states it. Datetimes are aware UTC."""

    uid: str
    summary: str
    description: str | None
    location: str | None
    dtstart: datetime
    dtend: datetime | None
    geo: tuple[float, float] | None
    status: str | None
    organizer_cn: str | None


# ── parsing ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Prop:
    params: dict[str, str]
    value: str


_NEWLINES = re.compile(r"\r\n|\r|\n")
_TEXT_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_DATE_ONLY = re.compile(r"[0-9]{8}")
_DATE_TIME = re.compile(r"([0-9]{8}T[0-9]{6})(Z?)")


def _unfold(text: str) -> list[str]:
    """Content lines, with RFC 5545 folds (a line break plus one space or tab) removed.

    A logical line is collected as pieces and joined once, so a feed made of
    thousands of one-character folds costs linear time, not quadratic.
    """
    lines: list[str] = []
    pieces: list[str] = []
    for raw in _NEWLINES.split(text):
        if pieces and raw[:1] in (" ", "\t"):
            pieces.append(raw[1:])
            continue
        if pieces:
            lines.append("".join(pieces))
        pieces = [raw] if raw else []
    if pieces:
        lines.append("".join(pieces))
    return lines


def _split_unquoted(text: str, separator: str, maxsplit: int | None = None) -> list[str]:
    """Split on ``separator`` outside double quotes, at most ``maxsplit`` times."""
    parts: list[str] = []
    start = 0
    quoted = False
    for index, char in enumerate(text):
        if maxsplit is not None and len(parts) >= maxsplit:
            break
        if char == '"':
            quoted = not quoted
        elif char == separator and not quoted:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _parse_line(line: str) -> tuple[str, _Prop] | None:
    """``NAME;PARAM=v;PARAM2="a:b":value`` as ``(NAME, _Prop)``."""
    head_and_value = _split_unquoted(line, ":", maxsplit=1)
    if len(head_and_value) != 2:
        return None
    head, value = head_and_value
    name, *raw_params = _split_unquoted(head, ";")
    params: dict[str, str] = {}
    for raw in raw_params:
        key, sep, param_value = raw.partition("=")
        if sep:
            params[key.strip().upper()] = param_value.strip().strip('"')
    name = name.strip().upper()
    return (name, _Prop(params=params, value=value)) if name else None


def _unescape_text(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        char = match.group(1)
        return "\n" if char in "nN" else char

    return _TEXT_ESCAPE.sub(replace, value)


def _parse_moment(prop: _Prop) -> datetime | None:
    """A ``DTSTART``/``DTEND`` value as aware UTC, or ``None`` if unreadable."""
    value = prop.value.strip()
    tzid = prop.params.get("TZID", "").strip().upper()
    try:
        if _DATE_ONLY.fullmatch(value):
            day = datetime.strptime(value, "%Y%m%d").date()
            return datetime.combine(day, time(0, 0), tzinfo=IST).astimezone(UTC)
        match = _DATE_TIME.fullmatch(value)
        if match is None:
            return None
        naive = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")
        if match.group(2):  # an explicit "Z" wins over any TZID
            return naive.replace(tzinfo=UTC)
        if not tzid or tzid in _IST_TZIDS:
            return naive.replace(tzinfo=IST).astimezone(UTC)
        if tzid in _UTC_TZIDS:
            return naive.replace(tzinfo=UTC)
    except (ValueError, OverflowError):
        return None
    return None


def _parse_geo(value: str) -> tuple[float, float] | None:
    lat_text, sep, lng_text = value.partition(";")
    if not sep:
        return None
    try:
        lat, lng = float(lat_text), float(lng_text)
    except ValueError:
        return None
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    return lat, lng


def _text(props: dict[str, _Prop], name: str) -> str | None:
    prop = props.get(name)
    if prop is None:
        return None
    return _unescape_text(prop.value).strip() or None


def _to_event(props: dict[str, _Prop]) -> IcalEvent | None:
    uid = _text(props, "UID")
    start_prop = props.get("DTSTART")
    if uid is None or start_prop is None:
        return None
    dtstart = _parse_moment(start_prop)
    if dtstart is None:
        return None
    end_prop = props.get("DTEND")
    dtend = _parse_moment(end_prop) if end_prop is not None else None
    if dtend is not None and dtend <= dtstart:
        dtend = None
    geo_prop = props.get("GEO")
    status = props["STATUS"].value.strip().upper() if "STATUS" in props else None
    organizer = props.get("ORGANIZER")
    return IcalEvent(
        uid=uid,
        summary=_text(props, "SUMMARY") or "",
        description=_text(props, "DESCRIPTION"),
        location=_text(props, "LOCATION"),
        dtstart=dtstart,
        dtend=dtend,
        geo=_parse_geo(geo_prop.value.strip()) if geo_prop is not None else None,
        status=status or None,
        organizer_cn=(organizer.params.get("CN") or None) if organizer is not None else None,
    )


def parse_ical(data: bytes) -> list[IcalEvent]:
    """Every readable ``VEVENT`` in ``data``, in feed order; ``[]`` for bad input."""
    if len(data) > MAX_ICAL_BYTES:
        return []
    events: list[IcalEvent] = []
    current: dict[str, _Prop] | None = None
    nested = 0  # depth of components (a VALARM) inside the current VEVENT
    for line in _unfold(data.decode("utf-8-sig", errors="replace")):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        name, prop = parsed
        keyword = prop.value.strip().upper()
        if name == "BEGIN":
            if current is None:
                if keyword == "VEVENT":
                    current, nested = {}, 0
            else:
                nested += 1
        elif name == "END":
            if current is None:
                continue
            if nested:
                nested -= 1
            elif keyword == "VEVENT":
                event = _to_event(current)
                if event is not None:
                    events.append(event)
                current = None
        elif current is not None and not nested:
            current.setdefault(name, prop)  # the first occurrence wins
    return events


# ── building ─────────────────────────────────────────────────────────────────

# Control characters other than a line break, which text escapes to "\n".
_BUILD_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_URL_UNSAFE = re.compile(r"[\x00-\x20\x7f]")


def _escape_text(value: str) -> str:
    value = _BUILD_CONTROL.sub("", value)
    value = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return _NEWLINES.sub("\\\\n", value)


def _fold(line: str) -> str:
    """``line`` in chunks of at most 75 octets (a fold costs the next line one octet)."""
    chunks: list[str] = []
    chunk: list[str] = []
    size = 0
    limit = _LINE_LIMIT
    for char in line:
        octets = len(char.encode("utf-8"))
        if size + octets > limit:
            chunks.append("".join(chunk))
            chunk, size, limit = [], 0, _LINE_LIMIT - 1
        chunk.append(char)
        size += octets
    chunks.append("".join(chunk))
    return "\r\n ".join(chunks)


def _utc_stamp(moment: datetime) -> str:
    return as_utc(moment).strftime("%Y%m%dT%H%M%SZ")


def build_ics(
    *,
    uid: str,
    title: str,
    starts_at: datetime,
    ends_at: datetime,
    location: str | None,
    url: str,
    description: str | None,
    cancelled: bool,
    stamp: datetime,
) -> str:
    """A one-event ``.ics`` file as text (CRLF endings, folded, times in UTC ``Z``).

    A naive datetime is read as UTC. ``stamp`` is when the file was made; it is
    a parameter so the output is a pure function of its arguments.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{_escape_text(uid)}",
        f"DTSTAMP:{_utc_stamp(stamp)}",
        f"DTSTART:{_utc_stamp(starts_at)}",
        f"DTEND:{_utc_stamp(ends_at)}",
        f"SUMMARY:{_escape_text(title)}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape_text(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape_text(description)}")
    if url:
        lines.append(f"URL:{_URL_UNSAFE.sub('', url)}")
    if cancelled:
        lines.append("STATUS:CANCELLED")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "".join(_fold(line) + "\r\n" for line in lines)

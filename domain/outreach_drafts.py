"""
domain/outreach_drafts.py
~~~~~~~~~~~~~~~~~~~~~~~~~
The shapes an outreach draft can take, and the template used when no model can
write one.

A draft is always a *suggestion* the user edits and copies; nothing here (or
anywhere in Outreach) sends it. Each channel has a hard length the message must
fit inside - a LinkedIn connection note stops at 200 characters whatever the
model wrote - so every body, generated or templated, goes through
``fit_to_limit``, and only an email carries a subject.

The templates leave visible ``[gaps]`` for the one thing only the user can say
(what caught their eye, why this company). A draft sent without filling one in
would look sent-by-a-bot, which is the point: the gap is the reminder.

Pure: no I/O, no clock, no LLM. The send date is passed in.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

DraftFormat = Literal["linkedin_note", "linkedin_message", "email", "x_dm", "github_comment"]
DraftKind = Literal["first", "follow_up"]

# --- formats ------------------------------------------------------------------


@dataclass(frozen=True)
class FormatSpec:
    """One way to write to someone: where it goes, how long it may be."""

    key: DraftFormat
    channel: str
    max_chars: int
    label: str


#: In the order the picker shows them. ``max_chars`` is the body; an email's
#: subject has its own ``SUBJECT_MAX``.
FORMATS: dict[str, FormatSpec] = {
    spec.key: spec
    for spec in (
        # The free-account LinkedIn connection-note limit.
        FormatSpec("linkedin_note", "linkedin", 200, "LinkedIn note"),
        FormatSpec("linkedin_message", "linkedin", 700, "LinkedIn message"),
        FormatSpec("email", "email", 1200, "Email"),
        FormatSpec("x_dm", "x", 280, "X message"),
        FormatSpec("github_comment", "github", 500, "GitHub comment"),
    )
}

SUBJECT_MAX = 80

_FALLBACK_ROLE = "software engineer"


def default_format(channel: str, kind: DraftKind) -> DraftFormat:
    """The format to open the draft panel on for ``channel``.

    LinkedIn has two: the short connection note for a first message, the longer
    message for a follow-up. Every other channel has one. A channel with no
    draft format (a call, an event) is a ``ValueError``.
    """
    if channel == "linkedin":
        return "linkedin_note" if kind == "first" else "linkedin_message"
    for spec in FORMATS.values():
        if spec.channel == channel:
            return spec.key
    raise ValueError(f"No draft format for the channel {channel!r}.")


def formats_for_channels(channels: Iterable[str]) -> list[DraftFormat]:
    """The formats a person can be written to in, given the channels we hold for
    them, in ``FORMATS`` order. Channels without a draft format are skipped."""
    wanted = set(channels)
    return [spec.key for spec in FORMATS.values() if spec.channel in wanted]


# --- text helpers -------------------------------------------------------------


def _one_line(value: str | None) -> str:
    """Whitespace runs (newlines included) collapsed to single spaces."""
    return " ".join((value or "").split())


def _word_cut(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters on a whole word.

    Nothing is added to the end. A single word longer than ``limit`` is cut
    where the limit falls, since there is no word boundary to keep.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    window = text[:limit]
    if text[limit].isspace():
        return window.rstrip()  # the limit lands exactly at the end of a word
    for i in range(limit - 1, 0, -1):
        if window[i].isspace():
            head = window[:i].rstrip()
            if head:
                return head
    return window


def fit_to_limit(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters.

    Text that fits is returned as it came. Otherwise it is cut at the last
    sentence end inside the limit (``.``, ``!`` or ``?`` followed by whitespace,
    so ``3.5`` is not one), and failing that at the last word. No ellipsis or
    other punctuation is added.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    for i in range(limit - 1, 0, -1):
        if text[i] in ".!?" and text[i + 1].isspace():
            return text[: i + 1]
    return _word_cut(text, limit)


_SUBJECT_LINE = re.compile(r"\A\s*subject[ \t]*:[ \t]*([^\r\n]*)(?:\r?\n|\Z)", re.IGNORECASE)


def split_subject(text: str) -> tuple[str | None, str]:
    """An email draft's ``Subject: ...`` first line, split from its body.

    No subject line (or an empty one) gives ``(None, ...)``; with none, the text
    comes back as it was. A subject longer than ``SUBJECT_MAX`` is cut on a
    whole word. Only the first line is read: a ``Subject:`` further down is
    part of the body.
    """
    match = _SUBJECT_LINE.match(text)
    if match is None:
        return None, text
    subject = _word_cut(_one_line(match.group(1)), SUBJECT_MAX)
    return (subject or None), text[match.end() :].strip()


def first_name(name: str | None) -> str | None:
    """The first word of a name, or ``None`` when there is no name to use."""
    parts = (name or "").split()
    return parts[0] if parts else None


# --- templates ----------------------------------------------------------------


def _day(d: date) -> str:
    """``3 Oct``: the day without a zero pad, which ``%d`` would add."""
    return f"{d.day} {d.strftime('%b')}"


def fallback_draft(
    *,
    fmt: DraftFormat,
    kind: DraftKind,
    recipient_name: str | None,
    company: str,
    role: str | None,
    last_sent_on: date | None,
) -> tuple[str | None, str]:
    """The template for ``fmt``, as ``(subject, body)``.

    ``role`` is the user's own target role ("I'm a Backend Engineer in
    Bengaluru"), not the recipient's. ``last_sent_on`` dates a follow-up's
    reference to the earlier note. The subject is ``None`` for every format
    but ``email``. The body is cut to the format's limit.
    """
    first = first_name(recipient_name)
    greeting = f"Hi {first}," if first else "Hi,"
    company_text = _one_line(company)
    user_role = _one_line(role)
    role_text = user_role or _FALLBACK_ROLE
    is_email = fmt == "email"

    if kind == "follow_up":
        earlier = f"my note from {_day(last_sent_on)}" if last_sent_on else "my earlier note"
        tail = (
            f"I'm still keen to learn how {company_text}'s team works. "
            "If now isn't a good time, no problem at all."
        )
        if is_email:
            body = f"{greeting}\n\nFollowing up on {earlier}. {tail}"
        else:
            body = f"{greeting} following up on {earlier}. {tail}"
    elif is_email:
        body = (
            f"{greeting}\n\n"
            f"I'm a {role_text} in Bengaluru. "
            f"I'm interested in {company_text} because [one specific reason]. "
            "Would you be open to a 15-minute chat, or could you point me to the right person? "
            "If now isn't a good time, no problem at all."
        )
    elif fmt == "github_comment":
        body = (
            "Hi! I'd like to work on this. My plan: [one line on the approach]. "
            "Could you assign it to me, or tell me if someone is already on it?"
        )
    else:
        body = (
            f"{greeting} I'm a {role_text} in Bengaluru and I've been following "
            f"{company_text}'s work on [what caught your eye]. "
            "Would you be open to a short chat about your team?"
        )

    subject: str | None = None
    if is_email:
        # The user's own role is used as written (iOS, macOS, dbt); only the
        # fallback is capitalised, since a subject should not start lowercase.
        subject_role = user_role or _FALLBACK_ROLE.capitalize()
        subject = _word_cut(f"{subject_role} interested in {company_text}", SUBJECT_MAX)
    return subject, fit_to_limit(body, FORMATS[fmt].max_chars)

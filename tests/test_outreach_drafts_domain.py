"""
The formats, limits and fallback templates behind Outreach drafts.

Everything under test is pure. The model writes the draft when it can; these
rules cut its reply to the format's limit, split an email's subject off the top,
and supply the template the user gets when the model is out of reach. A template
leaves visible ``[gaps]`` for the one thing only the user can say, so a sent-as-is
draft cannot pass for a personal note.
"""

from __future__ import annotations

from datetime import date
from typing import get_args

import pytest

from domain.outreach_drafts import (
    FORMATS,
    SUBJECT_MAX,
    DraftFormat,
    DraftKind,
    FormatSpec,
    default_format,
    fallback_draft,
    first_name,
    fit_to_limit,
    formats_for_channels,
    split_subject,
)

ALL_FORMATS: tuple[DraftFormat, ...] = get_args(DraftFormat)
KINDS: tuple[DraftKind, ...] = get_args(DraftKind)

# 60 characters: long enough that the shorter formats must cut their template.
LONG_COMPANY = "Ledgerline Quantum Analytics and Robotics Systems Groups Ltd"


# --- formats -------------------------------------------------------------------


def test_format_limits() -> None:
    assert {key: (spec.channel, spec.max_chars, spec.label) for key, spec in FORMATS.items()} == {
        "linkedin_note": ("linkedin", 200, "LinkedIn note"),
        "linkedin_message": ("linkedin", 700, "LinkedIn message"),
        "email": ("email", 1200, "Email"),
        "x_dm": ("x", 280, "X message"),
        "github_comment": ("github", 500, "GitHub comment"),
    }
    assert SUBJECT_MAX == 80


def test_formats_are_keyed_by_their_own_key_and_are_immutable() -> None:
    assert tuple(FORMATS) == ALL_FORMATS
    for key, spec in FORMATS.items():
        assert isinstance(spec, FormatSpec)
        assert spec.key == key
    with pytest.raises(AttributeError):
        FORMATS["email"].max_chars = 5  # type: ignore[misc]


def test_default_format() -> None:
    assert default_format("linkedin", "first") == "linkedin_note"
    assert default_format("linkedin", "follow_up") == "linkedin_message"
    for kind in KINDS:
        assert default_format("email", kind) == "email"
        assert default_format("x", kind) == "x_dm"
        assert default_format("github", kind) == "github_comment"


def test_default_format_refuses_a_channel_with_no_draft() -> None:
    with pytest.raises(ValueError):
        default_format("call", "first")


def test_formats_for_channels_follow_formats_order() -> None:
    assert formats_for_channels(["linkedin"]) == ["linkedin_note", "linkedin_message"]
    assert formats_for_channels(["github", "x", "linkedin"]) == [
        "linkedin_note",
        "linkedin_message",
        "x_dm",
        "github_comment",
    ]
    assert formats_for_channels(iter(["email", "email"])) == ["email"]


def test_formats_for_channels_ignore_channels_without_a_draft() -> None:
    assert formats_for_channels([]) == []
    assert formats_for_channels(["call", "event", "other", "nonsense"]) == []
    assert formats_for_channels(["call", "x"]) == ["x_dm"]


# --- fit_to_limit --------------------------------------------------------------


def test_fit_to_limit() -> None:
    assert fit_to_limit("Short and sweet.", 200) == "Short and sweet."
    assert fit_to_limit("Exactly ten", 11) == "Exactly ten"
    assert fit_to_limit("One. Two three four.", 10) == "One."

    # No sentence end inside the limit: cut at the last space, add nothing.
    cut = fit_to_limit("alpha beta gamma delta epsilon", 17)
    assert cut == "alpha beta gamma"

    text = "One. Two three four. Five six seven eight nine ten eleven twelve."
    for limit in range(0, len(text) + 5):
        assert len(fit_to_limit(text, limit)) <= limit


def test_fit_to_limit_keeps_the_last_sentence_that_fits() -> None:
    text = "First one here. Second one here. Third one here."
    assert fit_to_limit(text, 32) == "First one here. Second one here."
    assert fit_to_limit(text, 31) == "First one here."
    assert fit_to_limit("Abc. Def ghi", 4) == "Abc."
    assert fit_to_limit("Is this it? Maybe not at all", 14) == "Is this it?"
    assert fit_to_limit("Done! And then some more words", 8) == "Done!"


def test_fit_to_limit_does_not_keep_a_sentence_that_leaves_almost_nothing() -> None:
    # "Hi Asha." is 8 characters of a 200 limit: a message that stops there has thrown
    # the rest away, so the cut falls back to the last whole word.
    text = "Hi Asha. " + "I build payment systems and would love to hear how your team works, " * 6
    cut = fit_to_limit(text, 200)
    assert 150 < len(cut) <= 200
    assert cut.startswith("Hi Asha. I build")
    assert text.startswith(cut)
    assert text[len(cut)].isspace()  # cut on a whole word

    # A sentence that keeps at least a quarter of the limit is still preferred.
    quarter = "x" * 49 + "."  # 50 characters: exactly a quarter of 200
    assert fit_to_limit(quarter + " " + "y" * 300, 200) == quarter
    short = "x" * 48 + "."  # 49 characters: one short of a quarter
    assert fit_to_limit(short + " " + "y " * 150, 200) != short


def test_fit_to_limit_cuts_on_a_whole_word() -> None:
    assert fit_to_limit("alpha beta gamma", 10) == "alpha beta"  # the limit lands on a space
    assert fit_to_limit("alpha beta gamma", 11) == "alpha beta"  # ... and one past it
    assert fit_to_limit("alpha beta gamma", 13) == "alpha beta"  # ... and mid-word
    assert fit_to_limit("alpha beta\ngammadelta", 15) == "alpha beta"  # a newline is a break too


def test_fit_to_limit_does_not_read_a_decimal_point_as_a_sentence_end() -> None:
    assert fit_to_limit("Raised 3.5 million dollars in total", 12) == "Raised 3.5"


def test_fit_to_limit_hard_cuts_a_single_long_word() -> None:
    assert fit_to_limit("x" * 50, 20) == "x" * 20


def test_fit_to_limit_with_no_room_is_empty() -> None:
    assert fit_to_limit("anything", 0) == ""
    assert fit_to_limit("anything", -3) == ""


# --- split_subject -------------------------------------------------------------


def test_split_subject() -> None:
    assert split_subject("Subject: Hello there\n\nBody text") == ("Hello there", "Body text")
    assert split_subject("Just a body, no subject line.") == (None, "Just a body, no subject line.")

    subject, body = split_subject("Subject: " + "word " * 24 + "\n\nBody")
    assert subject is not None
    assert len(subject) <= SUBJECT_MAX
    assert body == "Body"
    assert subject == subject.strip()


def test_a_long_subject_is_cut_on_a_whole_word() -> None:
    long_subject = " ".join(["abcdefghi"] * 12)  # 9 * 12 + 11 = 119 characters
    assert len(long_subject) > 100
    subject, _ = split_subject(f"Subject: {long_subject}\nBody")
    assert subject == " ".join(["abcdefghi"] * 8)  # 8 words = 79 characters
    assert subject is not None and len(subject) <= SUBJECT_MAX


def test_split_subject_is_case_blind_and_tolerates_leading_blank_lines() -> None:
    assert split_subject("\n  subject:  Hi there \r\nBody") == ("Hi there", "Body")
    assert split_subject("SUBJECT: Hi\nLine one\nLine two") == ("Hi", "Line one\nLine two")


def test_split_subject_only_reads_the_first_line() -> None:
    text = "Hello\nSubject: not a subject\nBody"
    assert split_subject(text) == (None, text)


def test_an_empty_subject_is_no_subject() -> None:
    assert split_subject("Subject:\n\nBody text") == (None, "Body text")


# --- first_name ----------------------------------------------------------------


def test_first_name() -> None:
    assert first_name("Asha Rao") == "Asha"
    assert first_name("  Asha   Rao  ") == "Asha"
    assert first_name("Vikram") == "Vikram"
    assert first_name(None) is None
    assert first_name("") is None
    assert first_name("   ") is None


# --- fallback_draft ------------------------------------------------------------


def _draft(
    fmt: DraftFormat,
    kind: DraftKind,
    *,
    name: str | None = "Asha Rao",
    company: str = "Tessel AI",
    role: str | None = "Backend Engineer",
    last_sent_on: date | None = date(2026, 10, 3),
) -> tuple[str | None, str]:
    return fallback_draft(
        fmt=fmt,
        kind=kind,
        recipient_name=name,
        company=company,
        role=role,
        last_sent_on=last_sent_on,
    )


def test_templates_fit_and_keep_gaps() -> None:
    assert len(LONG_COMPANY) == 60
    for fmt in ALL_FORMATS:
        for kind in KINDS:
            subject, body = _draft(fmt, kind, company=LONG_COMPANY)
            assert 0 < len(body) <= FORMATS[fmt].max_chars, (fmt, kind)
            if kind == "first":
                assert "[" in body, (fmt, kind)
            else:
                assert "3 Oct" in body, (fmt, kind)
            if fmt == "email":
                assert subject, (fmt, kind)
                assert len(subject) <= SUBJECT_MAX
            else:
                assert subject is None, (fmt, kind)


def test_template_without_a_name_reads_as_a_message() -> None:
    for fmt in ALL_FORMATS:
        if fmt == "github_comment":
            continue
        for kind in KINDS:
            _, body = _draft(fmt, kind, name=None)
            assert body.startswith("Hi,"), (fmt, kind, body)


def test_a_first_name_is_used_when_there_is_one() -> None:
    for fmt in ALL_FORMATS:
        if fmt == "github_comment":
            continue
        for kind in KINDS:
            _, body = _draft(fmt, kind)
            assert body.startswith("Hi Asha,"), (fmt, kind, body)
            assert "Rao" not in body


def test_first_message_template_wording() -> None:
    _, body = _draft("linkedin_note", "first")
    assert body == (
        "Hi Asha, I'm a Backend Engineer in Bengaluru and I've been following Tessel AI's work "
        "on [what caught your eye]. Would you be open to a short chat about your team?"
    )
    # The same sentences serve the longer LinkedIn message and X.
    assert _draft("linkedin_message", "first")[1] == body
    assert _draft("x_dm", "first")[1] == body


def test_follow_up_template_wording() -> None:
    _, body = _draft("linkedin_message", "follow_up")
    assert body == (
        "Hi Asha, following up on my note from 3 Oct. I'm still keen to learn how Tessel AI's "
        "team works. If now isn't a good time, no problem at all."
    )
    # A follow-up is the same note on any channel.
    assert _draft("x_dm", "follow_up")[1] == body
    assert _draft("linkedin_note", "follow_up")[1] == body


def test_github_comment_template_wording() -> None:
    subject, body = _draft("github_comment", "first")
    assert subject is None
    assert body == (
        "Hi! I'd like to work on this. My plan: [one line on the approach]. "
        "Could you assign it to me, or tell me if someone is already on it?"
    )
    # A GitHub comment is addressed to the issue, not to a person.
    assert _draft("github_comment", "first", name=None)[1] == body


def test_email_template_has_a_subject_and_four_sentences() -> None:
    subject, body = _draft("email", "first")
    assert subject == "Backend Engineer interested in Tessel AI"
    greeting, _, rest = body.partition("\n\n")
    assert greeting == "Hi Asha,"
    assert rest.count(". ") + rest.count("? ") + 1 == 4  # who I am, why, the ask, the easy out
    assert "I'm a Backend Engineer in Bengaluru." in rest
    assert "[one specific reason]" in rest
    assert "15-minute chat" in rest
    assert "no problem at all" in rest


def test_email_follow_up_keeps_a_subject_and_names_the_date() -> None:
    subject, body = _draft("email", "follow_up")
    assert subject == "Backend Engineer interested in Tessel AI"
    assert body.startswith("Hi Asha,\n\n")
    assert "my note from 3 Oct" in body


def test_email_subject_is_cut_to_the_subject_limit() -> None:
    subject, _ = _draft("email", "first", company=LONG_COMPANY)
    assert subject is not None
    assert len(subject) <= SUBJECT_MAX
    assert subject.startswith("Backend Engineer interested in Ledgerline")


def test_a_missing_role_reads_as_software_engineer() -> None:
    subject, body = _draft("email", "first", role=None)
    assert subject == "Software engineer interested in Tessel AI"
    assert "I'm a software engineer in Bengaluru." in body
    _, note = _draft("linkedin_note", "first", role=None)
    assert "I'm a software engineer in Bengaluru" in note
    assert _draft("email", "first", role="")[0] == "Software engineer interested in Tessel AI"


@pytest.mark.parametrize("role", ["iOS Engineer", "macOS Developer", "dbt Developer"])
def test_the_subject_keeps_the_users_role_exactly_as_written(role: str) -> None:
    subject, body = _draft("email", "first", role=role)
    assert subject == f"{role} interested in Tessel AI"
    assert f"I'm a {role} in Bengaluru." in body


def test_a_missing_send_date_does_not_print_none() -> None:
    for fmt in ALL_FORMATS:
        _, body = _draft(fmt, "follow_up", last_sent_on=None)
        assert "None" not in body, fmt
        assert "my earlier note" in body, fmt


def test_single_digit_days_are_not_zero_padded() -> None:
    _, body = _draft("x_dm", "follow_up", last_sent_on=date(2026, 11, 7))
    assert "my note from 7 Nov." in body
    _, body = _draft("x_dm", "follow_up", last_sent_on=date(2026, 12, 25))
    assert "my note from 25 Dec." in body


def test_inserted_text_is_flattened_to_one_line() -> None:
    subject, body = _draft(
        "email",
        "first",
        company="Tessel\n  AI",
        role="Backend\r\nEngineer",
        name="  Asha\nRao",
    )
    assert subject == "Backend Engineer interested in Tessel AI"
    assert "Hi Asha,\n\n" in body
    assert "\r" not in body
    assert "Tessel AI" in body

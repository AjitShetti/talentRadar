"""Learning a company's address format from found addresses, and applying it."""

from __future__ import annotations

from domain.email_patterns import guess, learn, local_part_kind, name_parts


def test_learns_first_dot_last() -> None:
    assert learn([("Priya Kumar", "priya.kumar@acme.com")]) == "first.last"


def test_one_word_name_teaches_nothing() -> None:
    assert learn([("Priya", "priya@acme.com")]) is None


def test_learns_flast_from_agreeing_examples() -> None:
    assert learn([("Priya Kumar", "pkumar@acme.com"), ("Ravi Shah", "rshah@acme.com")]) == "flast"


def test_example_fitting_two_patterns_teaches_nothing_alone() -> None:
    # first == last, so "ann@" is both `first` and `last`.
    assert learn([("Ann Ann", "ann@acme.com")]) is None


def test_ambiguous_example_resolved_by_an_unambiguous_one() -> None:
    assert learn([("Ann Ann", "ann@acme.com"), ("Ravi Shah", "ravi@acme.com")]) == "first"


def test_conflicting_examples_learn_nothing() -> None:
    assert learn([("Priya Kumar", "priya.kumar@acme.com"), ("Ravi Shah", "rshah@acme.com")]) is None


def test_inbox_never_teaches() -> None:
    assert learn([("Careers", "careers@acme.com"), ("Priya", "priya@acme.com")]) is None


def test_unexplained_example_is_ignored_not_fatal() -> None:
    examples = [("Priya Kumar", "priya.kumar@acme.com"), ("Ravi Shah", "rs1987@acme.com")]
    assert learn(examples) == "first.last"


def test_middle_names_are_ignored() -> None:
    assert name_parts("Priya Lakshmi Kumar") == ("priya", "kumar")
    assert learn([("Priya Lakshmi Kumar", "priya.kumar@acme.com")]) == "first.last"


def test_guess_applies_pattern() -> None:
    assert guess("f.last", "Ravi Shah", "acme.com") == "r.shah@acme.com"


def test_guess_folds_accents_and_hyphens() -> None:
    assert guess("first.last", "José Mar-Tin", "acme.com") == "jose.martin@acme.com"


def test_guess_refuses_one_word_initials_and_non_latin() -> None:
    assert guess("first.last", "Ravi", "acme.com") is None
    assert guess("first.last", "A. K.", "acme.com") is None
    assert guess("first.last", "रवि शाह", "acme.com") is None


def test_guess_refuses_digits_in_name() -> None:
    assert guess("first.last", "Ravi Shah2", "acme.com") is None


def test_local_part_kinds() -> None:
    assert local_part_kind("careers") == "hiring"
    assert local_part_kind("recruitment") == "hiring"
    assert local_part_kind("hr") == "hiring"
    assert local_part_kind("hello") == "drop"
    assert local_part_kind("no-reply") == "drop"
    assert local_part_kind("priya.kumar") == "personal"

"""
tests/test_event_relevance.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The Events relevance verdict: where an event is (decides ``store``) and what it
is (decides ``listed``), with the evidence for both. No database, no network.

The 19 recorded titles are inlined, each with the Bengaluru coordinates the
Luma feed gave it, and the recorded fixture is never read here: the feed
captured later will not contain these titles, and these tests must not move
when it is re-recorded.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from domain.areas import area_for_point
from domain.event_facts import EventFacts, SourceScope
from domain.event_relevance import (
    FORMATS,
    TOPICS,
    Verdict,
    assess,
    event_format,
    event_topics,
    excluded,
    is_tech,
)

START = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)
HSR = (12.9163, 77.6463)
KORAMANGALA = (12.9357, 77.6241)

LUMA_BENGALURU = SourceScope(key="luma-discover-bengaluru", india=True, trusted_tech=False)
ELSEWHERE = SourceScope(key="luma-discover-elsewhere", india=False, trusted_tech=False)
GDG = SourceScope(key="gdg", india=True, trusted_tech=True)
EVENTBRITE = SourceScope(key="eventbrite", india=False, trusted_tech=False)


def make_facts(title: str, **overrides: object) -> EventFacts:
    """An in-person Bengaluru event unless a test says otherwise."""
    fields: dict[str, object] = {
        "source": "luma",
        "external_id": "evt-1",
        "url": "https://events.example.invalid/evt-1",
        "title": title,
        "starts_at": START,
        "lat": HSR[0],
        "lng": HSR[1],
    }
    fields.update(overrides)
    return EventFacts(**fields)  # type: ignore[arg-type]


def online_facts(title: str, **overrides: object) -> EventFacts:
    return make_facts(title, is_online=True, lat=None, lng=None, **overrides)


# Title, latitude, longitude, listed: the 19 events of the Luma Bengaluru Discover feed
# as first recorded. "listed" is the verdict on the title alone. The unlisted ones name no
# technology in their titles; a real description would usually say what they are.
RECORDED = (
    ("-1 to Swiggy with Rohan Quill | South Park Commons (BLR)", 12.9163, 77.6463, False),
    (
        "Reimagining education in the age of AI with Dr. Ila Quill (IISc)",
        12.9802,
        77.6415,
        True,
    ),
    ("Swift Bengaluru × Okta", 12.9208, 77.6874, True),
    ("Namma AI Design Social", 12.9373, 77.6089, True),
    ("Blueprints of Being: The Embodied Architecture of Experience", 12.9797, 77.6390, False),
    ("After Figma Config Networking - Bangalore | Orbit by Osiflow", 12.9005, 77.6529, True),
    ("Explanet Social · Bangalore Tech · Indiranagar Side (#10)", 13.0037, 77.6618, True),
    ("Slow weekends: ft AOH", 12.9705, 77.6413, False),
    ("Voice AI Buildathon: Build an AI That Can Interview", 12.9190, 77.6672, True),
    ("Embedded Swift: From Code to Hardware", 12.9818, 77.5959, True),
    ("RSI Research Mixer", 12.9182, 77.6705, False),
    ("BitGo x Giottus: Crypto Custody in Action", 12.9319, 77.6283, True),
    ("Physical AI Demo Night (SPC in partnership with Elevation Capital)", 12.9151, 77.6432, True),
    ("-1 to Meera Quill | South Park Commons (BLR)", 12.9153, 77.6468, False),
    ("Code Meets Animation - Bangalore", 12.9166, 77.6712, False),
    ("Okta Developer Connect Bengaluru", 12.9225, 77.6862, True),
    ("QFF 2026: Quantum and Qiskit 101", 12.9219, 77.6202, True),
    ("QFF 2026: Hands-On Quantum Programming with Qiskit", 13.0364, 77.6201, True),
    ("Bangalore Product Mixer #4 | Product Reactor", 12.9122, 77.6528, True),
)


# ── the recorded titles ──────────────────────────────────────────────────────


def test_the_recorded_set_is_nineteen_titles() -> None:
    assert len(RECORDED) == 19
    assert len({title for title, *_ in RECORDED}) == 19


@pytest.mark.parametrize(("title", "lat", "lng", "listed"), RECORDED)
def test_every_recorded_title_is_stored(title: str, lat: float, lng: float, listed: bool) -> None:
    verdict = assess(make_facts(title, lat=lat, lng=lng), LUMA_BENGALURU)
    assert verdict.store is True
    assert verdict.listed is listed
    assert verdict.format in FORMATS
    assert set(verdict.topics) <= set(TOPICS)


def test_a_slow_weekend_is_stored_but_not_listed() -> None:
    verdict = assess(make_facts("Slow weekends: ft AOH"), LUMA_BENGALURU)
    assert verdict.store is True
    assert verdict.listed is False


def test_voice_ai_buildathon_is_a_listed_ai_hackathon() -> None:
    verdict = assess(
        make_facts("Voice AI Buildathon: Build an AI That Can Interview"), LUMA_BENGALURU
    )
    assert verdict.format == "hackathon"
    assert "ai" in verdict.topics
    assert verdict.listed is True


def test_product_mixer_is_a_listed_product_mixer() -> None:
    verdict = assess(make_facts("Bangalore Product Mixer #4 | Product Reactor"), LUMA_BENGALURU)
    assert verdict.format == "mixer"
    assert "product" in verdict.topics
    assert verdict.listed is True


def test_qiskit_101_is_a_quantum_workshop() -> None:
    verdict = assess(make_facts("QFF 2026: Quantum and Qiskit 101"), LUMA_BENGALURU)
    assert verdict.format == "workshop"
    assert "quantum" in verdict.topics
    assert verdict.listed is True


def test_minus_one_to_is_a_talk() -> None:
    title = "-1 to Swiggy with Rohan Quill | South Park Commons (BLR)"
    assert assess(make_facts(title), LUMA_BENGALURU).format == "talk"


# ── where: decides ``store`` ─────────────────────────────────────────────────


def test_outside_bengaluru_is_not_stored() -> None:
    verdict = assess(make_facts("AI Builders Meetup", lat=37.77, lng=-122.42), LUMA_BENGALURU)
    assert verdict.store is False
    assert verdict.listed is False
    assert verdict.area is None


def test_stated_coordinates_are_decisive() -> None:
    # An address that says Bengaluru cannot rescue a point that is in San Francisco.
    facts = make_facts(
        "AI Builders Meetup", lat=37.77, lng=-122.42, address="Indiranagar, Bengaluru"
    )
    assert assess(facts, LUMA_BENGALURU).store is False


def test_area_from_point_and_text() -> None:
    by_point = assess(make_facts("Python Meetup", lat=HSR[0], lng=HSR[1]), LUMA_BENGALURU)
    assert by_point.store is True
    assert by_point.area == area_for_point(*HSR)
    assert by_point.area is not None
    assert f"in {by_point.area}" in by_point.reasons

    # No coordinates: a street address that names no known area is still Bengaluru.
    by_city = assess(
        make_facts(
            "Python Meetup", lat=None, lng=None, address="Kumarakrupa Road, Bengaluru 560001"
        ),
        LUMA_BENGALURU,
    )
    assert by_city.store is True
    assert by_city.area is None
    assert "in Bengaluru" in by_city.reasons

    # No coordinates: an address that names an area places the event there.
    by_text = assess(
        make_facts(
            "Python Meetup",
            lat=None,
            lng=None,
            address="Koramangala 5th Block, Bangalore",
        ),
        LUMA_BENGALURU,
    )
    assert by_text.store is True
    assert by_text.area == "Koramangala"
    assert "in Koramangala" in by_text.reasons


def test_venue_or_locality_can_place_an_event_without_coordinates() -> None:
    by_venue = make_facts("Python Meetup", lat=None, lng=None, venue_name="Cafe, Indiranagar")
    assert assess(by_venue, LUMA_BENGALURU).area == "Indiranagar"
    by_locality = make_facts("Python Meetup", lat=None, lng=None, locality="Bangalore")
    assert assess(by_locality, LUMA_BENGALURU).store is True


def test_an_area_the_address_names_beats_the_nearest_centroid() -> None:
    # The RMZ Ecoworld campus is in Bellandur; its point is nearer Sarjapur Road's centroid.
    point = (12.9208, 77.6874)
    assert area_for_point(*point) == "Sarjapur Road"
    named = make_facts(
        "Swift Bengaluru × Okta",
        lat=point[0],
        lng=point[1],
        address="RMZ Ecoworld Rd, Bellandur, Bengaluru, Karnataka",
    )
    verdict = assess(named, LUMA_BENGALURU)
    assert verdict.area == "Bellandur"
    assert "in Bellandur" in verdict.reasons

    # An address that names no area leaves the point to decide.
    unnamed = make_facts(
        "Swift Bengaluru × Okta", lat=point[0], lng=point[1], address="Campus 31, Bengaluru"
    )
    assert assess(unnamed, LUMA_BENGALURU).area == "Sarjapur Road"


def test_text_alone_never_places_an_event_in_another_city() -> None:
    # "Brigade Road" is an MG Road alias, but this one is in Hyderabad.
    hyderabad = make_facts("Python Meetup", lat=None, lng=None, address="Brigade Road, Hyderabad")
    assert assess(hyderabad, LUMA_BENGALURU).store is False
    # "CBD" is an MG Road alias too; a foreign country in the address settles it.
    sydney = make_facts("Python Meetup", lat=None, lng=None, address="Sydney CBD, Australia")
    assert assess(sydney, LUMA_BENGALURU).store is False


def test_no_coordinates_and_no_bengaluru_text_is_not_stored() -> None:
    assert assess(make_facts("Python Meetup", lat=None, lng=None), LUMA_BENGALURU).store is False
    elsewhere = make_facts("Python Meetup", lat=None, lng=None, address="Bandra, Mumbai")
    assert assess(elsewhere, LUMA_BENGALURU).store is False
    # One coordinate alone is no point.
    assert assess(make_facts("Python Meetup", lng=None), LUMA_BENGALURU).store is False


def test_online_needs_india_signal() -> None:
    facts = online_facts("Intro to Kubernetes workshop")
    assert assess(facts, ELSEWHERE).store is False

    in_india = assess(facts, LUMA_BENGALURU)
    assert in_india.store is True
    assert "online · India" in in_india.reasons
    assert in_india.area is None

    by_timezone = assess(
        online_facts("Intro to Kubernetes workshop", timezone="Asia/Kolkata"), ELSEWHERE
    )
    assert by_timezone.store is True
    assert "online · India" in by_timezone.reasons

    other_timezone = online_facts("Intro to Kubernetes workshop", timezone="America/Los_Angeles")
    assert assess(other_timezone, ELSEWHERE).store is False


@pytest.mark.parametrize(
    ("title", "snippet"),
    [
        ("Kubernetes workshop for India", None),
        ("Kubernetes workshop", "Streamed 7pm IST"),
        ("Kubernetes workshop", "Hosted from Bengaluru"),
        ("Bangalore Kubernetes workshop", None),
    ],
)
def test_online_india_words_in_the_text_count(title: str, snippet: str | None) -> None:
    assert assess(online_facts(title, snippet=snippet), ELSEWHERE).store is True


@pytest.mark.parametrize(
    ("title", "snippet"),
    [
        ("Kubernetes workshop", "Streamed live"),
        ("Das ist ein Kubernetes workshop", None),  # German "ist" is not IST
        ("Kubernetes workshop for Indiana developers", None),
    ],
)
def test_online_look_alikes_are_not_india(title: str, snippet: str | None) -> None:
    assert assess(online_facts(title, snippet=snippet), ELSEWHERE).store is False


def test_the_online_flag_decides_the_branch() -> None:
    # A source that says "online" is online, whatever point it also attached.
    facts = make_facts("Kubernetes workshop", is_online=True)
    assert assess(facts, ELSEWHERE).store is False
    assert assess(facts, LUMA_BENGALURU).reasons[-1] == "online · India"


# ── what: decides ``listed`` ─────────────────────────────────────────────────


def test_online_webinar_not_listed() -> None:
    webinar = assess(online_facts("Intro to Kubernetes webinar"), LUMA_BENGALURU)
    assert webinar.store is True
    assert webinar.listed is False

    assert assess(online_facts("GenAI Hackathon"), LUMA_BENGALURU).listed is True


@pytest.mark.parametrize(
    ("title", "expected_format"),
    [
        ("GenAI Hackathon", "hackathon"),
        ("Rust Workshop", "workshop"),
        ("Cloud Native Conference", "conference"),
        ("Kubernetes Meetup", "meetup"),
        ("Kubernetes Demo Night", "demo"),
        ("Kubernetes Mixer", "mixer"),
        ("Kubernetes Talk", "talk"),
    ],
)
def test_online_lists_only_hackathon_workshop_and_conference(
    title: str, expected_format: str
) -> None:
    verdict = assess(online_facts(title), LUMA_BENGALURU)
    assert verdict.format == expected_format
    assert verdict.listed is (expected_format in {"hackathon", "workshop", "conference"})


def test_in_person_other_format_is_listed_when_tech() -> None:
    verdict = assess(make_facts("Swift Bengaluru × Okta"), LUMA_BENGALURU)
    assert verdict.format == "other"
    assert verdict.listed is True


def test_a_tech_event_that_is_not_stored_is_not_listed() -> None:
    far = assess(make_facts("GenAI Hackathon", lat=37.77, lng=-122.42), LUMA_BENGALURU)
    assert is_tech(make_facts("GenAI Hackathon"), LUMA_BENGALURU) is True
    assert far.listed is False


EXCLUDED: tuple[tuple[str, dict[str, object], str, bool], ...] = (
    (
        "Data Science Certification Course",
        {"is_free": False},
        "paid course",
        True,
    ),
    ("Free demo class: Python with placement guarantee", {}, "free demo class", True),
    # "Crypto" alone is not technology (see test_crypto_alone_is_not_tech), so the
    # first row is excluded and not tech; the second is tech and still excluded.
    ("Crypto trading masterclass", {}, "trading", False),
    ("Crypto trading bots in Python", {}, "trading", True),
    ("Speed dating for techies", {}, "dating", True),
    ("AI Bootcamp", {"ends_at": START + timedelta(days=5)}, "longer than 3 days", True),
)


@pytest.mark.parametrize(("title", "overrides", "reason", "tech"), EXCLUDED)
def test_exclusions(title: str, overrides: dict[str, object], reason: str, tech: bool) -> None:
    facts = make_facts(title, **overrides)
    # Where it reads as technology, the exclusion alone keeps it off the list.
    assert is_tech(facts, LUMA_BENGALURU) is tech
    assert excluded(facts) == reason
    verdict = assess(facts, LUMA_BENGALURU)
    assert verdict.store is True
    assert verdict.listed is False


@pytest.mark.parametrize(
    ("title", "snippet", "overrides", "reason"),
    [
        ("Python Bootcamp", None, {"is_free": False}, "paid course"),
        ("Cloud Training Program", None, {"is_free": False}, "paid course"),
        ("AWS Certification Prep", None, {"is_free": False}, "paid course"),
        ("Python Masterclass", None, {"is_free": False}, "paid course"),
        ("Python Weekend", "Placement guaranteed for every learner", {}, "placement guarantee"),
        ("Python Weekend", "100% placement", {}, "placement guarantee"),
        ("Python Weekend", "Join our free demo classes", {}, "free demo class"),
        ("Stock market algorithms with Python", None, {}, "trading"),
        ("Forex bots in Python", None, {}, "trading"),
        ("Python for astrology", None, {}, "astrology"),
        ("Network marketing for Python developers", None, {}, "network marketing"),
        ("Singles mixer for developers", None, {}, "dating"),
        ("Yoga and meditation morning", None, {}, "wellness"),
    ],
)
def test_more_exclusion_patterns(
    title: str, snippet: str | None, overrides: dict[str, object], reason: str
) -> None:
    assert excluded(make_facts(title, snippet=snippet, **overrides)) == reason


@pytest.mark.parametrize(
    ("title", "snippet", "overrides"),
    [
        ("Data Science Certification Course", None, {}),  # price unknown
        ("Data Science Certification Course", None, {"is_free": True}),  # free
        ("AI Summit", "Of course lunch is included", {"is_free": False}),  # the idiom
        ("Developer wellness: yoga for devs", None, {}),  # a tech word saves it
        ("Python Weekend", None, {"ends_at": START + timedelta(days=3)}),  # exactly three days
        ("Python Weekend", None, {"ends_at": START - timedelta(days=5)}),  # end before start
    ],
)
def test_ordinary_events_are_not_excluded(
    title: str, snippet: str | None, overrides: dict[str, object]
) -> None:
    assert excluded(make_facts(title, snippet=snippet, **overrides)) is None


def test_a_run_over_three_days_is_excluded() -> None:
    just_over = make_facts("Python Weekend", ends_at=START + timedelta(days=3, minutes=1))
    assert excluded(just_over) == "longer than 3 days"
    # An unstated end is two hours, so it is never "long".
    assert excluded(make_facts("Python Weekend")) is None


def test_walk_ins_welcome_is_not_a_hiring_event() -> None:
    facts = make_facts("Python Meetup", snippet="Walk-ins are welcome, bring a friend")
    verdict = assess(facts, LUMA_BENGALURU)
    assert verdict.format == "meetup"
    assert verdict.listed is True


def test_hiring_needs_listed_host() -> None:
    facts = make_facts("Walk-in drive for Java developers")
    assert event_format(facts.title) == "hiring"

    unhosted = assess(facts, LUMA_BENGALURU)
    assert unhosted.store is True
    assert unhosted.format == "hiring"
    assert unhosted.listed is False

    hosted = assess(facts, LUMA_BENGALURU, host_is_listed_company=True)
    assert hosted.listed is True
    assert hosted.format == "hiring"


def test_a_webinar_is_not_rescued_by_its_description() -> None:
    # The title names no format and the event is online: the description is not consulted.
    facts = online_facts("Intro to Kubernetes webinar", snippet="A hands-on session")
    verdict = assess(facts, LUMA_BENGALURU)
    assert verdict.format == "other"
    assert verdict.listed is False
    # The same word as a title does qualify it.
    workshop = online_facts("Intro to Kubernetes workshop", snippet="A webinar")
    assert assess(workshop, LUMA_BENGALURU).listed is True


def test_a_placement_drive_is_not_rescued_by_its_description() -> None:
    facts = make_facts(
        "Placement drive for Java developers", snippet="Join our community of engineers"
    )
    unhosted = assess(facts, LUMA_BENGALURU)
    assert unhosted.format == "hiring"
    assert unhosted.listed is False
    assert assess(facts, LUMA_BENGALURU, host_is_listed_company=True).listed is True


def test_a_walk_in_drive_stays_hiring_whatever_its_description_says() -> None:
    facts = make_facts(
        "Walk-in drive for Java developers", snippet="Connect with our hiring managers"
    )
    verdict = assess(facts, LUMA_BENGALURU)
    assert verdict.format == "hiring"
    assert verdict.listed is False


def test_a_hiring_phrase_in_the_description_marks_a_title_with_no_format_as_hiring() -> None:
    facts = make_facts(
        "Java developers evening", snippet="A walk-in drive for freshers, with networking"
    )
    verdict = assess(facts, LUMA_BENGALURU)
    assert verdict.format == "hiring"
    assert verdict.listed is False
    # A title that names a format keeps it.
    titled = make_facts("Java Hackathon", snippet="A walk-in drive for freshers")
    assert assess(titled, LUMA_BENGALURU).format == "hackathon"
    # An online event is never listed as hiring, with or without a host.
    online = online_facts("Java developers evening", snippet="A placement drive for freshers")
    assert assess(online, LUMA_BENGALURU, host_is_listed_company=True).listed is False


def test_the_title_names_the_format_before_the_description_does() -> None:
    jam_packed = make_facts("Python Meetup", snippet="A jam-packed evening")
    assert assess(jam_packed, LUMA_BENGALURU).format == "meetup"
    social = make_facts("Rust Workshop", snippet="Connect with the community")
    assert assess(social, LUMA_BENGALURU).format == "workshop"


def test_in_person_falls_back_to_the_description_when_the_title_names_no_format() -> None:
    facts = make_facts("Swift Bengaluru × Okta", snippet="A hands-on session for iOS developers")
    assert assess(facts, LUMA_BENGALURU).format == "workshop"


def test_crypto_alone_is_not_tech() -> None:
    for title in (
        "Bitcoin investing for beginners",
        "Crypto trading masterclass",
        "Cryptocurrency tips",
    ):
        assert is_tech(make_facts(title), LUMA_BENGALURU) is False
        assert assess(make_facts(title), LUMA_BENGALURU).listed is False
    # It is still a topic, and a technical crypto subject is still technology.
    assert "web3" in event_topics("Bitcoin investing for beginners")
    assert is_tech(make_facts("BitGo x Giottus: Crypto Custody in Action"), LUMA_BENGALURU)
    assert is_tech(make_facts("Solidity and blockchain"), LUMA_BENGALURU)


def test_gdg_scope_is_tech() -> None:
    facts = make_facts("Build with Gemini - Bangalore", source="gdg")
    verdict = assess(facts, GDG)
    assert verdict.listed is True
    assert "GDG chapter" in verdict.reasons
    assert is_tech(make_facts("Evening get-together", source="gdg"), GDG) is True


def test_a_gdg_chapter_is_known_by_its_source_not_its_scope_key() -> None:
    chapter = SourceScope(key="gdg:gdg-bangalore", india=True, trusted_tech=True)
    verdict = assess(make_facts("Evening get-together", source="gdg"), chapter)
    assert verdict.listed is True
    assert verdict.reasons[0] == "GDG chapter"


def test_a_luma_calendar_named_gdg_is_not_a_gdg_chapter() -> None:
    calendar = SourceScope(key="GDG Cloud Bengaluru", india=True, trusted_tech=True)
    verdict = assess(make_facts("Evening get-together"), calendar)
    assert verdict.listed is True
    assert "GDG chapter" not in verdict.reasons
    assert verdict.reasons[0] == "GDG Cloud Bengaluru calendar"


def test_a_trusted_calendar_is_tech_and_named() -> None:
    calendar = SourceScope(key="Bangalore Systems", india=True, trusted_tech=True)
    verdict = assess(make_facts("Evening get-together"), calendar)
    assert verdict.listed is True
    assert verdict.reasons[0] == "Bangalore Systems calendar"
    # A feed that is not marked trusted vouches for nothing.
    assert is_tech(make_facts("Evening get-together"), LUMA_BENGALURU) is False


def test_eventbrite_category_102_is_tech() -> None:
    facts = make_facts("Founders evening", source="eventbrite", taxonomy={"category": "102"})
    assert is_tech(facts, EVENTBRITE) is True


def test_eventbrite_taxonomy_alone_makes_a_plain_title_tech() -> None:
    plain = "Evening get-together"
    assert is_tech(make_facts(plain, source="eventbrite"), EVENTBRITE) is False

    category = make_facts(plain, source="eventbrite", taxonomy={"category": "102"})
    verdict = assess(category, EVENTBRITE)
    assert verdict.listed is True
    assert verdict.reasons[0] == "Eventbrite: Science & Technology"

    subcategory = make_facts(plain, source="eventbrite", taxonomy={"subcategory": "2004"})
    assert is_tech(subcategory, EVENTBRITE) is True

    other = make_facts(
        plain, source="eventbrite", taxonomy={"category": "103", "subcategory": "3001"}
    )
    assert is_tech(other, EVENTBRITE) is False


def test_whole_word_matching() -> None:
    assert is_tech(make_facts("Said the AIrline"), LUMA_BENGALURU) is False
    assert event_topics("Said the AIrline") == ()

    assert is_tech(make_facts("Rustic pottery"), LUMA_BENGALURU) is False
    assert "languages" not in event_topics("Rustic pottery")
    assert "languages" in event_topics("Rust meetup")

    assert "ai" in event_topics("AI-powered search")
    assert is_tech(make_facts("AI-powered pottery"), LUMA_BENGALURU) is True
    assert is_tech(make_facts("Open-source weekend"), LUMA_BENGALURU) is True
    assert is_tech(make_facts("Open source weekend"), LUMA_BENGALURU) is True
    assert is_tech(make_facts("OPEN SOURCE weekend"), LUMA_BENGALURU) is True


def test_the_snippet_counts_as_text() -> None:
    plain = make_facts("Slow weekends")
    assert assess(plain, LUMA_BENGALURU).listed is False
    described = make_facts("Slow weekends", snippet="A relaxed evening for Python developers")
    verdict = assess(described, LUMA_BENGALURU)
    assert verdict.listed is True
    assert "languages" in verdict.topics


# ── format and topics ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Voice AI Buildathon", "hackathon"),
        ("Game Jam", "hackathon"),
        ("PyCon Summit", "conference"),
        ("DevFest Bengaluru", "conference"),
        ("Google I/O Extended", "conference"),
        ("Demo Day", "demo"),
        ("Founders Showcase", "demo"),
        ("Hands-On Qiskit", "workshop"),
        ("Hands on Qiskit", "workshop"),
        ("Qiskit 101", "workshop"),
        ("Codelab: Flutter", "workshop"),
        ("Fireside with a founder", "talk"),
        ("Panel on hiring", "talk"),
        ("AMA with the maintainers", "talk"),
        ("Networking night", "mixer"),
        ("Meet & Greet", "mixer"),
        ("Designers Social", "mixer"),
        ("Rust Meetup", "meetup"),
        ("Python community", "meetup"),
        ("Job Fair", "hiring"),
        ("Hiring event for SREs", "hiring"),
        ("Placement drive", "hiring"),
        ("Walk-in drive", "hiring"),
        ("Pottery evening", "other"),
        ("", "other"),
    ],
)
def test_event_format(text: str, expected: str) -> None:
    assert event_format(text) == expected


def test_format_is_first_match_in_the_documented_order() -> None:
    assert FORMATS == (
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
    assert event_format("Hackathon demo day and networking meetup") == "hackathon"
    assert event_format("Summit workshop") == "conference"
    assert event_format("Demo day talk") == "demo"
    assert event_format("Workshop talk") == "workshop"
    assert event_format("Talk and networking") == "talk"
    assert event_format("Networking meetup") == "mixer"
    assert event_format("Meetup job fair") == "meetup"


def test_the_minus_one_to_series_needs_its_hyphen() -> None:
    assert event_format("-1 to Meera Quill") == "talk"
    assert event_format("Section 1 to 3 explained") == "other"


def test_topics_are_closed_ordered_and_may_be_empty() -> None:
    assert TOPICS == (
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
    assert event_topics("Pottery evening") == ()
    assert event_topics("") == ()
    topics = event_topics("Quantum Rust and AI in the cloud")
    assert topics == ("ai", "cloud", "quantum", "languages")
    assert all(topic in TOPICS for topic in topics)


@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("LLM agents in production", "ai"),
        ("Postgres and data engineering", "data"),
        ("Serverless on AWS", "cloud"),
        ("Kubernetes and Terraform", "devops"),
        ("React and Next.js", "web"),
        ("Flutter for Android", "mobile"),
        ("Zero trust security", "security"),
        ("Product management office hours", "product"),
        ("Figma and UX critique", "design"),
        ("Seed-stage startup founders", "startups"),
        ("Open source maintainers", "opensource"),
        ("Embedded firmware and robotics", "hardware"),
        ("Solidity and blockchain", "web3"),
        ("Qiskit for qubits", "quantum"),
        ("Python and Golang", "languages"),
        ("Mock interview practice", "career"),
    ],
)
def test_every_topic_has_a_keyword_table(text: str, topic: str) -> None:
    assert topic in event_topics(text)


def test_interview_alone_is_not_a_career_topic() -> None:
    # "Build an AI That Can Interview" is a voice-AI product, not interview prep.
    assert "career" not in event_topics("Voice AI Buildathon: Build an AI That Can Interview")


# ── the verdict and its reasons ──────────────────────────────────────────────


def test_reasons_read_source_then_topic_then_place() -> None:
    facts = make_facts(
        "Build with Gemini - Bangalore", source="gdg", lat=KORAMANGALA[0], lng=KORAMANGALA[1]
    )
    verdict = assess(facts, GDG)
    assert verdict.reasons == ("GDG chapter", "AI", "in Koramangala")
    assert verdict.area == "Koramangala"
    assert verdict.topics == ("ai",)


def test_reasons_name_at_most_three_topics_in_vocabulary_order() -> None:
    snippet = (
        "AI and data on the cloud: Kubernetes, React, Android, security, product design "
        "and startup founders."
    )
    verdict = assess(make_facts("Systems evening", snippet=snippet), LUMA_BENGALURU)
    # Every topic is still recorded; only the reasons are capped.
    assert verdict.topics == (
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
    )
    assert verdict.reasons[:3] == ("AI", "Data", "Cloud")
    assert len(verdict.reasons) == 4
    assert verdict.reasons[3].startswith("in ")

    gdg = assess(make_facts("Systems evening", source="gdg", snippet=snippet), GDG)
    assert gdg.reasons[:4] == ("GDG chapter", "AI", "Data", "Cloud")
    assert len(gdg.reasons) == 5


def test_keyword_tech_with_no_topic_names_the_word() -> None:
    verdict = assess(make_facts("Okta Developer Connect Bengaluru"), LUMA_BENGALURU)
    assert verdict.listed is True
    assert verdict.topics == ()
    assert verdict.reasons[0] == "mentions developer"


def test_online_reasons() -> None:
    verdict = assess(online_facts("GenAI Hackathon"), LUMA_BENGALURU)
    assert verdict.reasons == ("AI", "online · India")


def test_hosted_by_names_the_organiser() -> None:
    facts = make_facts("Walk-in drive for Java developers", organiser_name="Acme Labs")
    verdict = assess(facts, LUMA_BENGALURU, host_is_listed_company=True)
    assert verdict.reasons[-1] == "hosted by Acme Labs"

    by_host = make_facts("Walk-in drive for Java developers", hosts=("Initech",))
    assert (
        assess(by_host, LUMA_BENGALURU, host_is_listed_company=True).reasons[-1]
        == "hosted by Initech"
    )

    nameless = make_facts("Walk-in drive for Java developers")
    assert assess(nameless, LUMA_BENGALURU, host_is_listed_company=True).reasons[-1] == (
        "hosted by a company on the map"
    )
    assert "hosted by" not in " ".join(assess(nameless, LUMA_BENGALURU).reasons)


def test_a_place_outside_bengaluru_has_no_reasons() -> None:
    verdict = assess(make_facts("GenAI Hackathon", lat=37.77, lng=-122.42), LUMA_BENGALURU)
    assert verdict.reasons == ()


def test_verdict_is_frozen_and_assess_is_deterministic() -> None:
    facts = make_facts("Voice AI Buildathon: Build an AI That Can Interview")
    verdict = assess(facts, LUMA_BENGALURU)
    assert isinstance(verdict, Verdict)
    assert verdict == assess(facts, LUMA_BENGALURU)
    with pytest.raises(FrozenInstanceError):
        verdict.listed = False  # type: ignore[misc]

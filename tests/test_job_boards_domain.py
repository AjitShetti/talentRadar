"""Pure rules for the job-board roster: which boards a page links, what to probe, when to read again."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from domain.job_boards import (
    ATS,
    LIVE_SEARCH_ATS,
    PACE_SECONDS,
    PROBE_ATS,
    SWEPT_STATUSES,
    BoardRef,
    Schedule,
    board_from_url,
    board_ref,
    boards_in_html,
    domain_label,
    is_india_role,
    names_match,
    next_read,
    slug_candidates,
)

NOW = datetime(2026, 11, 23, 6, 0, tzinfo=UTC)


def _s(status):  # type: ignore[no-untyped-def]
    return Schedule(status, 0, 0, 0, NOW)


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_the_nine_ats_in_order_each_with_a_pace():
    assert ATS == (
        "greenhouse",
        "ashby",
        "lever",
        "workable",
        "recruitee",
        "keka",
        "rippling",
        "personio",
        "breezy",
    )
    assert dict(PACE_SECONDS) == {
        "greenhouse": 0.5,
        "ashby": 0.5,
        "lever": 1,
        "rippling": 1,
        "recruitee": 1,
        "personio": 1,
        "breezy": 1,
        "keka": 2,
        "workable": 10,
    }
    assert set(PACE_SECONDS) == set(ATS)


def test_probe_and_live_search_sets():
    assert frozenset({"greenhouse", "ashby"}) == LIVE_SEARCH_ATS
    assert PROBE_ATS == ("greenhouse", "ashby", "lever", "workable", "recruitee", "keka")
    assert frozenset({"candidate", "active", "no_india"}) == SWEPT_STATUSES


def test_board_ref_source():
    assert BoardRef("lever", "cred").source == "lever:cred"
    assert BoardRef("personio", "acme", "de").source == "personio:acme"
    assert BoardRef("lever", "cred").region is None


# ---------------------------------------------------------------------------
# boards_in_html / board_from_url
# ---------------------------------------------------------------------------


def test_boards_in_html_finds_each_ats():
    html = """<a href="https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited">x</a>
      <a href="https://boards.greenhouse.io/embed/job_board?for=sigmoid">x</a>
      <a href="https://jobs.lever.co/meesho/">x</a><a href="https://jobs.ashbyhq.com/sarvam">x</a>
      <a href="https://apply.workable.com/apna/">x</a><a href="https://lenskartcareers.recruitee.com/">x</a>
      <a href="https://spyneai.keka.com/careers">x</a><a href="https://ats.rippling.com/rippling/jobs">x</a>
      <a href="https://acme.breezy.hr/">x</a><a href="https://acme.jobs.personio.de/">x</a>"""
    assert [(b.ats, b.board, b.region) for b in boards_in_html(html)] == [
        ("greenhouse", "razorpaysoftwareprivatelimited", None),
        ("greenhouse", "sigmoid", None),
        ("lever", "meesho", None),
        ("ashby", "sarvam", None),
        ("workable", "apna", None),
        ("recruitee", "lenskartcareers", None),
        ("keka", "spyneai", None),
        ("rippling", "rippling", None),
        ("breezy", "acme", None),
        ("personio", "acme", "de"),
    ]


def test_boards_in_html_dedupes_and_ignores_lookalikes():
    html = (
        '<a href="https://jobs.lever.co/cred">a</a><a href="https://jobs.lever.co/cred/abc">b</a>'
        '<a href="https://boards.greenhouse.io.evil.example/x">c</a>'
        '<a href="https://www.recruitee.com/">d</a><a href="https://api.keka.com/careers">e</a>'
    )
    assert [(b.ats, b.board) for b in boards_in_html(html)] == [("lever", "cred")]


def test_boards_in_html_reads_src_single_quotes_unquoted_entities_and_bare_text():
    html = (
        "<iframe src='https://boards.greenhouse.io/embed/job_board?for=Sigmoid&amp;b=https%3A%2F%2Fx.example'></iframe>"
        "<a href=https://jobs.ashbyhq.com/sarvam>x</a>"
        "<p>We hire through https://jobs.lever.co/meesho, see also (https://apply.workable.com/apna).</p>"
        '<script>var u = "//acme.breezy.hr/";</script>'
    )
    assert [(b.ats, b.board) for b in boards_in_html(html)] == [
        ("greenhouse", "sigmoid"),
        ("ashby", "sarvam"),
        ("lever", "meesho"),
        ("workable", "apna"),
        ("breezy", "acme"),
    ]


def test_boards_in_html_dedupes_one_board_across_greenhouse_hosts():
    html = (
        '<a href="https://boards.greenhouse.io/sigmoid">a</a>'
        '<a href="https://job-boards.greenhouse.io/sigmoid/jobs/123">b</a>'
        '<a href="https://boards.greenhouse.io/embed/job_board?for=sigmoid">c</a>'
    )
    assert [(b.ats, b.board) for b in boards_in_html(html)] == [("greenhouse", "sigmoid")]


def test_boards_in_html_handles_empty_and_garbage():
    assert boards_in_html("") == []
    assert boards_in_html("<p>no links here</p>") == []
    assert boards_in_html('<a href="https://[bad">x</a><a href="http://">y</a>') == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/sigmoid", ("greenhouse", "sigmoid", None)),
        ("http://boards.greenhouse.io/sigmoid/jobs/4567890", ("greenhouse", "sigmoid", None)),
        ("https://job-boards.greenhouse.io/Sigmoid", ("greenhouse", "sigmoid", None)),
        ("https://boards.greenhouse.io/embed/job_board?for=InMobi", ("greenhouse", "inmobi", None)),
        (
            "https://job-boards.greenhouse.io/embed/job_board?for=inmobi&b=x",
            ("greenhouse", "inmobi", None),
        ),
        # Lever and Ashby keep the case they were written in; everything else is lower-cased.
        ("https://jobs.lever.co/Meesho/", ("lever", "Meesho", None)),
        ("https://jobs.ashbyhq.com/Sarvam/abc", ("ashby", "Sarvam", None)),
        ("https://apply.workable.com/Apna/j/ABC123/", ("workable", "apna", None)),
        (
            "https://LenskartCareers.Recruitee.com/o/engineer",
            ("recruitee", "lenskartcareers", None),
        ),
        ("https://spyneai.keka.com/careers/jobdetails/12", ("keka", "spyneai", None)),
        ("https://ats.rippling.com/Rippling/jobs/x", ("rippling", "rippling", None)),
        ("https://acme.breezy.hr/p/abc-engineer", ("breezy", "acme", None)),
        ("https://acme.jobs.personio.de/job/1", ("personio", "acme", "de")),
        ("https://acme.jobs.personio.com/", ("personio", "acme", "com")),
    ],
)
def test_board_from_url_accepts(url, expected):
    ref = board_from_url(url)
    assert ref is not None
    assert (ref.ats, ref.board, ref.region) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "ftp://jobs.lever.co/cred",
        "https://jobs.lever.co/",
        "https://jobs.lever.co",
        "https://user@jobs.lever.co/cred",
        "https://user:pw@jobs.lever.co/cred",
        "https://jobs.lever.co:8443/cred",
        "https://jobs.lever.co.evil.example/cred",
        "https://evil.example/jobs.lever.co/cred",
        "https://notjobs.lever.co/cred",
        "https://a.jobs.lever.co/cred",
        "https://boards.greenhouse.io.evil.example/x",
        "https://boards.greenhouse.io/embed/job_board",
        "https://boards.greenhouse.io/embed/job_board?for=",
        "https://boards.greenhouse.io/embed/job_board?for=a/b",
        "https://boards.greenhouse.io/embed/other?for=sigmoid",
        "https://boards.greenhouse.io/embed",
        "https://apply.workable.com/api/v1/widget/accounts/apna",
        "https://jobs.lever.co/%20x",
        "https://jobs.lever.co/-leadinghyphen",
        "https://www.recruitee.com/",
        "https://app.recruitee.com/",
        "https://a.b.recruitee.com/",
        "https://recruitee.com/",
        "https://api.keka.com/careers",
        "https://acme.keka.com/",
        "https://acme.keka.com/hr/login",
        "https://acme.keka.com/careersx",
        "https://www.breezy.hr/",
        "https://help.breezy.hr/",
        "https://jobs.jobs.personio.de/",
        "https://acme.personio.de/",
        "https://acme.jobs.personio.org/",
        "https://x.jobs.personio.de.evil.example/",
        "https://careers.keka.com/careers",
        "https://support.recruitee.com/",
    ],
)
def test_board_from_url_rejects(url):
    assert board_from_url(url) is None


def test_board_from_url_rejects_overlong_slug_and_url():
    assert board_from_url("https://jobs.lever.co/" + "a" * 81) is None
    assert board_from_url("https://jobs.lever.co/" + "a" * 80) is not None
    assert board_from_url("https://jobs.lever.co/cred?x=" + "a" * 3000) is None


# ---------------------------------------------------------------------------
# board_ref: a board read from a list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ats", "board", "region", "expected"),
    [
        # Lever's and Ashby's ids keep the case they are written in; every other ATS is lower-cased,
        # exactly as board_from_url does for the same board found in a link.
        ("lever", "Proseware", None, ("lever", "Proseware", None)),
        ("ashby", "AdventureWorks", None, ("ashby", "AdventureWorks", None)),
        ("greenhouse", "Contoso-Systems", None, ("greenhouse", "contoso-systems", None)),
        ("workable", "Coho_Winery", None, ("workable", "coho_winery", None)),
        ("keka", " Bellows ", None, ("keka", "bellows", None)),
        # Only Personio has a region; it is de or com, and de when it is anything else.
        ("personio", "alpine", "com", ("personio", "alpine", "com")),
        ("personio", "alpine", "de", ("personio", "alpine", "de")),
        ("personio", "alpine", None, ("personio", "alpine", "de")),
        ("personio", "alpine", "org", ("personio", "alpine", "de")),
        ("greenhouse", "northwind", "com", ("greenhouse", "northwind", None)),
    ],
)
def test_board_ref_follows_the_rules_of_board_from_url(ats, board, region, expected):
    ref = board_ref(ats, board, region)
    assert ref is not None
    assert (ref.ats, ref.board, ref.region) == expected


@pytest.mark.parametrize(
    ("ats", "board"),
    [
        ("smartrecruiters", "humongous"),
        ("taleo", "relecloud"),
        ("", "northwind"),
        ("Greenhouse", "northwind"),  # the caller lower-cases a name it read; this is not guessed
        ("greenhouse", ""),
        ("greenhouse", "   "),
        ("greenhouse", "tailspin.toys"),  # a dot: not a board id on any of the nine
        ("greenhouse", "a/b"),
        ("greenhouse", "-leading"),
        ("greenhouse", "x" * 81),
        ("keka", "evil.example/x"),
    ],
)
def test_board_ref_rejects(ats, board):
    assert board_ref(ats, board) is None


def test_board_ref_accepts_the_longest_board_id():
    assert board_ref("lever", "a" * 80) is not None


# ---------------------------------------------------------------------------
# slug_candidates
# ---------------------------------------------------------------------------


def test_slug_candidates():
    assert slug_candidates("Groww", "groww.in") == ["groww"]
    assert slug_candidates("Observe.AI", "observe.ai") == ["observe", "observeai", "observe-ai"]
    assert slug_candidates("Cars 24 Services", None) == ["cars24services", "cars-24-services"]
    assert len(slug_candidates("A B C D E", "abcde.com")) <= 4


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("groww.in", "groww"),
        ("observe.ai", "observe"),
        ("https://www.acme.co.in/careers", "acme"),
        ("careers.acmelabs.com", "acmelabs"),
        ("ACME.COM", "acme"),
    ],
)
def test_domain_label_is_the_registrable_domains_first_label(domain, expected):
    assert domain_label(domain) == expected


@pytest.mark.parametrize("domain", [None, "", "   ", "linkedin.com/company/acme", "not a host"])
def test_domain_label_is_none_without_a_usable_domain(domain):
    assert domain_label(domain) is None


def test_domain_label_is_the_first_slug_candidate():
    assert slug_candidates("Observe.AI", "observe.ai")[0] == domain_label("observe.ai")
    assert domain_label("groww.in") in slug_candidates("Groww Technologies", "groww.in")


def test_slug_candidates_domain_label_comes_from_the_registrable_domain():
    assert slug_candidates("Acme", "https://www.acme.co.in/careers") == ["acme"]
    assert slug_candidates("Acme Labs", "careers.acmelabs.com") == ["acmelabs", "acme-labs"]


def test_slug_candidates_ignores_profile_domains_and_junk():
    assert slug_candidates("Acme", "linkedin.com/company/acme") == ["acme"]
    assert slug_candidates("", None) == []
    assert slug_candidates("!!!", "") == []
    assert slug_candidates("", "acme.com") == ["acme"]


def test_slug_candidates_keeps_legal_suffix_words_and_folds_accents():
    # Razorpay's real Greenhouse board is the full legal name.
    assert slug_candidates("Razorpay Software Private Limited", "razorpay.com") == [
        "razorpay",
        "razorpaysoftwareprivatelimited",
        "razorpay-software-private-limited",
    ]
    assert slug_candidates("Café Coffee", None) == ["cafecoffee", "cafe-coffee"]


def test_slug_candidates_drops_guesses_that_could_not_be_a_slug():
    assert slug_candidates("x" * 100, None) == []


# ---------------------------------------------------------------------------
# next_read
# ---------------------------------------------------------------------------


def test_next_read_india_found_is_active_daily():
    s = next_read(_s("candidate"), "ok", 3, now=NOW)
    assert (s.status, s.zero_india_reads, s.next_read_at) == (
        "active",
        0,
        NOW + timedelta(hours=24),
    )


def test_next_read_zero_india_backs_off_then_no_india():
    s1 = next_read(_s("candidate"), "ok", 0, now=NOW)
    assert s1.next_read_at == NOW + timedelta(days=7)
    s2 = next_read(s1, "ok", 0, now=NOW)
    assert s2.next_read_at == NOW + timedelta(days=14)
    s3 = next_read(s2, "ok", 0, now=NOW)
    assert (s3.status, s3.next_read_at) == ("no_india", NOW + timedelta(days=30))


def test_next_read_zero_india_keeps_candidate_or_active_until_the_third():
    assert next_read(_s("candidate"), "ok", 0, now=NOW).status == "candidate"
    assert next_read(_s("active"), "ok", 0, now=NOW).status == "active"
    s = Schedule("active", 0, 2, 0, NOW)
    assert next_read(s, "ok", 0, now=NOW).status == "no_india"


def test_next_read_no_india_board_stays_monthly_then_wakes_on_an_india_role():
    s = Schedule("no_india", 0, 3, 0, NOW)
    again = next_read(s, "ok", 0, now=NOW)
    assert (again.status, again.zero_india_reads, again.next_read_at) == (
        "no_india",
        4,
        NOW + timedelta(days=30),
    )
    woke = next_read(again, "ok", 2, now=NOW)
    assert (woke.status, woke.zero_india_reads, woke.next_read_at) == (
        "active",
        0,
        NOW + timedelta(hours=24),
    )


def test_next_read_404_twice_is_gone():
    s1 = next_read(_s("active"), "not_found", 0, now=NOW)
    assert s1.status == "active"
    assert next_read(s1, "not_found", 0, now=NOW).status == "gone"


def test_next_read_first_404_retries_a_day_later_and_gone_waits_thirty_days():
    s1 = next_read(_s("active"), "not_found", 0, now=NOW)
    assert (s1.not_found_reads, s1.next_read_at) == (1, NOW + timedelta(hours=24))
    s2 = next_read(s1, "not_found", 0, now=NOW)
    assert (s2.status, s2.not_found_reads, s2.next_read_at) == ("gone", 2, NOW + timedelta(days=30))


def test_next_read_a_read_between_two_404s_breaks_the_streak():
    s1 = next_read(_s("active"), "not_found", 0, now=NOW)
    mid = next_read(s1, "ok", 1, now=NOW)
    assert mid.not_found_reads == 0
    assert next_read(mid, "not_found", 0, now=NOW).status == "active"
    # a rate limit says nothing about whether the board exists, but is not a 404 either
    after_429 = next_read(s1, "rate_limited", 0, now=NOW)
    assert after_429.not_found_reads == 0
    assert next_read(after_429, "not_found", 0, now=NOW).status == "active"


def test_next_read_failures_back_off_then_blocked():
    s = _s("active")
    for n in range(1, 7):
        s = next_read(s, "rate_limited", 0, now=NOW)
        assert s.failures == n
        assert s.next_read_at == NOW + min(timedelta(hours=2**n), timedelta(days=7))
    assert s.status == "blocked"


def test_next_read_failures_stay_active_until_the_sixth_and_error_counts_like_a_429():
    s = _s("active")
    for _ in range(5):
        s = next_read(s, "error", 0, now=NOW)
        assert s.status == "active"
    assert next_read(s, "error", 0, now=NOW).status == "blocked"


def test_next_read_back_off_is_capped_at_seven_days():
    s = Schedule("blocked", 9, 0, 0, NOW)
    assert next_read(s, "error", 0, now=NOW).next_read_at == NOW + timedelta(days=7)


def test_next_read_failures_do_not_touch_the_zero_india_count():
    s = Schedule("active", 0, 2, 0, NOW)
    after = next_read(s, "rate_limited", 0, now=NOW)
    assert after.zero_india_reads == 2


def test_ok_read_resets_failure_counters():
    s = next_read(Schedule("active", 3, 2, 1, NOW), "ok", 1, now=NOW)
    assert (s.failures, s.zero_india_reads, s.not_found_reads) == (0, 0, 0)


def test_ok_read_with_no_india_roles_resets_failures_but_counts_the_zero():
    s = next_read(Schedule("active", 3, 1, 1, NOW), "ok", 0, now=NOW)
    assert (s.failures, s.zero_india_reads, s.not_found_reads) == (0, 2, 0)


def test_next_read_does_not_mutate_its_input():
    prev = _s("candidate")
    next_read(prev, "ok", 5, now=NOW)
    assert prev == _s("candidate")


# ---------------------------------------------------------------------------
# is_india_role / names_match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bengaluru, Karnataka", True),
        ("Remote - India", True),
        ("Pune", True),
        ("London, UK", False),
        ("Remote", False),
        ("", False),
        (None, False),
    ],
)
def test_is_india_role(text, expected):
    assert is_india_role(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Readers join a role's several locations with "; ": any India part is enough,
        # though domain.geo lets a foreign country beat a city within one string.
        ("United Kingdom; Bengaluru", True),
        ("Bengaluru; United Kingdom", True),
        ("San Francisco; Mumbai; London", True),
        (" United Kingdom ;  Pune ", True),
        ("London; Berlin", False),
        ("United Kingdom; Germany", False),
        ("Remote; ", False),
        (";", False),
        ("; ;", False),
        ("Hyderabad, Pakistan; London", False),
        ("Hyderabad, Pakistan; Hyderabad, India", True),
        (None, False),
        ("", False),
    ],
)
def test_is_india_role_reads_each_semicolon_part_on_its_own(text, expected):
    assert is_india_role(text) is expected


def test_names_match():
    assert names_match("Razorpay Software Pvt. Ltd.", "razorpay software")
    assert names_match("CRED", "Cred")
    assert names_match("Observe.AI", "observe ai")
    assert not names_match("Groww", "Growth")


def test_names_match_never_matches_two_empty_names():
    assert not names_match("", "")
    assert not names_match("...", "!!!")

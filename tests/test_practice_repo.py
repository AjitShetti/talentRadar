"""
tests/test_practice_repo.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
The pure repo rules (URL, video link, push snapshot, tree, key files, commits, proof
file) and the deterministic signals computed from them. Nothing here touches the network
or the clock; every payload is a recorded or hand-built fixture, or built inline.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from domain.practice_repo import (
    PROOF_ALPHABET,
    PROOF_FILE,
    VIDEO_HOSTS,
    CommitLine,
    Push,
    RepoRef,
    TreeEntry,
    is_test_path,
    new_proof_code,
    parse_activity,
    parse_commits,
    parse_repo_url,
    parse_timestamp,
    parse_tree,
    pick_key_files,
    proof_matches,
    snapshot,
    valid_video_url,
)
from domain.practice_signals import compute_signals
from tests.test_practice_fixtures import load_fixture

MAIN = "refs/heads/main"
PROOF = "K7QX2M9PLA"
#: A 48-hour take-home that started at 04:00 UTC on Saturday 17 October. The snapshot
#: push of the submission fixture lands at exactly the deadline.
STARTED = datetime(2026, 10, 17, 4, 0, tzinfo=UTC)
DEADLINE = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)

SIGNAL_KEYS = {
    "repo_created_in_window",
    "pushes_in_window",
    "push_span_hours",
    "commits_at_snapshot",
    "largest_push_commits",
    "tests_present",
    "readme_chars",
    "writeup_words",
    "proof_found",
    "late_pushes_ignored",
}


def _sha(number: int) -> str:
    """A well-formed 40-hex commit id, distinct per number."""
    return f"{number:040x}"


def _push(after: int, before: int | None, at: datetime, kind: str = "push") -> Push:
    return Push(
        sha=_sha(after),
        before=_sha(before) if before is not None else "0" * 40,
        at=at,
        kind=kind,
    )


def _line(number: int, claimed_at: datetime | None = None) -> CommitLine:
    return CommitLine(sha=_sha(number), message=f"commit {number}", claimed_at=claimed_at)


def _entry(path: str, size: int = 100) -> TreeEntry:
    return TreeEntry(path=path, size=size)


def _submission_commits() -> list[CommitLine]:
    return parse_commits(load_fixture("submission/commits.json"))


def _submission_pushes() -> list[Push]:
    return parse_activity(load_fixture("submission/activity.json"), MAIN)


def _submission_signals(
    commits: list[CommitLine], pushes: list[Push], *, late_pushes: int
) -> dict[str, int | float | bool]:
    repo = load_fixture("submission/repo.json")
    return compute_signals(
        repo_created_at=parse_timestamp(repo["created_at"]),
        started_at=STARTED,
        deadline_at=DEADLINE,
        pushes=pushes,
        late_pushes=late_pushes,
        commits=commits,
        tree=parse_tree(load_fixture("submission/tree.json")),
        readme_chars=len(load_fixture("submission/readme.md")),
        writeup={
            "decisions": " ".join(["decision"] * 40),
            "tradeoffs": "No retries for four hundreds",
            "ai_usage": "AI wrote the HMAC check and I replaced it",
            "next_steps": "Add a dead-letter table",
            "video_url": "https://www.loom.com/share/abc",
        },
        proof_found=proof_matches(load_fixture("submission/talentradar.txt"), PROOF),
    )


# --- repo URL -----------------------------------------------------------------------------


def test_parse_repo_url_reads_owner_and_name() -> None:
    ref = parse_repo_url("https://github.com/demo-builder/webhook-relay")
    assert ref == RepoRef("demo-builder", "webhook-relay")
    assert ref is not None
    assert ref.api_path == "/repos/demo-builder/webhook-relay"
    assert ref.html_url == "https://github.com/demo-builder/webhook-relay"


def test_parse_repo_url_accepts_a_trailing_slash() -> None:
    assert parse_repo_url("https://github.com/demo-builder/webhook-relay/") == RepoRef(
        "demo-builder", "webhook-relay"
    )


def test_parse_repo_url_strips_dot_git() -> None:
    assert parse_repo_url("https://github.com/demo-builder/webhook-relay.git") == RepoRef(
        "demo-builder", "webhook-relay"
    )


def test_parse_repo_url_keeps_dots_and_underscores_inside_a_name() -> None:
    assert parse_repo_url("https://github.com/demo-builder/.github") == RepoRef(
        "demo-builder", ".github"
    )
    assert parse_repo_url("https://github.com/demo-builder/my_repo.v2") == RepoRef(
        "demo-builder", "my_repo.v2"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "https://github.com/a/b/tree/main",
        "https://github.com/a/../b",
        "https://github.com/a",
        "https://github.com/" + "o" * 40 + "/repo",
        "https://github.com/a/b?tab=readme",
        "https://github.com/a/b#readme",
        "https://github.com/a/b\n",
        "https://github.com/a/b ",
        "https://github.com.evil.example/a/b",
        "https://www.github.com/a/b",
        "https://github.com/a_b/c",
        "HTTPS://github.com/a/b",
        "",
    ],
)
def test_parse_repo_url_rejects_everything_else(url: str) -> None:
    assert parse_repo_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/a/.",
        "https://github.com/a/..",
        "https://github.com/a/.git",
        "https://github.com/a/..git",
    ],
)
def test_parse_repo_url_rejects_dot_names(url: str) -> None:
    assert parse_repo_url(url) is None


def test_parse_repo_url_accepts_a_39_character_owner() -> None:
    owner = "o" * 39
    assert parse_repo_url(f"https://github.com/{owner}/repo") == RepoRef(owner, "repo")


# --- video link ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", VIDEO_HOSTS)
def test_every_listed_video_host_is_accepted(host: str) -> None:
    assert valid_video_url(f"https://{host}/watch/abc")


def test_video_hosts_are_the_agreed_list() -> None:
    assert VIDEO_HOSTS == (
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "loom.com",
        "www.loom.com",
        "drive.google.com",
    )


def test_loom_share_link_is_valid() -> None:
    assert valid_video_url("https://www.loom.com/share/abc")


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/loom.com",
        "http://youtu.be/x",
        "https://youtube.com.evil.example/x",
        "https://m.youtube.com/x",
        "https://youtube.com@evil.example/x",
        "https://evil.example@youtube.com/x",
        "https://evil.example\\@youtube.com/x",
        "https://youtube.com:8443/x",
        "https://youtube.com:notaport/x",
        "ftp://youtu.be/x",
        "//youtu.be/x",
        "javascript:alert(1)",
        "https://youtu.be/x y",
        "https://youtu.be/x\n",
        "",
    ],
)
def test_other_video_links_are_refused(url: str) -> None:
    assert not valid_video_url(url)


# --- timestamps ---------------------------------------------------------------------------


def test_parse_timestamp_gives_aware_utc() -> None:
    parsed = parse_timestamp("2026-10-19T04:00:00Z")
    assert parsed == DEADLINE
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.tzinfo is UTC


def test_parse_timestamp_converts_an_offset_to_utc() -> None:
    parsed = parse_timestamp("2026-10-19T09:30:00+05:30")
    assert parsed == DEADLINE
    assert parsed is not None
    assert parsed.tzinfo is UTC


@pytest.mark.parametrize("value", [None, "", "yesterday", "2026-10-19", "2026-10-19T04:00:00", 5])
def test_parse_timestamp_rejects_anything_that_is_not_an_aware_instant(value: object) -> None:
    assert parse_timestamp(value) is None


# --- activity and the snapshot ------------------------------------------------------------


def test_parse_activity_keeps_the_four_main_pushes_and_drops_the_wip_branch() -> None:
    pushes = _submission_pushes()
    assert len(pushes) == 4
    assert [p.sha[:2] for p in pushes] == ["d4", "c3", "b2", "a1"]
    assert {p.kind for p in pushes} == {"push", "force_push"}
    assert all(p.at.tzinfo is UTC for p in pushes)
    assert pushes[0].at == datetime(2026, 10, 19, 5, 30, tzinfo=UTC)
    wip = parse_activity(load_fixture("submission/activity.json"), "refs/heads/wip")
    assert [p.sha[:2] for p in wip] == ["5d"]


def test_parse_activity_reads_push_and_force_push_only() -> None:
    payload = load_fixture("submission/activity.json")
    payload.append({**payload[0], "activity_type": "branch_deletion", "id": 9})
    payload.append({**payload[0], "activity_type": "something_new", "id": 10})
    assert len(parse_activity(payload, MAIN)) == 4


def test_parse_activity_skips_malformed_entries() -> None:
    good = load_fixture("submission/activity.json")[1]
    payload: list[Any] = [
        "not a dict",
        None,
        {**good, "after": "not-a-sha"},
        {**good, "after": good["after"].upper()},
        {**good, "before": "abc"},
        {**good, "timestamp": "soon"},
        {**good, "timestamp": None},
        {k: v for k, v in good.items() if k != "ref"},
        good,
    ]
    parsed = parse_activity(payload, MAIN)
    assert [p.sha for p in parsed] == [good["after"]]


@pytest.mark.parametrize("payload", [None, {}, "x", 7, {"message": "Not Found"}])
def test_parse_activity_of_a_non_list_is_empty(payload: object) -> None:
    assert parse_activity(payload, MAIN) == []


def test_parse_activity_reads_the_recorded_github_shape() -> None:
    pushes = parse_activity(load_fixture("github_activity.json"), "refs/heads/v2")
    assert pushes
    assert all(p.kind in ("push", "force_push") for p in pushes)
    assert all(len(p.sha) == 40 and p.at.tzinfo is UTC for p in pushes)
    assert pushes == sorted(pushes, key=lambda p: p.at, reverse=True)


def test_snapshot_includes_push_at_deadline() -> None:
    chosen, _ = snapshot(_submission_pushes(), DEADLINE)
    assert chosen is not None
    assert chosen.sha.startswith("c3")
    assert chosen.at == DEADLINE


def test_force_push_after_deadline_is_ignored_and_counted() -> None:
    chosen, late = snapshot(_submission_pushes(), DEADLINE)
    assert chosen is not None
    assert chosen.kind == "push"
    assert late == 1


def test_snapshot_one_second_before_the_deadline_falls_back_to_the_earlier_push() -> None:
    chosen, late = snapshot(_submission_pushes(), DEADLINE - timedelta(seconds=1))
    assert chosen is not None
    assert chosen.sha.startswith("b2")
    assert late == 2


def test_snapshot_does_not_depend_on_the_order_of_the_pushes() -> None:
    pushes = _submission_pushes()
    assert snapshot(list(reversed(pushes)), DEADLINE) == snapshot(pushes, DEADLINE)


def test_snapshot_of_nothing_is_nothing() -> None:
    assert snapshot([], DEADLINE) == (None, 0)


def test_snapshot_with_only_late_pushes_has_no_snapshot() -> None:
    late = [_push(1, None, DEADLINE + timedelta(minutes=1))]
    assert snapshot(late, DEADLINE) == (None, 1)


def test_backdated_commit_dates_do_not_move_snapshot_or_signals() -> None:
    """Commit dates are claimed by the author. Moving every one of them, past the deadline
    or before the start, must not change the snapshot or any signal."""
    pushes = _submission_pushes()
    commits = _submission_commits()
    recorded_snapshot = snapshot(pushes, DEADLINE)
    recorded_signals = _submission_signals(commits, pushes, late_pushes=recorded_snapshot[1])
    assert recorded_signals["late_pushes_ignored"] == 1

    for shift in (timedelta(days=100), -timedelta(days=100)):
        moved = [
            dataclasses.replace(c, claimed_at=c.claimed_at + shift if c.claimed_at else None)
            for c in commits
        ]
        assert [c.claimed_at for c in moved] != [c.claimed_at for c in commits]
        assert snapshot(pushes, DEADLINE) == recorded_snapshot
        assert _submission_signals(moved, pushes, late_pushes=recorded_snapshot[1]) == (
            recorded_signals
        )


def test_commit_dates_may_be_missing_without_changing_signals() -> None:
    pushes = _submission_pushes()
    commits = _submission_commits()
    blank = [dataclasses.replace(c, claimed_at=None) for c in commits]
    assert _submission_signals(blank, pushes, late_pushes=1) == _submission_signals(
        commits, pushes, late_pushes=1
    )


# --- tree and key files -------------------------------------------------------------------


def test_parse_tree_reads_the_recorded_github_shape() -> None:
    assert parse_tree(load_fixture("github_tree.json")) == [TreeEntry("README", 13)]


def test_parse_tree_treats_a_missing_size_as_zero() -> None:
    entries = parse_tree(load_fixture("submission/tree.json"))
    assert len(entries) == 8
    assert all(e.size == 0 for e in entries)
    assert entries[2] == TreeEntry("src/app.py", 0)


def test_parse_tree_keeps_blobs_only() -> None:
    payload = {
        "tree": [
            {"path": "src", "type": "tree"},
            {"path": "vendor/lib", "type": "commit"},
            {"path": "src/a.py", "type": "blob", "size": 12},
        ]
    }
    assert parse_tree(payload) == [TreeEntry("src/a.py", 12)]


def test_parse_tree_skips_unusable_rows() -> None:
    payload = {
        "tree": [
            "x",
            {"type": "blob"},
            {"path": "", "type": "blob"},
            {"path": 5, "type": "blob"},
            {"path": "bad\nname.py", "type": "blob"},
            {"path": "ok.py", "type": "blob", "size": "big"},
            {"path": "neg.py", "type": "blob", "size": -4},
            {"path": "flag.py", "type": "blob", "size": True},
        ]
    }
    assert parse_tree(payload) == [
        TreeEntry("ok.py", 0),
        TreeEntry("neg.py", 0),
        TreeEntry("flag.py", 0),
    ]


@pytest.mark.parametrize("payload", [None, [], "x", {}, {"tree": "no"}, {"tree": None}])
def test_parse_tree_of_an_unexpected_payload_is_empty(payload: object) -> None:
    assert parse_tree(payload) == []


def test_key_files_of_the_submission_skip_vendored_locked_and_binary_files() -> None:
    picked = pick_key_files(parse_tree(load_fixture("submission/tree.json")))
    assert picked[0] == "src/app.py"
    assert "tests/test_app.py" in picked
    assert len(picked) <= 4
    assert picked == ["src/app.py", "src/relay.py", "tests/test_app.py"]
    for excluded in ("node_modules/x/index.js", "package-lock.json", "docs/diagram.png"):
        assert excluded not in picked
    assert ".talentradar" not in picked
    assert "README.md" not in picked


def test_key_files_rank_entry_points_then_largest_sources_then_one_test() -> None:
    tree = [
        _entry("tests/test_small.py", 10),
        _entry("tests/test_big.py", 900),
        _entry("src/small.py", 10),
        _entry("src/big.py", 5000),
        _entry("lib/mid.py", 300),
        _entry("src/main.py", 1),
        _entry("scripts/loose.py", 99999),
    ]
    assert pick_key_files(tree, limit=10) == [
        "src/main.py",
        "src/big.py",
        "lib/mid.py",
        "src/small.py",
        "tests/test_big.py",
    ]


def test_key_files_entry_points_are_ordered_by_depth_then_by_name() -> None:
    tree = [
        _entry("src/deep/index.ts"),
        _entry("server.js"),
        _entry("app.py"),
        _entry("cli.py"),
        _entry("main.go"),
        _entry("manage.py"),
        _entry("index.js"),
    ]
    assert pick_key_files(tree, limit=10) == [
        "main.go",
        "app.py",
        "server.js",
        "index.js",
        "cli.py",
        "manage.py",
        "src/deep/index.ts",
    ]


def test_key_files_stop_at_the_limit() -> None:
    tree = [_entry(f"src/f{i}.py", i) for i in range(10)] + [_entry("main.py")]
    assert len(pick_key_files(tree)) == 4
    assert pick_key_files(tree)[0] == "main.py"
    assert len(pick_key_files(tree, limit=2)) == 2
    assert pick_key_files(tree, limit=0) == []


def test_key_files_exclude_vendored_generated_locked_minified_hidden_and_oversize() -> None:
    tree = [
        _entry("vendor/lib/main.py"),
        _entry("dist/app.js"),
        _entry("build/server.js"),
        _entry("src/vendor/x.py"),
        _entry("Vendor/Main.py"),
        _entry("node_modules/a/src/index.js"),
        _entry(".github/workflows/main.py"),
        _entry(".gitignore"),
        _entry("src/.gitkeep.py"),
        _entry("yarn.lock"),
        _entry("Cargo.lock"),
        _entry("src/app.min.js"),
        _entry("src/huge.py", 200 * 1024 + 1),
        _entry("src/logo.png", 5),
        _entry("src/data.json", 5),
        _entry("src/ok.py", 200 * 1024),
    ]
    assert pick_key_files(tree) == ["src/ok.py"]


def test_key_files_never_pick_a_test_as_an_entry_point_or_source() -> None:
    tree = [
        _entry("app.test.js"),
        _entry("src/app.spec.ts"),
        _entry("src/test_utils.py"),
        _entry("src/real.py", 5),
    ]
    assert pick_key_files(tree) == ["src/real.py", "app.test.js"]


def test_key_files_of_an_empty_tree_is_empty() -> None:
    assert pick_key_files([]) == []


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_app.py",
        "test/app.js",
        "src/__tests__/a.ts",
        "spec/models/user_spec.rb",
        "app_test.go",
        "src/app.test.tsx",
        "src/app.spec.ts",
        "test.py",
        "tests.py",
        "src/Spec/a.py",
    ],
)
def test_test_paths_are_recognised(path: str) -> None:
    assert is_test_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "src/app.py",
        "src/latest.py",
        "src/contest.py",
        "src/attestation/main.py",
        "README.md",
        "node_modules/pkg/test/a.js",
        "vendor/lib/tests/a.py",
    ],
)
def test_other_paths_are_not_tests(path: str) -> None:
    assert not is_test_path(path)


# --- commits ------------------------------------------------------------------------------


def test_parse_commits_reads_the_recorded_github_shape() -> None:
    commits = parse_commits(load_fixture("github_commits.json"))
    assert len(commits) == 3
    assert commits[0].sha == "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"
    assert commits[0].message == "Merge pull request #6 from demo-builder/patch-1"
    assert commits[0].claimed_at == datetime(2012, 3, 6, 23, 6, 50, tzinfo=UTC)
    assert commits[2].message == "first commit"


def test_parse_commits_reads_the_submission_newest_first() -> None:
    commits = _submission_commits()
    assert [c.sha[:4] for c in commits] == ["c35e", "e3b8", "e219", "e14a", "b27c", "a1f3"]
    assert commits[3].claimed_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert commits[3].message == "Relay design notes"


def test_parse_commits_stops_at_the_limit() -> None:
    payload = load_fixture("submission/commits.json")
    assert len(parse_commits(payload, limit=2)) == 2
    assert [c.sha[:4] for c in parse_commits(payload, limit=2)] == ["c35e", "e3b8"]
    many = [payload[0] | {"sha": _sha(n)} for n in range(100)]
    assert len(parse_commits(many)) == 60


def test_parse_commits_keeps_the_first_line_and_cuts_it_at_eighty_characters() -> None:
    base = load_fixture("submission/commits.json")[0]

    def commit_with(message: Any) -> dict[str, Any]:
        return {**base, "commit": {**base["commit"], "message": message}}

    lines = parse_commits(
        [
            commit_with("  Subject line\n\nlong body\nmore"),
            commit_with("x" * 200),
            commit_with(""),
            commit_with(None),
        ]
    )
    assert [c.message for c in lines] == ["Subject line", "x" * 80, "", ""]


def test_parse_commits_skips_unusable_rows_and_keeps_a_missing_date_as_none() -> None:
    base = load_fixture("submission/commits.json")[0]
    no_date = {**base, "sha": _sha(7), "commit": {"message": "no author"}}
    bad_date = {
        **base,
        "sha": _sha(8),
        "commit": {"message": "m", "author": {"date": "last week"}},
    }
    parsed = parse_commits(
        [
            "x",
            {"sha": "short", "commit": base["commit"]},
            {"commit": base["commit"]},
            no_date,
            bad_date,
            {**base, "sha": _sha(9), "commit": "oops"},
        ]
    )
    assert [(c.sha, c.claimed_at) for c in parsed] == [
        (_sha(7), None),
        (_sha(8), None),
        (_sha(9), None),
    ]


@pytest.mark.parametrize("payload", [None, {}, "x", {"message": "Not Found"}])
def test_parse_commits_of_a_non_list_is_empty(payload: object) -> None:
    assert parse_commits(payload) == []


# --- proof file ---------------------------------------------------------------------------


def test_proof_file_is_the_agreed_name() -> None:
    assert PROOF_FILE == ".talentradar"


def test_proof_matches_the_code_with_surrounding_whitespace() -> None:
    assert proof_matches("K7QX2M9PLA\n", "K7QX2M9PLA")
    assert proof_matches("K7QX2M9PLA\r\n", "K7QX2M9PLA")
    assert proof_matches("  \tK7QX2M9PLA \r\n\r\n", "K7QX2M9PLA")
    assert proof_matches(load_fixture("submission/talentradar.txt"), PROOF)


def test_proof_matches_a_file_saved_with_a_byte_order_mark() -> None:
    assert proof_matches("\N{BYTE ORDER MARK}K7QX2M9PLA\r\n", "K7QX2M9PLA")


def test_proof_does_not_match_missing_or_wrong_content() -> None:
    assert not proof_matches(None, "K7QX2M9PLA")
    assert not proof_matches("", "K7QX2M9PLA")
    assert not proof_matches("k7qx2m9pla", "K7QX2M9PLA")
    assert not proof_matches("K7QX2M9PLB", "K7QX2M9PLA")
    assert not proof_matches("K7QX2M9PLA extra", "K7QX2M9PLA")
    assert not proof_matches("K7QX2M9PLA\nK7QX2M9PLA", "K7QX2M9PLA")
    assert not proof_matches("K7QX2M9P LA", "K7QX2M9PLA")


def test_an_empty_code_never_matches() -> None:
    assert not proof_matches("", "")
    assert not proof_matches("  \n", "")


def test_new_proof_code_is_ten_characters_from_the_unambiguous_alphabet() -> None:
    assert PROOF_ALPHABET == "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = {new_proof_code() for _ in range(200)}
    assert len(codes) > 1
    for code in codes:
        assert len(code) == 10
        assert set(code) <= set(PROOF_ALPHABET)


# --- signals ------------------------------------------------------------------------------


def _signals(**overrides: Any) -> dict[str, int | float | bool]:
    """Signals for a small repo where every input is overridable."""
    arguments: dict[str, Any] = {
        "repo_created_at": STARTED + timedelta(minutes=5),
        "started_at": STARTED,
        "deadline_at": DEADLINE,
        "pushes": [],
        "late_pushes": 0,
        "commits": [],
        "tree": [],
        "readme_chars": 0,
        "writeup": {},
        "proof_found": False,
    }
    arguments.update(overrides)
    return compute_signals(**arguments)


def test_signals_of_the_submission_fixture() -> None:
    pushes = _submission_pushes()
    _, late = snapshot(pushes, DEADLINE)
    signals = _submission_signals(_submission_commits(), pushes, late_pushes=late)
    assert set(signals) == SIGNAL_KEYS
    assert signals["repo_created_in_window"] is True
    assert signals["pushes_in_window"] == 3
    assert signals["push_span_hours"] == 46.0
    assert signals["commits_at_snapshot"] == 6
    assert signals["largest_push_commits"] == 4
    assert signals["tests_present"] is True
    assert signals["readme_chars"] == len(load_fixture("submission/readme.md"))
    assert signals["writeup_words"] == 40 + 5 + 9 + 4
    assert signals["proof_found"] is True
    assert signals["late_pushes_ignored"] == 1


@pytest.mark.parametrize(("push_prefix", "carried"), [("a1", 1), ("b2", 1), ("c3", 4)])
def test_largest_push_commits_counts_commits_between_after_and_before(
    push_prefix: str, carried: int
) -> None:
    # a1's before is the zero id, absent from the list, so it counts to the end of the list
    # (itself only); b2 carries b2; c3 carries c3, e3, e2 and e1 (back to b2, its before).
    pushes = [p for p in _submission_pushes() if p.sha.startswith(push_prefix)]
    assert len(pushes) == 1
    signals = _signals(commits=_submission_commits(), pushes=pushes)
    assert signals["largest_push_commits"] == carried


def test_largest_push_commits_is_the_biggest_of_the_pushes_in_the_window() -> None:
    signals = _signals(commits=_submission_commits(), pushes=_submission_pushes())
    assert signals["largest_push_commits"] == 4


def test_a_before_that_is_not_in_the_list_counts_every_commit_to_the_end() -> None:
    commits = [_line(n) for n in (9, 8, 7, 6, 5)]
    pushes = [_push(8, 1, STARTED + timedelta(hours=1))]
    signals = _signals(commits=commits, pushes=pushes)
    assert signals["largest_push_commits"] == 4


def test_a_push_whose_sha_is_not_in_the_list_carries_nothing() -> None:
    commits = [_line(n) for n in (9, 8, 7)]
    pushes = [_push(1, 2, STARTED + timedelta(hours=1))]
    assert _signals(commits=commits, pushes=pushes)["largest_push_commits"] == 0


def test_a_push_that_moves_the_branch_backwards_carries_nothing() -> None:
    commits = [_line(n) for n in (9, 8, 7)]
    pushes = [_push(7, 9, STARTED + timedelta(hours=1), kind="force_push")]
    assert _signals(commits=commits, pushes=pushes)["largest_push_commits"] == 0


def test_a_single_push_of_forty_backdated_commits_is_one_push_and_forty_commits() -> None:
    long_ago = STARTED - timedelta(days=400)
    commits = [_line(n, claimed_at=long_ago) for n in range(40, 0, -1)]
    pushes = [_push(40, None, DEADLINE - timedelta(minutes=1))]
    signals = _signals(commits=commits, pushes=pushes)
    assert snapshot(pushes, DEADLINE)[0] == pushes[0]
    assert signals["pushes_in_window"] == 1
    assert signals["push_span_hours"] == 0.0
    assert signals["largest_push_commits"] == 40
    assert signals["commits_at_snapshot"] == 40


def test_signals_with_nothing_pushed_are_zero() -> None:
    signals = _signals(pushes=[], commits=[], tree=[], proof_found=False, readme_chars=0)
    assert signals == {
        "repo_created_in_window": True,
        "pushes_in_window": 0,
        "push_span_hours": 0.0,
        "commits_at_snapshot": 0,
        "largest_push_commits": 0,
        "tests_present": False,
        "readme_chars": 0,
        "writeup_words": 0,
        "proof_found": False,
        "late_pushes_ignored": 0,
    }


def test_pushes_outside_the_window_are_not_counted_in_it() -> None:
    pushes = [
        _push(3, 2, DEADLINE + timedelta(seconds=1)),
        _push(2, 1, STARTED + timedelta(hours=10)),
        _push(1, None, STARTED - timedelta(seconds=1)),
    ]
    signals = _signals(pushes=pushes, late_pushes=1)
    assert signals["pushes_in_window"] == 1
    assert signals["push_span_hours"] == 0.0
    assert signals["late_pushes_ignored"] == 1


def test_push_span_is_hours_between_the_first_and_last_push_in_the_window() -> None:
    pushes = [
        _push(3, 2, STARTED + timedelta(hours=30, minutes=30)),
        _push(2, 1, STARTED + timedelta(hours=5)),
        _push(1, None, STARTED + timedelta(hours=2)),
    ]
    assert _signals(pushes=pushes)["push_span_hours"] == 28.5


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        (STARTED, True),
        (STARTED - timedelta(minutes=15), True),
        (STARTED - timedelta(minutes=15, seconds=1), False),
        (STARTED - timedelta(days=3), False),
        (DEADLINE, True),
        (DEADLINE + timedelta(seconds=1), False),
        (None, False),
    ],
)
def test_repo_created_in_window(created_at: datetime | None, expected: bool) -> None:
    assert _signals(repo_created_at=created_at)["repo_created_in_window"] is expected


def test_tests_present_looks_at_authored_paths_only() -> None:
    assert _signals(tree=[_entry("src/a.py"), _entry("tests/a.py")])["tests_present"] is True
    assert _signals(tree=[_entry("src/a.py")])["tests_present"] is False
    vendored = [_entry("node_modules/pkg/test/a.js"), _entry("src/a.py")]
    assert _signals(tree=vendored)["tests_present"] is False


def test_writeup_words_count_the_four_text_fields_and_ignore_the_video_link() -> None:
    writeup = {
        "decisions": "one two  three\nfour",
        "tradeoffs": "five",
        "ai_usage": "",
        "next_steps": "six seven",
        "video_url": "https://www.loom.com/share/a-b-c d e f",
        "unknown": "ignored words here",
    }
    assert _signals(writeup=writeup)["writeup_words"] == 7
    assert _signals(writeup={})["writeup_words"] == 0


def test_signal_types_are_ints_floats_and_bools() -> None:
    signals = _signals()
    bools = {"repo_created_in_window", "tests_present", "proof_found"}
    for key, value in signals.items():
        if key in bools:
            assert isinstance(value, bool), key
        elif key == "push_span_hours":
            assert isinstance(value, float), key
        else:
            assert isinstance(value, int) and not isinstance(value, bool), key

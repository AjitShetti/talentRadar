"""Practice fixtures: every response the Practice tests replay.

LeetCode fixtures are written by hand; no script requests leetcode.com. GitHub
fixtures are recorded by ``scripts/capture_fixtures.py --practice``, and the
``submission/`` folder is hand-built.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

PRACTICE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "practice"
CAPTURE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "capture_fixtures.py"
#: Identities a recorded GitHub fixture may carry: the fictional stand-ins the capture
#: writes, and GitHub's own mascot account. Anything else is a real person.
ALLOWED_LOGINS = frozenset({"demo-builder", "octocat"})
ALLOWED_NAMES = frozenset({"Demo Builder", "The Octocat"})
ALLOWED_USER_IDS = frozenset({90000001, 583231})
EMAIL_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
#: An SSH clone URL names a repository, not a person.
SSH_REMOTE = "git@github.com:"


def load_fixture(name: str) -> Any:
    """Load a practice fixture by its path under ``PRACTICE_FIXTURES``: JSON for ``.json``, text otherwise."""
    text = (PRACTICE_FIXTURES / name).read_text(encoding="utf-8")
    if name.endswith(".json"):
        return json.loads(text)
    return text


def _identity_problems(source: str, node: Any, key: str = "") -> list[str]:
    """Logins, user ids and person names in one parsed fixture that are not on the allowlist."""
    problems: list[str] = []
    if isinstance(node, dict):
        if "login" in node:
            if node["login"] not in ALLOWED_LOGINS:
                problems.append(f"{source}: login {node['login']!r} is not on the allowlist")
            if node.get("id") not in ALLOWED_USER_IDS:
                problems.append(f"{source}: user id {node.get('id')!r} is not on the allowlist")
        if key in ("author", "committer") and "name" in node and node["name"] not in ALLOWED_NAMES:
            problems.append(f"{source}: person name {node['name']!r} is not on the allowlist")
        for child_key, child in node.items():
            problems += _identity_problems(source, child, child_key)
    elif isinstance(node, list):
        for child in node:
            problems += _identity_problems(source, child, key)
    return problems


def _strings(node: Any) -> Iterable[str]:
    """Every string in one parsed fixture, keys included."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, child in node.items():
            yield key
            yield from _strings(child)
    elif isinstance(node, list):
        for child in node:
            yield from _strings(child)


def _email_problems(source: str, strings: Iterable[str]) -> list[str]:
    """Email addresses in any of the strings, bar SSH clone URLs."""
    return [
        f"{source}: email address {match.group()!r}"
        for text in strings
        if not text.startswith(SSH_REMOTE)
        for match in EMAIL_ADDRESS.finditer(text)
    ]


def _recorded_github_fixtures() -> list[tuple[str, Any]]:
    """Each recorded GitHub fixture as (file name, parsed JSON); markdown stays text."""
    documents: list[tuple[str, Any]] = []
    for path in sorted(PRACTICE_FIXTURES.glob("github_*")):
        text = path.read_text(encoding="utf-8")
        documents.append((path.name, json.loads(text) if path.suffix == ".json" else text))
    return documents


def test_every_practice_fixture_parses() -> None:
    fixtures = sorted(PRACTICE_FIXTURES.rglob("*.json"))
    assert fixtures, f"no practice fixtures under {PRACTICE_FIXTURES}"
    for path in fixtures:
        load_fixture(path.relative_to(PRACTICE_FIXTURES).as_posix())


def test_no_fixture_was_recorded_from_leetcode_by_script() -> None:
    assert "leetcode.com" not in CAPTURE_SCRIPT.read_text(encoding="utf-8")


def test_recorded_github_fixtures_name_only_allowlisted_identities() -> None:
    problems = [
        problem
        for name, document in _recorded_github_fixtures()
        for problem in _identity_problems(name, document)
    ]
    assert not problems, problems


def test_recorded_github_fixtures_carry_no_email_address() -> None:
    problems = [
        problem
        for name, document in _recorded_github_fixtures()
        for problem in _email_problems(name, _strings(document))
    ]
    assert not problems, problems


def test_allowlist_flags_an_unknown_login_name_or_id() -> None:
    sample = {
        "author": {"login": "unknown-login", "id": 4242},
        "commit": {"author": {"name": "Unlisted Person"}},
    }
    problems = " ".join(_identity_problems("sample.json", sample))
    assert "unknown-login" in problems
    assert "4242" in problems
    assert "Unlisted Person" in problems


def test_allowlist_accepts_the_fictional_identities() -> None:
    sample = {
        "author": {"login": "demo-builder", "id": 90000001},
        "commit": {"author": {"name": "Demo Builder"}},
    }
    assert _identity_problems("sample.json", sample) == []


def test_email_check_flags_an_address_but_not_an_ssh_remote() -> None:
    assert _email_problems("sample.json", ["git@github.com:octocat/Hello-World.git"]) == []
    assert _email_problems("sample.json", ["reach me at reader@example.org"]) != []

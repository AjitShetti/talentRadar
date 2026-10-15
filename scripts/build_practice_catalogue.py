"""
scripts/build_practice_catalogue.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Build ``data/practice/leetcode_free.json``, the Practice question catalogue, from
responses a person saved from leetcode.com by hand.

    python scripts/build_practice_catalogue.py saved-1.json saved-2.json \\
        --out data/practice/leetcode_free.json

How to get the saved files: open https://leetcode.com/problemset/ in your own browser,
scroll the whole list, then in DevTools -> Network select each ``graphql`` request the
page made for the problem list and save its response. This script never requests
leetcode.com (robots.txt disallows it), and neither does the server or CI.

Only free questions are kept, and only five fields of each: id, slug, title, difficulty
and topic slugs. Statements, hints, acceptance rates and paid-only questions are dropped.
The file holds positional rows, sorted by id:

    {"generated_at": "<iso>", "source": "...",
     "questions": [[1, "two-sum", "Two Sum", "easy", ["array", "hash-table"]], ...]}

Nothing is written when no free question is found, so a wrong file cannot replace a good
catalogue with an empty one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from domain.practice_pick import Question
from services.practice.catalogue import CATALOGUE_PATH, parse_saved_responses

SOURCE = "leetcode.com/problemset (saved by hand)"


def _row(question: Question) -> list[Any]:
    return [question.id, question.slug, question.title, question.difficulty, list(question.topics)]


def _document(questions: Sequence[Question], generated_at: str) -> str:
    """The catalogue file's text: a header, then one row per line so diffs stay readable."""
    rows = ",\n    ".join(json.dumps(_row(question), ensure_ascii=False) for question in questions)
    return (
        "{\n"
        f'  "generated_at": {json.dumps(generated_at)},\n'
        f'  "source": {json.dumps(SOURCE)},\n'
        '  "questions": [\n'
        f"    {rows}\n"
        "  ]\n"
        "}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the Practice question catalogue from saved leetcode.com responses."
    )
    parser.add_argument("saved", nargs="+", type=Path, help="a saved graphql response (JSON)")
    parser.add_argument(
        "--out",
        type=Path,
        default=CATALOGUE_PATH,
        help=f"where to write (default {CATALOGUE_PATH})",
    )
    args = parser.parse_args(argv)

    payloads: list[Any] = []
    for path in args.saved:
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"cannot read {path}: {exc}", file=sys.stderr)
            return 1

    questions = parse_saved_responses(payloads)
    if not questions:
        print("no free questions found in the saved files; nothing written", file=sys.stderr)
        return 1

    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_document(questions, generated_at), encoding="utf-8", newline="\n")

    by_difficulty = Counter(question.difficulty for question in questions)
    print(
        f"wrote {len(questions)} free questions to {args.out} "
        f"(easy {by_difficulty['easy']}, medium {by_difficulty['medium']}, "
        f"hard {by_difficulty['hard']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

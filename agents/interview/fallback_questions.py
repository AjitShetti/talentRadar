"""
agents/interview/fallback_questions.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Static question bank used as a fallback when the LLM is unavailable or
rate-limited (Groq: 20 req/min, 2 000 req/day on the free tier).

Design notes
------------
* Questions are keyed by (track, difficulty) tuples.
* The fallback bank is intentionally small — it just keeps the session
  alive, not replace the LLM.  The node retries the LLM on the next turn.
* Questions are drawn in insertion order; the node shuffles them per
  session so the same user never gets the exact same sequence twice.
"""

from __future__ import annotations

import random
from typing import Iterator

# ---------------------------------------------------------------------------
# Static fallback bank
# ---------------------------------------------------------------------------
# fmt: off
_FALLBACK_QUESTIONS: dict[tuple[str, str], list[str]] = {

    # --- Python DSA ----------------------------------------------------------
    ("python_dsa", "beginner"): [
        "What is the time complexity of accessing an element in a Python list by index?",
        "How does a Python dictionary store key-value pairs internally?",
        "Explain the difference between a stack and a queue with a simple example.",
        "What does it mean for an algorithm to be O(n log n)?",
        "How would you check whether a string is a palindrome in Python?",
    ],
    ("python_dsa", "mid"): [
        "Explain the difference between BFS and DFS. When would you prefer one over the other?",
        "How would you find the two numbers in a list that add up to a given target efficiently?",
        "What is a heap and how does Python's heapq module work?",
        "Describe how you would implement an LRU cache without using OrderedDict.",
        "What is dynamic programming? Give an example of a problem where it helps.",
    ],
    ("python_dsa", "senior"): [
        "Walk me through how you would design a system to find the k most frequent words in a stream of text with O(n log k) complexity.",
        "Explain the sliding window technique and give an example where it reduces O(n²) to O(n).",
        "How does Python's Timsort work and why is it well-suited for real-world data?",
        "Describe how you would solve the 'word ladder' problem and analyse the complexity.",
        "When would you choose a red-black tree over a hash map for an ordered mapping requirement?",
    ],

    # --- Python Backend -------------------------------------------------------
    ("python_backend", "beginner"): [
        "What is the difference between a GET and POST HTTP request?",
        "How do you define a route in FastAPI and what is a path parameter?",
        "What is a virtual environment in Python and why should you use one?",
        "Explain what a foreign key is in a relational database.",
        "What does async/await mean in Python?",
    ],
    ("python_backend", "mid"): [
        "How does SQLAlchemy's async session work and why must you avoid using sync calls inside an async context?",
        "Explain JWT authentication: what is in a token, how is it verified, and what are its limitations?",
        "How would you implement rate limiting on a FastAPI endpoint?",
        "What is the N+1 query problem and how do you solve it in SQLAlchemy?",
        "Describe when you would use Celery for a task and how you would monitor it.",
    ],
    ("python_backend", "senior"): [
        "How would you design a multi-tenant API where tenant data must be fully isolated at the database level?",
        "Explain the differences between optimistic and pessimistic locking and when you'd use each.",
        "How would you implement zero-downtime deployments for a FastAPI application backed by PostgreSQL?",
        "Describe how you would structure a large FastAPI project for a team of 10 engineers.",
        "What are the trade-offs between using a monolith versus microservices for a growing startup?",
    ],

    # --- SQL -----------------------------------------------------------------
    ("sql", "beginner"): [
        "What is the difference between INNER JOIN and LEFT JOIN?",
        "What does GROUP BY do and when do you use HAVING instead of WHERE?",
        "Explain what a primary key and a unique constraint do.",
        "What is the difference between DELETE and TRUNCATE?",
        "How do you count the number of rows that satisfy a condition in SQL?",
    ],
    ("sql", "mid"): [
        "Explain window functions. Give an example using ROW_NUMBER and PARTITION BY.",
        "What is a CTE and how does it differ from a subquery?",
        "How do indexes improve query performance? When can adding an index make things worse?",
        "Explain the difference between a clustered and a non-clustered index.",
        "How would you find the second-highest salary in an employees table?",
    ],
    ("sql", "senior"): [
        "Walk me through how you would investigate and fix a slow PostgreSQL query using EXPLAIN ANALYZE.",
        "How do database transactions and isolation levels (READ COMMITTED, REPEATABLE READ, SERIALIZABLE) affect concurrent writes?",
        "When would you use PostgreSQL's JSONB type over normalised columns, and what are the indexing implications?",
        "Describe a schema design for a multi-currency financial ledger ensuring no rounding errors.",
        "How would you implement a soft-delete pattern in a way that keeps unique constraints working correctly?",
    ],

    # --- System Design -------------------------------------------------------
    ("system_design", "beginner"): [
        "What is the difference between vertical and horizontal scaling?",
        "What does a load balancer do and why is it useful?",
        "Explain the difference between SQL and NoSQL databases in simple terms.",
        "What is caching and why is it used?",
        "What is an API gateway?",
    ],
    ("system_design", "mid"): [
        "How would you design a URL shortener like bit.ly? Walk me through the high-level components.",
        "Explain the CAP theorem and give a real-world example of a trade-off it forces.",
        "How does consistent hashing work and why is it used in distributed caches?",
        "What is a message queue? Compare Kafka and RabbitMQ for a real-time analytics pipeline.",
        "How would you design a rate limiter for an API that serves 10 000 requests per second?",
    ],
    ("system_design", "senior"): [
        "Design a distributed job scheduler that can run 1 million tasks per day with exactly-once semantics.",
        "How would you architect a real-time collaborative document editor (like Google Docs)? Focus on conflict resolution.",
        "Walk me through designing a global CDN. How do you handle cache invalidation at scale?",
        "How would you migrate a monolithic application to microservices without downtime?",
        "Design a notification system that delivers push, email, and SMS notifications with guaranteed delivery and deduplication.",
    ],
}
# fmt: on


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_fallback_question(
    track: str,
    difficulty: str,
    used_questions: set[str],
) -> str | None:
    """
    Return a random fallback question not yet asked in this session.

    Args:
        track:          Interview track key (e.g. "python_dsa").
        difficulty:     Difficulty key (e.g. "mid").
        used_questions: Set of question texts already posed this session.

    Returns:
        A question string, or None if all fallback questions are exhausted.
    """
    pool = _FALLBACK_QUESTIONS.get((track, difficulty), [])
    available = [q for q in pool if q not in used_questions]
    if not available:
        return None
    return random.choice(available)


def iter_fallback_questions(
    track: str,
    difficulty: str,
) -> Iterator[str]:
    """
    Yield shuffled fallback questions for a given track/difficulty.
    Useful for testing the full bank without running the LLM.
    """
    pool = list(_FALLBACK_QUESTIONS.get((track, difficulty), []))
    random.shuffle(pool)
    yield from pool

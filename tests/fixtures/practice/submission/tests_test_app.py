"""Tests for the webhook relay (src/app.py in the submitted repository)."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3

import httpx
import pytest
from src.app import Delivery, backoff, handle, verify  # type: ignore[import-not-found]

SECRET = "test-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE deliveries (id INTEGER PRIMARY KEY, event TEXT, body BLOB, received_at REAL)"
    )
    return conn


def ok_client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))


def test_verify_accepts_matching_signature() -> None:
    body = b'{"action":"opened"}'
    assert verify(Delivery("push", body, sign(body)), SECRET)


def test_verify_rejects_wrong_secret() -> None:
    body = b'{"action":"opened"}'
    assert not verify(Delivery("push", body, sign(body, "other")), SECRET)


def test_backoff_doubles() -> None:
    assert [backoff(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]


def test_bad_signature_is_rejected_and_not_logged(db: sqlite3.Connection) -> None:
    result = handle(Delivery("push", b"{}", "sha256=00"), db, ok_client())
    assert result == {"status": "rejected", "reason": "bad signature"}
    assert db.execute("SELECT COUNT(*) FROM deliveries").fetchone() == (0,)


def test_good_delivery_is_logged_then_forwarded(db: sqlite3.Connection) -> None:
    body = b'{"action":"opened"}'
    result = handle(Delivery("pull_request", body, sign(body)), db, ok_client())
    assert result == {"status": "forwarded", "id": 1, "action": "opened"}

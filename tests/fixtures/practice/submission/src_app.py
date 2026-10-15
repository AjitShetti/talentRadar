"""Webhook relay: check a GitHub delivery's signature, log it, forward it with retries."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from dataclasses import dataclass

import httpx

SECRET = os.environ.get("RELAY_SECRET", "")
TARGET = os.environ.get("RELAY_TARGET", "http://localhost:9000/hook")
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Delivery:
    event: str
    body: bytes
    signature: str


def verify(delivery: Delivery, secret: str = SECRET) -> bool:
    """Compare the HMAC-SHA256 of the body with the signature header in constant time."""
    expected = "sha256=" + hmac.new(secret.encode(), delivery.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, delivery.signature)


def backoff(attempt: int) -> float:
    """Seconds to wait before retry number *attempt*: 1, 2, 4, 8."""
    return float(2 ** max(0, attempt - 1))


def log_delivery(db: sqlite3.Connection, delivery: Delivery) -> int:
    cursor = db.execute(
        "INSERT INTO deliveries (event, body, received_at) VALUES (?, ?, ?)",
        (delivery.event, delivery.body, time.time()),
    )
    db.commit()
    return int(cursor.lastrowid or 0)


def forward(delivery: Delivery, client: httpx.Client) -> bool:
    """Post the body to TARGET. Server errors and timeouts retry; client errors do not."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.post(
                TARGET, content=delivery.body, headers={"X-GitHub-Event": delivery.event}
            )
        except httpx.TransportError:
            time.sleep(backoff(attempt))
            continue
        if response.status_code < 500:
            return response.status_code < 400
        time.sleep(backoff(attempt))
    return False


def handle(delivery: Delivery, db: sqlite3.Connection, client: httpx.Client) -> dict[str, object]:
    if not verify(delivery):
        return {"status": "rejected", "reason": "bad signature"}
    delivery_id = log_delivery(db, delivery)
    payload = json.loads(delivery.body or b"{}")
    delivered = forward(delivery, client)
    return {
        "status": "forwarded" if delivered else "failed",
        "id": delivery_id,
        "action": payload.get("action"),
    }

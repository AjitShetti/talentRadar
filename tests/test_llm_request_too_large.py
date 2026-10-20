"""
tests/test_llm_request_too_large.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A request Groq says is too large will be too large again: ``services.llm`` must name that error and
must not retry it, where a rate limit that will clear in a few seconds is retried as before.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import groq
import httpx
import pytest
import tenacity

from services import llm

URL = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

TOO_LARGE = (
    "Request too large for model `openai/gpt-oss-120b` in organization `org_x` service tier "
    "`on_demand` on tokens per minute (TPM): Limit 8000, Requested 11000, please reduce your "
    "message size and try again."
)
RATE_LIMITED = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_x` service tier "
    "`on_demand` on tokens per minute (TPM): Limit 8000, Used 7500, Requested 2000. "
    "Please try again in 4.2s."
)


def failure(status: int, message: str) -> groq.APIStatusError:
    response = httpx.Response(status, request=URL, json={"error": {"message": message}})
    body = {"message": message, "type": "tokens", "code": "rate_limit_exceeded"}
    if status == 429:
        return groq.RateLimitError(message, response=response, body=body)
    return groq.APIStatusError(message, response=response, body=body)


@pytest.mark.parametrize(
    ("status", "message", "too_large"),
    [
        (413, TOO_LARGE, True),
        (413, "anything at all", True),
        (429, TOO_LARGE, True),
        (429, "REQUEST TOO LARGE for model x", True),
        (429, RATE_LIMITED, False),
        (400, "bad request", False),
        (500, "server error", False),
    ],
)
def test_a_request_that_is_too_large_is_told_from_one_that_is_rate_limited(
    status: int, message: str, too_large: bool
) -> None:
    assert llm.is_request_too_large(failure(status, message)) is too_large


def test_other_errors_are_not_too_large() -> None:
    assert llm.is_request_too_large(RuntimeError("boom")) is False
    assert llm.is_request_too_large(TimeoutError("slow")) is False


@pytest.mark.parametrize(
    ("status", "message", "retryable"),
    [
        (413, TOO_LARGE, False),
        (429, TOO_LARGE, False),
        (429, RATE_LIMITED, True),
        (500, "server error", True),
        (400, "bad request", False),
    ],
)
def test_only_errors_a_retry_can_fix_are_retried(
    status: int, message: str, retryable: bool
) -> None:
    assert llm._is_retryable(failure(status, message)) is retryable


def client_raising(error: Exception) -> tuple[SimpleNamespace, AsyncMock]:
    create = AsyncMock(side_effect=error)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), create


@pytest.mark.parametrize("status", [413, 429])
async def test_a_too_large_request_is_sent_once(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    client, create = client_raising(failure(status, TOO_LARGE))
    monkeypatch.setattr(llm, "get_llm", lambda: client)
    monkeypatch.setattr(llm._chat.retry, "wait", tenacity.wait_none())

    with pytest.raises(groq.APIStatusError):
        await llm._chat("system", "user")

    assert create.await_count == 1


async def test_a_rate_limit_is_still_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client, create = client_raising(failure(429, RATE_LIMITED))
    monkeypatch.setattr(llm, "get_llm", lambda: client)
    monkeypatch.setattr(llm._chat.retry, "wait", tenacity.wait_none())

    with pytest.raises(groq.RateLimitError):
        await llm._chat("system", "user")

    assert create.await_count == 3

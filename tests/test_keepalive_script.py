"""
tests/test_keepalive_script.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Drives ``scripts/keepalive.sh`` against a local HTTP server that behaves like a
sleeping Render instance.

The workflow went red whenever Render took longer than curl's three 90-second
attempts to wake (curl exit 28). These tests pin the replacement's contract: a
slow wake - hung requests, then 503s from the proxy - still ends green; a wrong
URL fails fast; a service that never comes up fails at the deadline.

Timings are scaled down (1 s attempts instead of 60 s) so the suite stays fast.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "keepalive.sh"

BASH = shutil.which("bash")
CURL = shutil.which("curl")
pytestmark = pytest.mark.skipif(not (BASH and CURL), reason="needs bash and curl")


class _Render:
    """Scripted responses: each entry is ('hang', secs) or ('status', code)."""

    def __init__(self, script: list[tuple[str, int]]) -> None:
        self.script = script
        self.requests = 0


def _serve(render: _Render) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            index = min(render.requests, len(render.script) - 1)
            render.requests += 1
            kind, value = render.script[index]
            if kind == "hang":
                time.sleep(value)
                return
            payload = b'{"status":"healthy"}' if value == 200 else b"waking"
            self.send_response(value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/health"
    finally:
        server.shutdown()


def _run(url: str, *, deadline: int = 20) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "URL": url,
        "DEADLINE_SECONDS": str(deadline),
        "ATTEMPT_SECONDS": "1",
        "RETRY_DELAY_SECONDS": "0",
    }
    return subprocess.run(
        [BASH or "bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=deadline + 30,
    )


@pytest.fixture
def render(request: pytest.FixtureRequest) -> Iterator[tuple[_Render, str]]:
    fake = _Render(request.param)
    for url in _serve(fake):
        yield fake, url


@pytest.mark.parametrize("render", [[("status", 200)]], indirect=True)
def test_awake_instance_is_healthy_first_time(render: tuple[_Render, str]) -> None:
    fake, url = render
    result = _run(url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert fake.requests == 1


@pytest.mark.parametrize(
    "render",
    # Three hung attempts is exactly what used to fail the workflow.
    [[("hang", 3), ("hang", 3), ("hang", 3), ("status", 503), ("status", 502), ("status", 200)]],
    indirect=True,
)
def test_slow_wake_still_ends_green(render: tuple[_Render, str]) -> None:
    fake, url = render
    result = _run(url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "healthy after" in result.stdout
    assert fake.requests == 6


@pytest.mark.parametrize("render", [[("status", 404)]], indirect=True)
def test_wrong_url_fails_fast_without_retrying(render: tuple[_Render, str]) -> None:
    fake, url = render
    result = _run(url)
    assert result.returncode == 2, result.stdout + result.stderr
    assert fake.requests == 1


@pytest.mark.parametrize("render", [[("status", 503)]], indirect=True)
def test_service_that_never_wakes_fails_at_the_deadline(render: tuple[_Render, str]) -> None:
    _, url = render
    started = time.monotonic()
    result = _run(url, deadline=4)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "did not answer 200" in result.stdout
    assert time.monotonic() - started < 15


def test_refused_connection_counts_as_still_waking() -> None:
    # Port 9 on loopback: nothing listens, so every attempt is refused.
    result = _run("http://127.0.0.1:9/health", deadline=3)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "attempt 2" in result.stdout

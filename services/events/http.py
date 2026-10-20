"""
services/events/http.py
~~~~~~~~~~~~~~~~~~~~~~~
The one way an Events source reads a URL.

:func:`get_bounded` streams the body and stops reading the moment it passes
:data:`MAX_BYTES`. A body over the cap is an error, never a truncated payload:
a parser handed half a feed would read it as a complete, shorter one and the
store would then treat the missing events as gone.

Every request carries the project's User-Agent and the same timeout. Redirects
are not followed unless the caller asks (Luma and GDG never do): a redirect is
a new address nobody vetted.
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

USER_AGENT = "TalentRadar/1.0 (+https://github.com/AjitShetti/talentRadar)"
MAX_BYTES = 1_048_576
TIMEOUT_S = 15.0


async def get_bounded(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    follow_redirects: bool = False,
) -> bytes:
    """The body of ``GET url`` as bytes.

    Raises ``httpx.HTTPError`` on a transport error or any non-2xx status
    (a redirect that was not followed included), and ``ValueError`` when the
    body passes :data:`MAX_BYTES` (the download stops there). The caller
    records the outcome against the source's health counter.

    ``headers`` are added to the request; a ``User-Agent`` among them is
    ignored so every request names this project.
    """
    sent = {name: value for name, value in (headers or {}).items() if name.lower() != "user-agent"}
    sent["User-Agent"] = USER_AGENT
    async with client.stream(
        "GET", url, headers=sent, timeout=TIMEOUT_S, follow_redirects=follow_redirects
    ) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                parts = urlsplit(url)  # host and path only: a query can carry a token
                raise ValueError(
                    f"{parts.hostname or 'response'}{parts.path} is larger than {MAX_BYTES} bytes"
                )
    return bytes(body)

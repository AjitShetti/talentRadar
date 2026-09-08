"""
api/utils/uploads.py
~~~~~~~~~~~~~~~~~~~~
Shared helpers for handling multipart uploads safely.

Every endpoint that accepts a file must read it through :func:`read_capped`.
A bare ``await upload.read()`` buffers the entire request body in memory
before any check can reject it, so one request can exhaust a small container
— which is exactly the shape of instance the free hosting tiers give you.
"""

from __future__ import annotations

import os
import re

from fastapi import HTTPException, UploadFile, status

#: Read size per iteration. Small enough that the cap trips long before the
#: process has buffered anything meaningful.
UPLOAD_CHUNK_BYTES = 64 * 1024


async def read_capped(upload: UploadFile, max_bytes: int, *, label: str = "File") -> bytes:
    """Read ``upload`` in chunks, rejecting it as soon as it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{label} exceeds the {max_bytes // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def safe_filename(raw: str | None, *, fallback: str = "upload.bin") -> str:
    """Reduce a client-supplied filename to a harmless display label.

    The client controls this string entirely. Keep only the basename (so
    ``../../etc/passwd`` cannot survive as a path), drop control characters and
    anything outside a conservative allowlist, and cap the length.
    """
    candidate = os.path.basename((raw or "").replace("\\", "/").strip()) or fallback
    candidate = re.sub(r"[^A-Za-z0-9._ -]", "_", candidate)
    candidate = candidate.lstrip(".") or fallback
    return candidate[:120]


def content_disposition(filename: str) -> str:
    """Build a ``Content-Disposition`` value that cannot inject headers.

    ``filename`` here is derived from LLM output (the candidate name it read
    off a resume), so an unescaped interpolation could smuggle CR/LF or a
    quote into the response headers.
    """
    safe = safe_filename(filename, fallback="download")
    return f'attachment; filename="{safe}"'

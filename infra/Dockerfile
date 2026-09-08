# ─────────────────────────────────────────────────────────────────────
# TalentRadar API — production Dockerfile
#
# Sized to fit a free hosting tier. Three things used to make that
# impossible, and each is now opt-in rather than baked in:
#
#   * PyTorch (~1 GB installed), pulled in only by sentence-transformers for
#     one scorer. ml/scorers.py now falls back to the same all-MiniLM-L6-v2
#     weights under ONNX Runtime, which ships with chromadb's embedding
#     function anyway. Add it back with INSTALL_SEMANTIC=true.
#   * texlive-latex-extra (~1 GB), needed for three style files the resume
#     template uses. api/utils/pdf_renderer.py renders the same resume with
#     PyMuPDF instead. Add TeX back with INSTALL_TEXLIVE=true for
#     pixel-identical output.
#   * The browser scraping stack — scrapling, playwright, patchright and a
#     Camoufox Firefox download (~300 MB plus shared libraries). It is one
#     unit: scrapling.fetchers imports playwright and patchright at module
#     level, so its TLS-impersonation tier cannot be had without them, and
#     Camoufox on top OOMs a 512 MB instance. Without it,
#     ingestion/scrapling_manager.py uses httpx with browser-like headers.
#     Add with INSTALL_STEALTH=true.
#
# The ChromaDB *server* is gone entirely: embeddings live in Postgres via
# pgvector, so there is no second container and no persistent volume.
# ─────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ARG INSTALL_SEMANTIC=false
ARG INSTALL_STEALTH=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# A virtualenv rather than the system site-packages: one directory to copy
# into the runtime stage, and nothing from the build toolchain rides along.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./

# hatchling builds the wheel from [tool.hatch.build.targets.wheel].packages,
# so those directories have to exist here or `pip install .` fails with
# "Unable to determine which files to ship" — which is exactly what the
# previous version of this file did, having copied only pyproject.toml.
COPY agents/ agents/
COPY api/ api/
COPY config/ config/
COPY ingestion/ ingestion/
COPY ml/ ml/
COPY storage/ storage/

RUN pip install --upgrade pip && \
    EXTRAS="" && \
    if [ "$INSTALL_SEMANTIC" = "true" ]; then EXTRAS="semantic"; fi && \
    if [ "$INSTALL_STEALTH" = "true" ]; then EXTRAS="${EXTRAS:+$EXTRAS,}stealth"; fi && \
    if [ -n "$EXTRAS" ]; then pip install ".[$EXTRAS]"; else pip install .; fi

# chromadb pulls in the Kubernetes client (~85 MB) for its *server's* auth
# providers. Nothing here runs the Chroma server — the package is present only
# for its ONNX embedding function — so it is dead weight in the image.
# Tolerant of failure: if a future chromadb genuinely needs it, the install
# stays as it was rather than the build breaking.
RUN pip uninstall -y kubernetes 2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ARG INSTALL_TEXLIVE=false
ARG INSTALL_STEALTH=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/home/app \
    ANONYMIZED_TELEMETRY=False

WORKDIR /app

# curl is for the healthcheck; libpq5 is asyncpg/psycopg2's runtime library.
# TeX and the browser libraries are opt-in — see the header.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
    && if [ "$INSTALL_TEXLIVE" = "true" ]; then \
        apt-get install -y --no-install-recommends \
            texlive-latex-base \
            texlive-latex-extra \
            texlive-fonts-recommended ; \
    fi \
    && if [ "$INSTALL_STEALTH" = "true" ]; then \
        apt-get install -y --no-install-recommends \
            libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
            libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
            libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 libgtk-3-0 ; \
    fi \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Create the unprivileged user before warming the model cache, so the cache
# is written where that user can read it.
RUN useradd --create-home --shell /bin/bash app

COPY --chown=app:app api/ api/
COPY --chown=app:app services/ services/
COPY --chown=app:app core/ core/
COPY --chown=app:app domain/ domain/
COPY --chown=app:app config/ config/
COPY --chown=app:app storage/ storage/
COPY --chown=app:app agents/ agents/
COPY --chown=app:app ingestion/ ingestion/
COPY --chown=app:app ml/ ml/
COPY --chown=app:app data/ data/
COPY --chown=app:app scripts/ scripts/
COPY --chown=app:app alembic.ini alembic.ini

RUN chown -R app:app /app
USER app

# Bake the embedding model into the image (~90 MB extracted). Without this the
# first semantic search downloads it, which is slow, needs egress the host may
# not have, and is lost on every restart of an ephemeral filesystem.
#
# The downloaded tarball is deleted afterwards: keeping it doubles the cost of
# this layer for a file nothing reads again.
RUN if [ "$INSTALL_STEALTH" = "true" ]; then camoufox fetch || true; fi && \
    python -c "from chromadb.utils import embedding_functions as ef; ef.DefaultEmbeddingFunction()(['warm up the onnx model'])" && \
    find /home/app/.cache/chroma -name '*.tar.gz' -delete

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

# One worker by design, not by omission. Three pieces of state in this app
# are per-process and silently wrong when duplicated:
#   * the rate limiter's counters (api/rate_limit.py) - N workers means N x
#     the advertised budget;
#   * the async evaluation task map (api/routers/match.py) - a task started on
#     one worker is invisible to an SSE stream that lands on another;
#   * the APScheduler daily scan (api/main.py) - N workers means N racing runs
#     of the same delete-then-insert.
# The embedding model is also loaded per process, so each extra worker costs
# hundreds of MB - more than a free tier has. Scale out only after moving
# those three to Redis, and set ENABLE_SCHEDULER=false on all but one replica.
#
# $PORT is respected because most PaaS hosts assign it at runtime.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]

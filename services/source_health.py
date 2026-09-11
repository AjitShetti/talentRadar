"""
services/source_health.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Track whether each live job source is actually working, and stop calling the
ones that are not.

The failure this exists to catch is the quiet one. A board changes its markup,
the parser matches nothing, and the fan-out reports ``status="success"`` with
zero jobs forever. Contract tests cannot see it - they replay a fixture
recorded before the change. Only production traffic can.

So every live attempt records its outcome here, and three consecutive
zero-yield runs move a source to ``degraded``; exceptions or timeouts move it
to ``failing``. A failing source is **circuit-broken out of the fan-out** for
a cooldown, which matters for more than tidiness on this deployment:

* a source that is timing out is holding a slot and the user's latency budget
* a source that is erroring is often one that has started blocking us, and
  hammering it is how a shared deployment IP earns a longer ban

State lives in the shared cache backend and is therefore disposable. Losing it
means every source starts from ``unknown`` and gets tried again, which is the
correct behaviour after a restart anyway.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from services.cache_backend import CacheBackend

logger = logging.getLogger(__name__)

HealthStatus = Literal["healthy", "degraded", "failing", "unknown"]

_KEY_PREFIX = "tr:health:"
_TTL_SECONDS = 24 * 3600

# Consecutive zero-yield runs before a source is called degraded. One empty
# result is ordinary - a niche query genuinely has no matches on that board.
# Three in a row is a pattern.
ZERO_YIELD_THRESHOLD = 3

# Consecutive hard failures before the breaker opens.
FAILURE_THRESHOLD = 3

# How long a tripped breaker stays open. Long enough to stop hammering a
# blocking site, short enough that a transient outage self-heals within one
# user's session rather than needing a deploy.
COOLDOWN_SECONDS = 15 * 60


@dataclass
class SourceHealth:
    """Rolling health counters for one source."""

    name: str
    attempts: int = 0
    successes: int = 0
    consecutive_zero_yields: int = 0
    consecutive_failures: int = 0
    total_jobs: int = 0
    last_success_at: float | None = None
    last_error: str | None = None
    breaker_open_until: float | None = None
    recent_latencies_ms: list[float] = field(default_factory=list)

    @property
    def status(self) -> HealthStatus:
        if self.attempts == 0:
            return "unknown"
        if self.consecutive_failures >= FAILURE_THRESHOLD:
            return "failing"
        if self.consecutive_zero_yields >= ZERO_YIELD_THRESHOLD:
            return "degraded"
        return "healthy"

    @property
    def p50_latency_ms(self) -> float:
        if not self.recent_latencies_ms:
            return 0.0
        ordered = sorted(self.recent_latencies_ms)
        return round(ordered[len(ordered) // 2], 1)

    def is_circuit_open(self, now: float | None = None) -> bool:
        """True while this source is being skipped."""
        if self.breaker_open_until is None:
            return False
        return (now or time.time()) < self.breaker_open_until

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status
        data["p50_latency_ms"] = self.p50_latency_ms
        data["circuit_open"] = self.is_circuit_open()
        return data


class SourceHealthService:
    """Read and write per-source health."""

    @staticmethod
    def _key(source_name: str) -> str:
        return f"{_KEY_PREFIX}{source_name}"

    @classmethod
    async def get(cls, source_name: str) -> SourceHealth:
        raw = await CacheBackend.get_json(cls._key(source_name))
        if not isinstance(raw, dict):
            return SourceHealth(name=source_name)
        known = {f for f in SourceHealth.__dataclass_fields__}
        return SourceHealth(**{k: v for k, v in raw.items() if k in known})

    @classmethod
    async def _save(cls, health: SourceHealth) -> None:
        # Only the counters are persisted; status and latency percentile are
        # derived on read, so a change to how they are computed applies to
        # already-stored records.
        payload = {k: v for k, v in asdict(health).items()}
        await CacheBackend.set_json(cls._key(health.name), payload, _TTL_SECONDS)

    @classmethod
    async def record_attempt(
        cls,
        source_name: str,
        *,
        job_count: int,
        latency_ms: float,
        status: str,
    ) -> SourceHealth:
        """
        Record one fan-out result.

        ``status`` is the engine's own string: ``"success"``, ``"timeout"``,
        or ``"error: ..."``. Note that ``success`` with ``job_count == 0`` is
        precisely the case this service treats as suspicious.
        """
        health = await cls.get(source_name)
        health.attempts += 1
        health.total_jobs += job_count

        # Keep a short window; this is a health signal, not a metrics store,
        # and the value is capped at 25 MB of shared cache.
        health.recent_latencies_ms = (health.recent_latencies_ms + [latency_ms])[-20:]

        failed = status != "success"
        if failed:
            health.consecutive_failures += 1
            health.last_error = status[:200]
        else:
            health.consecutive_failures = 0
            health.successes += 1
            health.last_success_at = time.time()

        if job_count == 0:
            health.consecutive_zero_yields += 1
        else:
            health.consecutive_zero_yields = 0

        if health.consecutive_failures >= FAILURE_THRESHOLD:
            health.breaker_open_until = time.time() + COOLDOWN_SECONDS
            logger.warning(
                "Source %s circuit-broken for %ss after %d consecutive failures (last: %s)",
                source_name,
                COOLDOWN_SECONDS,
                health.consecutive_failures,
                health.last_error,
            )
        elif not failed:
            health.breaker_open_until = None

        await cls._save(health)
        return health

    @classmethod
    async def is_available(cls, source_name: str) -> bool:
        """False while the source's breaker is open."""
        health = await cls.get(source_name)
        return not health.is_circuit_open()

    @classmethod
    async def filter_available(cls, source_names: list[str]) -> list[str]:
        """Drop the sources whose breakers are open."""
        available = []
        for name in source_names:
            if await cls.is_available(name):
                available.append(name)
            else:
                logger.info("Skipping %s: circuit breaker open", name)
        return available

    @classmethod
    async def report(cls, source_names: list[str]) -> list[dict[str, Any]]:
        """Health of each named source, for the admin endpoint."""
        return [(await cls.get(name)).to_dict() for name in source_names]

    @classmethod
    async def reset(cls, source_name: str) -> None:
        """Clear a source's counters, closing its breaker. Admin recovery action."""
        await CacheBackend.delete(cls._key(source_name))

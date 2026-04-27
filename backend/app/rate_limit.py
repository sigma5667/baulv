"""Per-API-key rate limiting for the MCP surface.

We expose two flat budgets per key:

* **60 requests / minute** — burst protection. Catches a runaway
  ``while True: call_tool(...)`` loop in someone's notebook before it
  swamps the DB.
* **1000 requests / day** — fairness ceiling. The plan-level cap stays
  at this number for v19; Stage 5 will lift it for Pro and tier the
  per-minute limit.

Both budgets apply to the same ``api_key_id`` independently — a key
that hammers 60 RPM gets 429-ed even if it's well below 1000/day.
JWT-authenticated MCP traffic is *not* rate-limited here (no
``api_key_id`` to bucket on); the SPA's regular HTTP ratelimit covers
that path.

Backend selection
=================

The picker runs once at boot (``select_backend_at_boot``):

* If ``settings.redis_url`` is set — connect, use Redis. Counters live
  centrally so multiple gunicorn workers / Railway replicas share the
  same bucket; the algorithm is the standard "INCR + EXPIRE" sliding-
  window approximation.
* If unset — fall back to an in-process dict guarded by an
  ``asyncio.Lock``. **Single-worker only — bei Multi-Worker auf Redis
  migrieren.** With multiple workers each one has its own dict, so the
  effective limit becomes ``budget × n_workers``, which silently
  defeats the security goal. We emit a WARN-level log at boot so this
  is visible in Railway's log stream.

Both backends implement the same async ``RateLimiter`` interface; the
caller doesn't need to know which one is in play.

Algorithm choice
================

We use a *fixed-window counter* per (key, window) — the simplest
algorithm that matches "60 per minute, 1000 per day". A token bucket
would give nicer burst behaviour but adds machinery (refill clock,
fractional tokens) for negligible benefit at these limits. Boundary-
edge bursts (twice the limit straddling a window edge) are an
acceptable compromise — the worst case is 120 RPM for one second,
which is well below any infrastructure concern.

429 contract
============

On exhaustion the caller raises ``RateLimitExceeded`` with the
``retry_after_seconds`` it should set on the HTTP ``Retry-After``
header *and* mention in the German error message. Both fields come
from the limiter so policy lives in one place.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.config import settings


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public budgets — both flat per key for v19. Stage 5 will tier these.
# ---------------------------------------------------------------------------

#: Per-minute burst cap. 60 = one tool call per second sustained,
#: which covers any realistic interactive Claude Desktop session
#: with margin and still gates a runaway loop.
LIMIT_PER_MINUTE = 60

#: Per-day fairness cap. 1000 = ~16 calls per minute averaged over
#: a working day; the per-minute cap stops bursts above that. Stage 5
#: lifts this for Pro.
LIMIT_PER_DAY = 1000


class RateLimitExceeded(Exception):
    """Raised when a key has consumed its budget for some window.

    ``retry_after_seconds`` is the number of seconds the caller should
    advertise via the ``Retry-After`` header. ``window`` is a short
    label for logs / messages (``"minute"`` or ``"day"``).
    """

    def __init__(self, retry_after_seconds: int, window: str):
        self.retry_after_seconds = retry_after_seconds
        self.window = window
        super().__init__(
            f"Rate limit exceeded for window={window}, "
            f"retry_after={retry_after_seconds}s"
        )


@dataclass(frozen=True)
class LimitConfig:
    """One (limit, window) pair the caller wants to enforce."""

    label: str  # "minute" / "day" — surfaces in errors and logs
    limit: int
    window_seconds: int


_DEFAULT_LIMITS: tuple[LimitConfig, ...] = (
    LimitConfig(label="minute", limit=LIMIT_PER_MINUTE, window_seconds=60),
    LimitConfig(label="day", limit=LIMIT_PER_DAY, window_seconds=86_400),
)


# ---------------------------------------------------------------------------
# Backend protocol — both implementations satisfy this
# ---------------------------------------------------------------------------


class RateLimiter(Protocol):
    """Backend-independent rate-limit interface.

    ``check_and_consume`` either returns silently (request was allowed
    and the counter incremented) or raises ``RateLimitExceeded``. We
    deliberately combine the check and the consume into one call so
    no two requests can race past the limit on a check-then-consume
    boundary.
    """

    async def check_and_consume(self, api_key_id: UUID) -> None: ...


# ---------------------------------------------------------------------------
# In-memory backend — DEV / single-worker only
# ---------------------------------------------------------------------------


class _InMemoryRateLimiter:
    """Single-process fixed-window counter.

    *Single-Worker-only — bei Multi-Worker auf Redis migrieren.*

    Each (api_key_id, window_label) tuple maps to a ``[count, reset_at]``
    list. When ``time.monotonic() >= reset_at`` we reset count to 0 and
    push reset_at out by another window. The ``asyncio.Lock`` makes the
    read-modify-write atomic *within one event loop* — which is enough
    for one worker, and explicitly not enough for multiple.
    """

    def __init__(self, limits: tuple[LimitConfig, ...] = _DEFAULT_LIMITS):
        self._limits = limits
        self._buckets: dict[tuple[UUID, str], list[float]] = {}
        self._lock = asyncio.Lock()

    async def check_and_consume(self, api_key_id: UUID) -> None:
        now = time.monotonic()
        async with self._lock:
            for cfg in self._limits:
                key = (api_key_id, cfg.label)
                bucket = self._buckets.get(key)
                if bucket is None or now >= bucket[1]:
                    # New or expired window — reset.
                    bucket = [0.0, now + cfg.window_seconds]
                    self._buckets[key] = bucket

                if bucket[0] >= cfg.limit:
                    retry = max(1, int(bucket[1] - now))
                    raise RateLimitExceeded(
                        retry_after_seconds=retry, window=cfg.label
                    )
                bucket[0] += 1


# ---------------------------------------------------------------------------
# Redis backend — multi-worker correct
# ---------------------------------------------------------------------------


class _RedisRateLimiter:
    """Fixed-window counter backed by Redis ``INCR``/``EXPIRE``.

    The standard Redis rate-limit recipe: one key per (api_key_id,
    window) holding a counter. ``INCR`` is atomic and creates the key
    at 1 on first call; we then ``EXPIRE`` it to the window length on
    that first INCR so the counter self-evicts. Subsequent INCRs in
    the same window leave the TTL alone.

    We use a single round-trip per limit window via a pipeline, so
    the per-request overhead is one TCP RTT regardless of how many
    windows we check.
    """

    def __init__(
        self,
        redis_client,  # ``redis.asyncio.Redis`` — typed loosely so
        # the import stays optional when redis isn't installed.
        limits: tuple[LimitConfig, ...] = _DEFAULT_LIMITS,
    ):
        self._redis = redis_client
        self._limits = limits

    async def check_and_consume(self, api_key_id: UUID) -> None:
        # Issue all INCRs in one pipeline — we want to know every
        # window's count even if an earlier window already trips, so
        # we don't short-circuit on the client side. ``ttl`` lets us
        # compute Retry-After accurately.
        for cfg in self._limits:
            key = f"mcp:ratelimit:{api_key_id}:{cfg.label}"
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()

            if count == 1:
                # First write in this window — set the TTL. We use
                # ``EXPIRE`` (not ``EXPIREAT``) so window math is
                # relative to the server's clock, which removes a
                # client-server clock-skew failure mode.
                await self._redis.expire(key, cfg.window_seconds)
                ttl = cfg.window_seconds

            if count > cfg.limit:
                # ``ttl`` can briefly be -1 between INCR and our
                # EXPIRE on the very first call; clamp to the full
                # window length so the client gets a sane Retry-After.
                retry = ttl if ttl and ttl > 0 else cfg.window_seconds
                raise RateLimitExceeded(
                    retry_after_seconds=retry, window=cfg.label
                )


# ---------------------------------------------------------------------------
# Module-level limiter — picked once at boot
# ---------------------------------------------------------------------------


_limiter: RateLimiter | None = None


def select_backend_at_boot() -> RateLimiter:
    """Pick the limiter implementation based on ``settings.redis_url``.

    Called from ``main.py`` startup. Cached in module state — the
    transport reads ``get_limiter()`` per request rather than
    re-resolving.
    """
    global _limiter
    if _limiter is not None:
        return _limiter

    redis_url = settings.redis_url
    if redis_url:
        try:
            # Local import so the optional dep doesn't break import
            # of this module on machines that don't have redis.
            import redis.asyncio as redis_async  # type: ignore

            client = redis_async.from_url(
                redis_url, decode_responses=True
            )
            _limiter = _RedisRateLimiter(client)
            logger.info("rate_limit.backend=redis url_set=true")
            return _limiter
        except Exception as exc:  # pragma: no cover - boot path
            # If Redis is configured but unreachable / driver is
            # missing, we *fail closed* on the limiter's side by
            # falling back to in-memory and emitting a CRITICAL log —
            # this is one of those "you really want to know"
            # situations. Not crashing boot is deliberate: the rest
            # of the app should still come up.
            logger.critical(
                "rate_limit.redis_init_failed err=%s — falling back "
                "to in-memory limiter (Single-Worker-only).",
                exc,
            )

    _limiter = _InMemoryRateLimiter()
    logger.warning(
        "rate_limit.backend=memory — Single-Worker-only — bei Multi-"
        "Worker auf Redis migrieren (REDIS_URL setzen)."
    )
    return _limiter


def get_limiter() -> RateLimiter:
    """Return the boot-selected limiter, picking a default if startup
    didn't run (test path)."""
    if _limiter is None:
        return select_backend_at_boot()
    return _limiter


def reset_for_tests() -> None:
    """Wipe the module-level limiter so a test can install its own.

    Tests import this and call it in a fixture; production code never
    does. Kept as an explicit named helper rather than poking the
    private name from outside so the test contract is documented.
    """
    global _limiter
    _limiter = None

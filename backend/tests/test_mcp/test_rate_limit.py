"""Unit tests for the in-memory rate-limit backend.

Redis is a separate code path and a moving piece in CI we'd rather not
take a dependency on, so these tests target ``_InMemoryRateLimiter``
directly. The interface contract (``check_and_consume`` raises
``RateLimitExceeded``) is identical between the two backends, so
covering the in-memory variant gives us confidence in both.

The test windows we use here are intentionally smaller than the
production defaults — they keep the suite fast while still exercising
the same logic (allow up to N, reject N+1, isolate by key, isolate
by window).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.rate_limit import (
    LimitConfig,
    RateLimitExceeded,
    _InMemoryRateLimiter,
)


# Intentionally tiny limits — fast tests, same logic.
SHORT_LIMITS = (
    LimitConfig(label="burst", limit=3, window_seconds=60),
    LimitConfig(label="day", limit=10, window_seconds=86_400),
)


@pytest.mark.asyncio
async def test_in_memory_allows_within_burst():
    """Three consecutive calls under the burst cap should all pass."""
    limiter = _InMemoryRateLimiter(limits=SHORT_LIMITS)
    key_id = uuid.uuid4()

    # Each call within the limit returns silently — no exception.
    for _ in range(3):
        await limiter.check_and_consume(key_id)


@pytest.mark.asyncio
async def test_in_memory_rejects_burst_overflow():
    """The 4th call must raise ``RateLimitExceeded`` and tag the
    smaller window (``burst``) — the per-day limit isn't tripped yet."""
    limiter = _InMemoryRateLimiter(limits=SHORT_LIMITS)
    key_id = uuid.uuid4()

    for _ in range(3):
        await limiter.check_and_consume(key_id)

    with pytest.raises(RateLimitExceeded) as excinfo:
        await limiter.check_and_consume(key_id)
    assert excinfo.value.window == "burst"
    # ``retry_after_seconds`` should be a sensible positive integer
    # close to the window length, not zero or negative.
    assert 1 <= excinfo.value.retry_after_seconds <= 60


@pytest.mark.asyncio
async def test_in_memory_isolates_per_key():
    """Two different keys consume from independent buckets — exhausting
    one must not affect the other. The whole point of per-key
    bucketing."""
    limiter = _InMemoryRateLimiter(limits=SHORT_LIMITS)
    a = uuid.uuid4()
    b = uuid.uuid4()

    for _ in range(3):
        await limiter.check_and_consume(a)
    # ``a`` is now exhausted; ``b`` should still have its full budget.
    for _ in range(3):
        await limiter.check_and_consume(b)

    with pytest.raises(RateLimitExceeded):
        await limiter.check_and_consume(a)


@pytest.mark.asyncio
async def test_in_memory_concurrent_consumes_are_atomic():
    """Five tasks racing for a 3-budget bucket: exactly 3 must succeed
    and 2 must raise. The lock around read-modify-write is what keeps
    us from overshooting the budget under concurrent dispatch."""
    limiter = _InMemoryRateLimiter(
        limits=(LimitConfig(label="burst", limit=3, window_seconds=60),)
    )
    key_id = uuid.uuid4()

    results: list[Exception | None] = []

    async def attempt():
        try:
            await limiter.check_and_consume(key_id)
            results.append(None)
        except RateLimitExceeded as exc:
            results.append(exc)

    await asyncio.gather(*[attempt() for _ in range(5)])

    successes = [r for r in results if r is None]
    failures = [r for r in results if isinstance(r, RateLimitExceeded)]
    assert len(successes) == 3
    assert len(failures) == 2


def test_select_backend_falls_back_to_memory_without_redis(monkeypatch):
    """When ``settings.redis_url`` is unset, the picker must return the
    in-memory limiter and emit no Redis-related error. The WARN log is
    a side-effect we don't assert here (LogCaptureHandler would tie us
    to the formatter)."""
    from app import rate_limit
    from app.config import settings

    monkeypatch.setattr(settings, "redis_url", None, raising=False)
    rate_limit.reset_for_tests()

    backend = rate_limit.select_backend_at_boot()
    assert isinstance(backend, rate_limit._InMemoryRateLimiter)

    # Idempotent — second call returns the same instance.
    assert rate_limit.select_backend_at_boot() is backend

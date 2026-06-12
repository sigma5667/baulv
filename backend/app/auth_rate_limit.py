"""In-process rate limiting for the unauthenticated auth surface.

Security-Härtung (Audit 2026-06). Vorher hatten ``/login``,
``/register`` und ``/password-reset/confirm`` KEINE Drosselung — der
Docstring von ``app.rate_limit`` behauptete fälschlich, ein "SPA's
regular HTTP ratelimit" decke das ab; ein solches Limit existierte
nicht. Unbegrenztes Passwort-Raten / Credential-Stuffing war möglich.

Zwei unabhängige Buckets je Anfrage:

* **pro Client-IP** — stoppt einen einzelnen Host, der die Oberfläche
  hämmert;
* **pro Konto (E-Mail)** — stoppt Credential-Stuffing, das die IP
  rotiert (inkl. ``X-Forwarded-For``-Spoofing) und dabei EIN Konto
  angreift; das kann der IP-Bucket allein nicht abfangen.

Sliding Window, feste Limits. Bewusst **in-process** (wie der
In-Memory-Fallback in ``app.rate_limit``): die Zähler sind pro Worker,
unter mehreren Gunicorn-Workern ist das effektive Limit also
``limit × n_worker``. Das ist ein dokumentierter Kompromiss für eine
Brute-Force-*Bremse*; eine harte Garantie braucht den geteilten
Redis-Backend — das ist der Follow-up (siehe ``app.rate_limit``).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status


# (action, scope) -> (max_attempts, window_seconds)
#
# IP-Limits sind großzügig (geteilte Büro-NAT-IPs einer Baufirma sollen
# nicht versehentlich ausgesperrt werden), Konto-Limits strikt, weil
# sie das eigentliche Passwort-Raten gegen ein Ziel begrenzen.
_LIMITS: dict[tuple[str, str], tuple[int, int]] = {
    ("login", "ip"): (40, 300),                    # 40 / 5 min pro IP
    ("login", "acct"): (8, 900),                   # 8 / 15 min pro E-Mail
    ("register", "ip"): (15, 3600),                # 15 / h pro IP
    ("password-reset-confirm", "ip"): (20, 900),   # 20 / 15 min pro IP
}

_buckets: dict[tuple[str, str, str], deque[float]] = defaultdict(deque)
_lock = Lock()


def _client_ip(request: Request) -> str:
    """Best-effort Client-IP.

    Hinweis: ``X-Forwarded-For`` ist client-setzbar und damit
    spoofbar — deshalb gibt es zusätzlich den nicht-spoofbaren
    Konto-Bucket. Hinter Railway steht hier die reale Client-IP im
    XFF-Header.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _check(action: str, scope: str, identity: str) -> None:
    limit, window = _LIMITS[(action, scope)]
    now = time.time()
    cutoff = now - window
    key = (action, scope, identity)
    with _lock:
        dq = _buckets[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry = max(1, int(dq[0] + window - now))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Zu viele Versuche. Bitte versuchen Sie es in einigen "
                    "Minuten erneut."
                ),
                headers={"Retry-After": str(retry)},
            )
        dq.append(now)


def enforce(action: str, request: Request, *, account: str | None = None) -> None:
    """Record one attempt and 429 if the IP — or, when given, the
    account — bucket for ``action`` is over budget.

    ``action`` muss in ``_LIMITS`` definiert sein. Reihenfolge: zuerst
    IP, dann Konto; der erste überschrittene Bucket wirft.
    """
    _check(action, "ip", _client_ip(request))
    if account and (action, "acct") in _LIMITS:
        _check(action, "acct", account.lower().strip())


def reset_for_tests() -> None:
    """Wipe all buckets — for test fixtures. Production never calls this."""
    with _lock:
        _buckets.clear()

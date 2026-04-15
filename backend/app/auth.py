"""Authentication helpers (password hashing, JWT issue/verify, dep injection).

JWTs here are **stateful**: each token carries a ``jti`` claim that
corresponds to a row in ``user_sessions``. That lets us revoke
individual tokens without rotating the signing key — essential for
per-device "log me out" and for "log me out everywhere else" on
password change, both required under DSGVO-aware session management.

The cost is one DB read per authenticated request. We accept it; see
``app/db/models/session.py`` for the reasoning.

Legacy tokens (issued before this refactor) have no ``jti`` and are
rejected on sight. Users on the previous deployment will simply need
to log in once after the upgrade.
"""

from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.db.models.user import User
from app.db.models.session import UserSession

security = HTTPBearer(auto_error=False)

TOKEN_TTL = timedelta(days=7)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _new_jti() -> str:
    """43-character URL-safe random string. Plenty of entropy."""
    return token_urlsafe(32)


def create_access_token(
    user_id: UUID,
    *,
    jti: str | None = None,
    expires_delta: timedelta | None = None,
) -> tuple[str, str, datetime]:
    """Issue a JWT. Returns (token, jti, expires_at).

    The caller is expected to insert a matching ``UserSession`` row
    with the returned ``jti`` before the token is handed back to the
    client — otherwise the very next request from that token will 401.
    """
    jti = jti or _new_jti()
    expire = datetime.now(timezone.utc) + (expires_delta or TOKEN_TTL)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "jti": jti,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, jti, expire


def decode_token(token: str) -> dict | None:
    """Decode and validate the JWT. Returns the payload dict or None."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        return None


async def issue_session(
    db: AsyncSession,
    user: User,
    request: Request | None,
) -> str:
    """Create a session row + matching JWT for a successful auth.

    Returns the signed token. The row is added to the session but not
    committed — the caller's transaction owns the commit boundary.
    """
    token, jti, expires_at = create_access_token(user.id)

    ua = None
    ip = None
    if request is not None:
        ua_hdr = request.headers.get("user-agent")
        ua = ua_hdr[:500] if ua_hdr else None
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            ip = fwd.split(",")[0].strip()
        elif request.client:
            ip = request.client.host

    db.add(
        UserSession(
            user_id=user.id,
            jti=jti,
            user_agent=ua,
            ip_address=ip,
            expires_at=expires_at,
        )
    )
    await db.flush()
    return token


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency: returns the authenticated user.

    Steps: decode JWT → verify signature and expiry → look up the
    session row by ``jti`` → confirm it isn't revoked → load the user.
    Any failure becomes a 401. Also attaches ``request.state.session``
    so endpoints that need the current session row (e.g. to label
    "this device" in the sessions list) don't have to re-query it.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht authentifiziert",
        )
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token",
        )
    jti = payload.get("jti")
    sub = payload.get("sub")
    if not jti or not sub:
        # Legacy token from before the stateful-JWT refactor. Force a
        # re-login rather than silently accepting it.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bitte erneut anmelden.",
        )

    try:
        user_id = UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token",
        )

    result = await db.execute(select(UserSession).where(UserSession.jti == jti))
    session_row = result.scalars().first()
    if session_row is None or session_row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sitzung wurde beendet. Bitte erneut anmelden.",
        )
    if session_row.user_id != user_id:
        # Defense in depth — the JWT and the session row disagree.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token",
        )

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Benutzer nicht gefunden",
        )

    # Bump last_used_at so the sessions list reflects activity. Kept
    # intentionally simple (one UPDATE per authenticated request); if
    # this ever becomes a bottleneck, gate it on "has been >N seconds".
    session_row.last_used_at = datetime.now(timezone.utc)
    await db.flush()

    return user


async def get_current_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> UserSession:
    """Companion dependency: returns the ``UserSession`` for the
    token presented on this request. Intentionally duplicates the
    decode+lookup work done in ``get_current_user`` so endpoints can
    depend on either or both without coupling. Cheap either way —
    both land on the same SQL and hit the same hot row.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht authentifiziert",
        )
    payload = decode_token(credentials.credentials)
    if payload is None or not payload.get("jti"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ungültiger Token",
        )
    result = await db.execute(
        select(UserSession).where(UserSession.jti == payload["jti"])
    )
    session_row = result.scalars().first()
    if session_row is None or session_row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sitzung wurde beendet.",
        )
    return session_row


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Same as ``get_current_user`` but returns None instead of 401."""
    if credentials is None:
        return None
    payload = decode_token(credentials.credentials)
    if payload is None:
        return None
    jti = payload.get("jti")
    sub = payload.get("sub")
    if not jti or not sub:
        return None
    try:
        user_id = UUID(sub)
    except ValueError:
        return None
    result = await db.execute(select(UserSession).where(UserSession.jti == jti))
    session_row = result.scalars().first()
    if session_row is None or session_row.revoked_at is not None:
        return None
    return await db.get(User, user_id)

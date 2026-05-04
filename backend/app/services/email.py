"""Transactional email via Resend.

Lazily imports the Resend SDK so dev runs without a Resend account
don't fail at module-load time. The module surface is one helper
per email type:

* ``send_password_reset_email`` — DS-3 (v23.4) reset link.

Future flows (privacy-version refresh notifications, account-
deletion confirmations, etc.) plug in here as additional helpers
that share ``_send`` and the Resend client.

Operational notes
-----------------

**Tokens never appear in logs.** The reset token is the bearer
credential — logging it would defeat the entire single-use design.
The structured log line records ``recipient`` and a
``status=sent|failed`` field, nothing else. Production grep targets
the message ``email.password_reset.sent`` (or ``.failed``) only.

**Failures are swallowed.** A Resend outage (or a misconfigured
API key) must not break the surrounding HTTP request — the user
still sees the regular "if such an account exists, we sent an
email" 200 OK, and the operator notices via the WARN log line plus
the missing email in the Resend dashboard. Returning success even
on failure is intentional: it keeps the account-existence response
identical to the success path so we don't leak via timing or
error-message mismatch.

**No API key = log-only.** When ``settings.resend_api_key`` is
empty, ``_send`` returns ``False`` after a single WARN line. The
caller still treats the request as succeeded for DSGVO-leak
reasons; in dev the operator sees the warning and knows nothing
went out.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


# Module-level cache for the Resend SDK module. Imported on first
# successful send call so the module imports cleanly even when the
# Resend package isn't installed (it always is in our prod image —
# this is belt-and-suspenders for tests + alternate runtime envs).
_resend_module: Any = None


def _get_resend() -> Any | None:
    """Return the imported Resend module, or ``None`` if unavailable.

    Cached on the module so we don't re-import on every send. Errors
    are logged once at WARN — repeated sends won't spam the log.
    """
    global _resend_module
    if _resend_module is not None:
        return _resend_module
    try:
        import resend  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "email.resend_unavailable: 'resend' package not installed; "
            "transactional email is disabled"
        )
        return None
    _resend_module = resend
    return resend


def _send(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> bool:
    """Low-level send. Returns ``True`` on success, ``False`` on any
    failure (missing API key, ImportError, Resend HTTP error).

    The caller decides whether failure is user-visible. For the
    password-reset flow it never is — we always return 200 OK to the
    user to avoid leaking account existence; the email might just
    not arrive.
    """
    if not settings.resend_api_key:
        # Dev / unconfigured deploy. Log once per call so the operator
        # notices in the bootstrap output, but don't crash.
        logger.warning(
            "email.no_api_key: RESEND_API_KEY unset; would have sent "
            "subject=%r to=%r",
            subject,
            to_email,
        )
        return False

    resend = _get_resend()
    if resend is None:
        return False

    try:
        # Configure on every call — cheap, and means a runtime change
        # to ``settings.resend_api_key`` (in tests for instance) takes
        # effect on the next send without process restart.
        resend.api_key = settings.resend_api_key
        from_addr = (
            f"{settings.resend_from_name} <{settings.resend_from_email}>"
        )
        params: dict[str, Any] = {
            "from": from_addr,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
            "text": text_body,
        }
        # The ``Emails.send`` surface is the documented entry point;
        # response is a dict carrying ``id``. We don't store the ID —
        # tracing is via Resend dashboard + our recipient log line.
        resend.Emails.send(params)
        logger.info(
            "email.sent recipient=%s subject=%r", to_email, subject
        )
        return True
    except Exception as exc:  # noqa: BLE001 — Resend SDK raises various
        logger.warning(
            "email.send_failed recipient=%s subject=%r error=%s",
            to_email,
            subject,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Password-reset email (DS-3 / v23.4)
# ---------------------------------------------------------------------------


def send_password_reset_email(
    *,
    to_email: str,
    reset_token: str,
    user_name: str | None = None,
) -> bool:
    """Send the German password-reset email.

    Parameters
    ----------
    to_email
        The recipient address. Caller has already verified an account
        exists for this email — the *endpoint* always returns 200 OK
        regardless, but ``send_password_reset_email`` is only invoked
        from the success branch.
    reset_token
        The plaintext URL-safe token. Embedded into the link as
        ``?token=...``. **Never logged.** The caller has already
        persisted the SHA-256 hash; the plaintext lives only in this
        function's call frame and the outgoing email body.
    user_name
        Optional friendly name for the salutation. ``None`` falls
        back to a neutral "Hallo,".

    Returns ``True`` on successful Resend send, ``False`` otherwise.
    The caller should *not* expose the boolean to the user — the
    HTTP response is the same either way (no info leak).
    """
    # Never include the token in a log statement. Even at DEBUG.
    # Log the recipient + the fact we're attempting a send only.
    logger.info("email.password_reset.attempt recipient=%s", to_email)

    salutation = f"Hallo {user_name}," if user_name else "Hallo,"
    reset_link = (
        f"{settings.app_base_url.rstrip('/')}"
        f"/passwort-zuruecksetzen?token={reset_token}"
    )

    subject = "Passwort zurücksetzen - BauLV"

    # Plain-text fallback. Mail clients without HTML support (and most
    # spam filters) read this; keep wording identical to the HTML
    # version so anti-phishing heuristics don't flag a mismatch.
    text_body = f"""{salutation}

du hast eine Zurücksetzung deines BauLV-Passworts angefordert.

Klicke auf den folgenden Link, um ein neues Passwort zu vergeben:

{reset_link}

Der Link ist 1 Stunde gültig und kann nur einmal verwendet werden.

Falls du diese E-Mail nicht angefordert hast, kannst du sie ignorieren -
dein Passwort bleibt unverändert.

Viele Grüße
Dein BauLV-Team

--
BauLV - KI-gestützte Bau-Ausschreibungssoftware
https://baulv.at
"""

    # HTML version. Inline styles only — most mail clients strip
    # <style> blocks. Kept deliberately minimal so it renders the
    # same in Outlook, Gmail, Apple Mail.
    html_body = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Passwort zurücksetzen - BauLV</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; line-height: 1.6; color: #1f2937; max-width: 560px; margin: 0 auto; padding: 24px;">
  <h1 style="font-size: 20px; font-weight: 600; color: #111827; margin: 0 0 16px;">
    Passwort zurücksetzen
  </h1>
  <p style="margin: 0 0 16px;">{salutation}</p>
  <p style="margin: 0 0 16px;">
    du hast eine Zurücksetzung deines BauLV-Passworts angefordert.
    Klicke auf den Button, um ein neues Passwort zu vergeben:
  </p>
  <p style="margin: 24px 0;">
    <a href="{reset_link}"
       style="display: inline-block; padding: 12px 24px; background: #2563eb;
              color: #ffffff; text-decoration: none; border-radius: 6px;
              font-weight: 500;">
      Neues Passwort vergeben
    </a>
  </p>
  <p style="margin: 0 0 16px; font-size: 14px; color: #6b7280;">
    Oder kopiere diese Adresse in deinen Browser:<br>
    <span style="word-break: break-all;">{reset_link}</span>
  </p>
  <p style="margin: 0 0 16px; font-size: 14px; color: #6b7280;">
    Der Link ist <strong>1 Stunde</strong> gültig und kann nur einmal
    verwendet werden.
  </p>
  <p style="margin: 24px 0 0; font-size: 14px; color: #6b7280;">
    Falls du diese E-Mail nicht angefordert hast, kannst du sie ignorieren -
    dein Passwort bleibt unverändert.
  </p>
  <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 32px 0 16px;">
  <p style="margin: 0; font-size: 12px; color: #9ca3af;">
    BauLV - KI-gestützte Bau-Ausschreibungssoftware<br>
    <a href="https://baulv.at" style="color: #6b7280; text-decoration: underline;">baulv.at</a>
  </p>
</body>
</html>
"""

    sent = _send(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    # Final status line — distinct from the generic ``email.sent`` so
    # the operator can grep on the password-reset path specifically.
    logger.info(
        "email.password_reset.%s recipient=%s",
        "sent" if sent else "failed",
        to_email,
    )
    return sent

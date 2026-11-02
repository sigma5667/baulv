"""Der Testlauf darf nie gegen Produktion laufen (Audit 2026-11).

Konto ``pw-test-do-not-create@example.com`` entstand am 12.05.2026 durch
einen Smoke-Test gegen die Live-Seite. Diese Tests pinnen die Sperren in
``conftest.py``.
"""

import socket

import pytest

from app.config import settings


def test_settings_point_to_dummy_values():
    assert settings.anthropic_api_key.startswith("sk-ant-test-dummy")
    assert settings.resend_api_key == ""
    assert settings.stripe_secret_key == ""
    assert "127.0.0.1" in settings.database_url
    assert settings.environment == "test"


@pytest.mark.parametrize(
    "host", ["baulv.at", "www.baulv.at", "api.anthropic.com", "8.8.8.8"]
)
def test_outbound_network_is_blocked(host):
    with pytest.raises(OSError, match="nicht ins Netz"):
        socket.getaddrinfo(host, 443)


def test_loopback_still_allowed():
    assert socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo("localhost", 80)

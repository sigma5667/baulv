"""Client-IP für Rate-Limits — Cloudflare-fähig, nicht per Header fälschbar.

Weg einer Anfrage in Produktion::

    Browser → Cloudflare → Railway-Edge → App

* Railway setzt ``X-Forwarded-For`` selbst; der erste Eintrag ist die
  IP, die sich mit Railway verbunden hat (ein vom Client mitgeschickter
  XFF-Header wird überschrieben — am 2026-10-18 live geprüft).
* Kommt die Anfrage über baulv.at, ist diese IP ein Cloudflare-Knoten.
  Die echte Besucher-IP steht dann in ``CF-Connecting-IP``.
* Die App ist aber auch direkt (``*.up.railway.app``) erreichbar. Dort
  kann jeder ``CF-Connecting-IP`` frei erfinden — deshalb wird der
  Header NUR übernommen, wenn die verbindende IP in den offiziellen
  Cloudflare-Netzen liegt.

Liste: https://www.cloudflare.com/ips/ (Stand 2026-10-18). Ändert
Cloudflare die Netze, fällt neuer Traffic auf die Knoten-IP zurück —
das ist strenger, nie lockerer.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

_CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
        "2400:cb00::/32",
        "2606:4700::/32",
        "2803:f800::/32",
        "2405:b500::/32",
        "2405:8100::/32",
        "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
)


def _parse_ip(value: str | None):
    if not value:
        return None
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def is_cloudflare_ip(value: str | None) -> bool:
    ip = _parse_ip(value)
    return ip is not None and any(ip in net for net in _CLOUDFLARE_NETWORKS)


def client_ip(request: Request) -> str:
    """IP des Besuchers für Rate-Limit-Buckets."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        peer = fwd.split(",")[0].strip()
    elif request.client:
        peer = request.client.host
    else:
        return "unknown"

    if is_cloudflare_ip(peer):
        cf_ip = _parse_ip(request.headers.get("cf-connecting-ip"))
        if cf_ip is not None:
            return str(cf_ip)
    return peer

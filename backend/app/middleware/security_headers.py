"""Security-Header für jede HTTP-Response.

Setzt eine Handvoll Standard-Header die Browser nutzen um klassische
Angriffsvektoren abzudichten. Die Header sind statisch (gleich auf
allen Endpoints), darum als einfache Middleware implementiert statt
in jeder einzelnen Route.

Welche Header und warum
=======================

* ``Strict-Transport-Security: max-age=31536000; includeSubDomains``
  Zwingt Browser ein Jahr lang HTTPS zu verwenden, auch wenn der User
  ``http://baulv.at`` tippt. Verhindert Man-in-the-Middle-Downgrade-
  Angriffe auf öffentlichen WLANs. ``includeSubDomains`` greift auch
  für eventuell zukünftige ``api.baulv.at`` o.ä.
  Wirkt nur über HTTPS gesetzt — Browser ignorieren HSTS-Header die
  über HTTP kommen.

* ``X-Content-Type-Options: nosniff``
  Verhindert dass Browser den Content-Type der Response "erraten"
  ("MIME-Sniffing"). Wichtig gegen XSS via hochgeladener Datei die
  als HTML interpretiert wird obwohl ``Content-Type: text/plain``
  gesendet wurde.

* ``X-Frame-Options: DENY``
  Verbietet komplett das Einbetten der Seite in einen ``<iframe>``
  auf einer fremden Domain. Schutz gegen Clickjacking (Bauplan-
  Upload via verstecktem Iframe auf bösartiger Drittseite).

* ``Referrer-Policy: strict-origin-when-cross-origin``
  Beschneidet was BauLV als ``Referer``-Header an externe Links
  weitergibt. Cross-Origin: nur die Origin (https://baulv.at),
  kein Pfad — damit keine internen URLs wie
  ``/app/projects/<uuid>/plans/<uuid>`` zu Drittseiten leaken.

* ``Content-Security-Policy``
  Whitelist welche Quellen Skripte/Stylesheets/Bilder laden dürfen.
  Die Policy hier ist defensiv aber kompatibel mit unserer Setup
  (Vite-Hashed Assets aus ``/static``, Stripe-Checkout, Anthropic-
  API-Calls über das eigene Backend, KEINE direkt-eingebetteten
  Third-Party-Skripte).

  Wir lassen ``'unsafe-inline'`` für Styles drin weil Tailwind CSS-
  in-JS und einige Lucide-Icons inline-styles brauchen — ohne diesen
  Eintrag würde die App weiße Seite zeigen. Für Skripte halten wir
  die Policy streng (``'self'`` only). Falls in Zukunft externe
  Skripte gebraucht werden, muss der Eintrag explizit erweitert
  werden.

Wo nicht greift
===============

Diese Middleware setzt nur Response-Header. Sie ersetzt keine
serverseitige Eingabe-Validierung, keine SQL-Injection-Schutz,
keine Authentifizierung. Sie ist eine zusätzliche Verteidigungs-
Schicht in der Browser-Sandbox, kein Allheilmittel.

Test-Strategie
==============

Eine Smoke-Assertion gegen die Header in ``tests/test_middleware/
test_security_headers.py``: GET auf ``/api/health``, prüfen dass
alle 5 Header gesetzt sind. Werte selbst sind als Konstanten
exportiert damit der Test sie nicht doppeln muss.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Header-Werte als Modul-Konstanten exportiert damit Tests sie
# importieren können statt Strings zu duplizieren.
HSTS_VALUE = "max-age=31536000; includeSubDomains"
CONTENT_TYPE_OPTIONS_VALUE = "nosniff"
FRAME_OPTIONS_VALUE = "DENY"
REFERRER_POLICY_VALUE = "strict-origin-when-cross-origin"

# Content-Security-Policy. Whitelist-Strategie:
#  * default-src 'self'  — Default ist alles vom eigenen Origin.
#  * script-src 'self'   — Nur Skripte aus dem eigenen Bundle.
#  * style-src 'self' 'unsafe-inline' — Tailwind + inline-Styles.
#  * img-src 'self' data: — Eigene Bilder + Data-URIs (Avatare etc.).
#  * font-src 'self' data: — Fonts gleichermassen.
#  * connect-src 'self'  — API-Calls nur ans eigene Backend.
#  * frame-src 'none'    — Kein iframe-Inhalt von extern.
#  * object-src 'none'   — Kein <object>/<embed>.
#  * base-uri 'self'     — Verhindert <base>-Tag-Injection.
#  * form-action 'self'  — Formulare nur ans eigene Backend.
CSP_VALUE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-src 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Fügt jeder Response die fünf Standard-Security-Header hinzu.

    Vorhandene Header werden überschrieben — das ist gewollt damit
    keine Endpoint-Logik versehentlich eine schwächere Policy setzen
    kann. Wenn ein zukünftiger Endpoint einen einzelnen Header
    schwächer braucht (z.B. erlaubtes Iframe-Embedding), muss das
    explizit hier oder in einer endpoint-spezifischen Middleware
    geschehen.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = HSTS_VALUE
        response.headers["X-Content-Type-Options"] = CONTENT_TYPE_OPTIONS_VALUE
        response.headers["X-Frame-Options"] = FRAME_OPTIONS_VALUE
        response.headers["Referrer-Policy"] = REFERRER_POLICY_VALUE
        response.headers["Content-Security-Policy"] = CSP_VALUE
        return response

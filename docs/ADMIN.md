# Admin — Test-Upgrades (Beta)

> **WARNUNG.** Diese Datei beschreibt eine manuelle Umgehung des Stripe-
> Billing-Flows. Sie ist ausschließlich für **Beta-Tests mit internen
> Test-Accounts** gedacht. **Niemals für zahlende oder zahlende-wollende
> Kunden verwenden** — die müssen über den normalen Stripe-Checkout
> gehen, damit `stripe_customer_id` / `stripe_subscription_id` gesetzt
> werden und Webhooks die Subscription-Lifecycle-Events abfangen.

## Wozu das überhaupt?

`subscription_plan` wird im Normalbetrieb durch den Stripe-Webhook
gesetzt (siehe `backend/app/api/subscriptions.py`). Solange die Stripe-
Live-Keys nicht konfiguriert sind, gibt es keinen legitimen Weg, einen
Account auf `pro` zu bekommen — und damit auch keinen Weg, den AI-
Plananalyse-Flow end-to-end zu testen.

Der Bypass passiert bewusst **direkt in der Datenbank**, nicht über die
API: kein neuer HTTP-Endpoint, kein Admin-Token, kein CLI-Befehl. Das
ist die kleinstmögliche Angriffsfläche für ein Feature, das produktiv
nie gebraucht werden sollte.

## Upgrade durchführen

In Railway: **Postgres-Service → Data → Query**. Als einzelne
Transaktion ausführen (Email austauschen):

```sql
BEGIN;

-- 1. Plan auf "pro" setzen. RETURNING bestätigt, dass die richtige
--    Zeile getroffen wurde — bei 0 Zeilen: Tippfehler in der Email,
--    NICHT ohne Prüfung weitermachen.
UPDATE users
SET subscription_plan = 'pro',
    updated_at = NOW()
WHERE email = 'test-chrome@baulv.at'
RETURNING id, email, subscription_plan;

-- 2. Audit-Eintrag (DSGVO Art. 32). event_type 'admin.subscription_override'
--    macht beim späteren Log-Review sofort klar, dass das KEIN regulärer
--    Stripe-Event war.
INSERT INTO audit_log_entries (user_id, event_type, meta, created_at)
SELECT id,
       'admin.subscription_override',
       jsonb_build_object(
         'from', 'basis',
         'to', 'pro',
         'reason', 'manual beta test upgrade (Stripe live keys pending)'
       ),
       NOW()
FROM users
WHERE email = 'test-chrome@baulv.at';

COMMIT;
```

Nach dem COMMIT reicht im Frontend ein **Page-Reload** — die Feature-
Matrix kommt aus `/auth/me/features` und wird bei jedem `refreshUser`
live aus der DB geladen. Kein Re-Login nötig.

## Zurücksetzen (nach dem Test)

```sql
BEGIN;

UPDATE users
SET subscription_plan = 'basis',
    updated_at = NOW()
WHERE email = 'test-chrome@baulv.at'
RETURNING id, email, subscription_plan;

INSERT INTO audit_log_entries (user_id, event_type, meta, created_at)
SELECT id,
       'admin.subscription_override',
       jsonb_build_object(
         'from', 'pro',
         'to', 'basis',
         'reason', 'test complete — reverting to default'
       ),
       NOW()
FROM users
WHERE email = 'test-chrome@baulv.at';

COMMIT;
```

## Was dieser Bypass NICHT tut

- Keine Stripe-Subscription anlegen — `stripe_customer_id` /
  `stripe_subscription_id` bleiben `NULL`. Sobald der Account später
  real über Stripe zahlt, überschreibt der Webhook `subscription_plan`
  — das ist erwartetes Verhalten.
- Keine Rechnung, kein Eintrag im Stripe-Dashboard.
- Keine zeitbegrenzte Trial — der Plan bleibt auf `pro`, bis er manuell
  zurückgesetzt oder von einem Stripe-Event überschrieben wird.

## Nicht verwenden, wenn…

- Der Account real zahlt oder zahlen soll.
- Der Account einem Freund / Bekannten gehört und "eigentlich nur mal
  Pro ausprobieren" will — das ist Revenue Leakage, nicht Testen.
- Stripe-Live-Keys konfiguriert sind und der Checkout-Flow funktioniert
  — dann gibt es keinen legitimen Grund mehr, die DB direkt zu ändern.
  Diese Datei sollte dann entfernt werden.

-- v24.4.1-followup — Einmalige Bereinigung eines Smoke-Test-Accounts.
--
-- Erstellt: 2026-05-12
-- Operator: Tobi (über Railway-Console psql)
--
-- ZIEL
-- ====
-- Den Test-Account ``pw-test-do-not-create@example.com``
-- (ID ``4d19d08a-066f-4cdc-9d03-c4e963077071``) restlos aus der DB
-- entfernen. Der Account ist beim manuellen Smoke-Test der v24.4.1-
-- Security-Härtung versehentlich angelegt worden und enthält keine
-- echten Daten.
--
-- KASKADEN-VERHALTEN (siehe ORM-Modelle in backend/app/db/models/)
-- ================================================================
-- Folgende FKs auf ``users.id`` werden durch den User-DELETE getriggert:
--
--   CASCADE (Zeilen verschwinden mit dem User):
--     * projects.user_id            → Projekte + ihre Buildings/Floors/
--                                     Units/Rooms/Openings via weiterer
--                                     CASCADEs
--     * user_sessions.user_id       → alle Login-Sessions
--     * api_keys.user_id            → PATs
--     * password_reset_tokens.user_id → offene Reset-Tokens
--
--   SET NULL (Zeile bleibt, FK wird NULL, anonyme Spur):
--     * audit_log.user_id           → Audit-Einträge bleiben anonym
--     * consent_snapshots.user_id   → DSGVO-Art.-7-Evidenz bleibt
--     * lv_templates.created_by_user_id → Custom-Templates bleiben
--     * mcp_audit.user_id           → MCP-Call-Log bleibt anonym
--
-- ``usage_analytics`` hat KEINEN FK auf users.id (designed so:
-- ``anonymous_user_id`` ist ein gesalzeter Hash, nicht joinbar) —
-- diese Tabelle ist von einem User-DELETE überhaupt nicht betroffen.
--
-- AUSFÜHRUNG
-- ==========
-- 1. ``railway run psql $DATABASE_URL`` (oder direkt im Railway-DB-
--    Tab im "Query"-Bereich)
-- 2. Den gesamten Block unten kopieren und einfügen.
-- 3. Die SELECT-Ausgaben prüfen. Wenn alles aussieht wie erwartet
--    (1 User-Row, Counts plausibel) → ``COMMIT;`` ans Ende ausführen.
-- 4. Wenn etwas merkwürdig aussieht (z.B. 0 User-Rows, oder
--    falsche Email) → ``ROLLBACK;`` ausführen statt COMMIT, dann
--    Tobi melden.
--
-- Das gesamte Script läuft in EINER Transaktion. Erst der explizite
-- COMMIT macht den DELETE permanent. Ein ROLLBACK undoed alles.

BEGIN;

-- ---------------------------------------------------------------------
-- Schritt 1 — Bestätigung: existiert der Account und sieht er aus
-- wie der Smoke-Test-Account?
-- ---------------------------------------------------------------------
SELECT
    id,
    email,
    full_name,
    created_at,
    subscription_plan
FROM users
WHERE id = '4d19d08a-066f-4cdc-9d03-c4e963077071'
  AND email = 'pw-test-do-not-create@example.com';
-- Erwartete Ausgabe: GENAU EINE Zeile mit der Test-Email.
-- Falls 0 Zeilen: Account ist schon weg → ROLLBACK + Skript schließen.
-- Falls >1 Zeile: unmöglich (UUID ist primärschlüssel) → ROLLBACK + Tobi.
-- Falls andere Email: ID kollidiert mit echtem User → ROLLBACK + Tobi.


-- ---------------------------------------------------------------------
-- Schritt 2 — Was wird durch CASCADE mitgelöscht?
-- Erwartung: alle Counts = 0, weil der Smoke-Test nur den Account
-- angelegt und keine Daten erzeugt hat. Falls Counts > 0, hat der
-- Test-User irgendwas hinterlassen — Tobi sollte das wissen.
-- ---------------------------------------------------------------------
SELECT 'projects' AS table_name, COUNT(*) AS rows_to_be_deleted
FROM projects WHERE user_id = '4d19d08a-066f-4cdc-9d03-c4e963077071'
UNION ALL
SELECT 'user_sessions', COUNT(*)
FROM user_sessions WHERE user_id = '4d19d08a-066f-4cdc-9d03-c4e963077071'
UNION ALL
SELECT 'api_keys', COUNT(*)
FROM api_keys WHERE user_id = '4d19d08a-066f-4cdc-9d03-c4e963077071'
UNION ALL
SELECT 'password_reset_tokens', COUNT(*)
FROM password_reset_tokens WHERE user_id = '4d19d08a-066f-4cdc-9d03-c4e963077071';


-- ---------------------------------------------------------------------
-- Schritt 3 — Was wird durch SET NULL anonymisiert (bleibt erhalten)?
-- Diese Zeilen werden NICHT gelöscht, ihre user_id-Spalte wird auf
-- NULL gesetzt. So bleibt z.B. die Audit-Spur "jemand hat sich am
-- 2026-05-12 14:32 erfolgreich registriert (IP X.X.X.X)" erhalten,
-- aber ist nicht mehr auf den (gelöschten) User joinbar.
-- ---------------------------------------------------------------------
SELECT 'audit_log' AS table_name, COUNT(*) AS rows_to_be_anonymised
FROM audit_log WHERE user_id = '4d19d08a-066f-4cdc-9d03-c4e963077071'
UNION ALL
SELECT 'consent_snapshots', COUNT(*)
FROM consent_snapshots WHERE user_id = '4d19d08a-066f-4cdc-9d03-c4e963077071'
UNION ALL
SELECT 'lv_templates', COUNT(*)
FROM lv_templates WHERE created_by_user_id = '4d19d08a-066f-4cdc-9d03-c4e963077071'
UNION ALL
SELECT 'mcp_audit', COUNT(*)
FROM mcp_audit WHERE user_id = '4d19d08a-066f-4cdc-9d03-c4e963077071';


-- ---------------------------------------------------------------------
-- Schritt 4 — Eigentlicher DELETE.
-- Doppelter Filter (id + email) als Sicherheitsnetz: wenn die ID auf
-- einen anderen User zeigen sollte (sehr unwahrscheinlich), würde der
-- DELETE auf 0 Zeilen treffen und ein nachfolgendes COMMIT wäre ein
-- No-Op statt einen unbeteiligten User zu löschen.
-- ---------------------------------------------------------------------
DELETE FROM users
WHERE id = '4d19d08a-066f-4cdc-9d03-c4e963077071'
  AND email = 'pw-test-do-not-create@example.com';
-- Erwartete Ausgabe in psql: "DELETE 1".
-- Falls "DELETE 0": Email-ID-Paar matcht nicht → ROLLBACK + Tobi melden.


-- ---------------------------------------------------------------------
-- Schritt 5 — Verifikation. Beide Counts sollten 0 sein.
-- ---------------------------------------------------------------------
SELECT
    (SELECT COUNT(*) FROM users
        WHERE id = '4d19d08a-066f-4cdc-9d03-c4e963077071')
    AS user_remaining,
    (SELECT COUNT(*) FROM users
        WHERE email = 'pw-test-do-not-create@example.com')
    AS email_remaining;


-- ---------------------------------------------------------------------
-- Schritt 6 — Entscheidung.
-- Wenn alles gut aussah: nur diese eine Zeile noch ausführen:
--
--     COMMIT;
--
-- Wenn etwas merkwürdig war: stattdessen:
--
--     ROLLBACK;
--
-- ---------------------------------------------------------------------

-- (kein automatischer COMMIT — Operator entscheidet manuell)

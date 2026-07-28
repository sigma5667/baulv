/**
 * Warteliste-API (v25). Drei öffentliche Endpoints, alle POST —
 * die Mail-Links zeigen auf Frontend-Seiten, die den Token erst auf
 * Button-Klick einreichen (Mail-Scanner rufen Links per GET ab und
 * würden ein GET-Confirm ungewollt auslösen).
 */
import api from "./client";

export interface WaitlistSignupPayload {
  email: string;
  company_name: string;
  name?: string | null;
  consent: boolean;
  source?: string | null;
}

interface MessageResponse {
  message: string;
}

export async function joinWaitlist(
  payload: WaitlistSignupPayload,
): Promise<string> {
  const res = await api.post<MessageResponse>("/waitlist", payload);
  return res.data.message;
}

export async function confirmWaitlist(token: string): Promise<string> {
  const res = await api.post<MessageResponse>("/waitlist/confirm", {
    token,
  });
  return res.data.message;
}

export async function unsubscribeWaitlist(token: string): Promise<string> {
  const res = await api.post<MessageResponse>("/waitlist/unsubscribe", {
    token,
  });
  return res.data.message;
}

// ---------------------------------------------------------------------------
// Admin (v25.1) — Übersicht + Update-Versand. Beide Endpoints sind
// backend-seitig über ADMIN_EMAILS gedeckt; das Frontend rendert für
// Nicht-Admins zusätzlich einen lokalen 403-Fallback (gleiche
// Doppel-Absicherung wie /admin/analytics).
// ---------------------------------------------------------------------------

export interface WaitlistAdminEntry {
  email: string;
  company_name: string;
  name: string | null;
  status: "pending" | "confirmed" | "unsubscribed";
  signup_at: string;
  confirmed_at: string | null;
  unsubscribed_at: string | null;
  consent_text_version: string;
  source: string | null;
}

export interface WaitlistAdminOverview {
  total: number;
  counts: { pending: number; confirmed: number; unsubscribed: number };
  sources: { source: string | null; count: number }[];
  signups_last_7d: number;
  signups_last_30d: number;
  limit: number;
  offset: number;
  entries: WaitlistAdminEntry[];
}

export async function fetchWaitlistAdmin(params: {
  limit: number;
  offset: number;
}): Promise<WaitlistAdminOverview> {
  const res = await api.get<WaitlistAdminOverview>("/waitlist/admin", {
    params,
  });
  return res.data;
}

export interface WaitlistUpdateSendResult {
  dry_run: boolean;
  recipients: number;
  sent: number;
  failed: number;
  /** Nur im Trockenlauf gesetzt: exakt der Text des Echtlaufs. */
  preview?: { subject: string; text: string };
}

export async function sendWaitlistUpdate(payload: {
  subject: string;
  body: string;
  dry_run: boolean;
}): Promise<WaitlistUpdateSendResult> {
  const res = await api.post<WaitlistUpdateSendResult>(
    "/waitlist/admin/send-update",
    payload,
  );
  return res.data;
}

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

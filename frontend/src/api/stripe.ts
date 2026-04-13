import api from "./client";

export async function createCheckoutSession(plan: string): Promise<{ checkout_url: string }> {
  const res = await api.post(`/stripe/checkout?plan=${plan}`);
  return res.data;
}

export async function createPortalSession(): Promise<{ portal_url: string }> {
  const res = await api.post("/stripe/portal");
  return res.data;
}

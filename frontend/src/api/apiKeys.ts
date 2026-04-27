/**
 * Client for ``/api/auth/me/api-keys`` — the user's PAT management
 * surface. The interactive SPA is the only place these endpoints are
 * used; agents authenticate with PATs they got from this page, but
 * never call back into this client themselves.
 */

import api from "./client";

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
}

export interface ApiKeyCreated extends ApiKey {
  /** Plaintext token. Server returns this exactly once at creation. */
  token: string;
}

export interface McpAuditEntry {
  id: string;
  api_key_id: string | null;
  tool_name: string;
  arguments: Record<string, unknown> | null;
  result: "ok" | "error" | "rate_limited";
  error_message: string | null;
  latency_ms: number;
  created_at: string;
}

export interface PaginatedAudit {
  items: McpAuditEntry[];
  total: number;
  limit: number;
  offset: number;
}

export async function listApiKeys(): Promise<ApiKey[]> {
  const res = await api.get("/auth/me/api-keys");
  return res.data;
}

export async function createApiKey(payload: {
  name: string;
  expires_in_days?: number | null;
}): Promise<ApiKeyCreated> {
  const res = await api.post("/auth/me/api-keys", payload);
  return res.data;
}

export async function updateApiKey(
  id: string,
  payload: { expires_in_days?: number | null; clear_expires?: boolean }
): Promise<ApiKey> {
  const res = await api.patch(`/auth/me/api-keys/${id}`, payload);
  return res.data;
}

export async function revokeApiKey(id: string): Promise<void> {
  await api.delete(`/auth/me/api-keys/${id}`);
}

export async function fetchApiKeyAudit(
  id: string,
  opts: { limit?: number; offset?: number } = {}
): Promise<PaginatedAudit> {
  const res = await api.get(`/auth/me/api-keys/${id}/audit`, {
    params: { limit: opts.limit ?? 50, offset: opts.offset ?? 0 },
  });
  return res.data;
}

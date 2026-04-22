export interface User {
  id: string;
  email: string;
  full_name: string;
  company_name: string | null;
  subscription_plan: "basis" | "pro" | "enterprise";
  stripe_customer_id: string | null;
  marketing_email_opt_in: boolean;
  created_at: string;
}

export interface UserSessionSummary {
  id: string;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_used_at: string;
  expires_at: string;
  revoked_at: string | null;
  is_current: boolean;
}

export interface AuditLogEntry {
  id: string;
  event_type: string;
  meta: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface FeatureMatrix {
  manual_lv_editor: boolean;
  pdf_export: boolean;
  ai_plan_analysis: boolean;
  ai_position_generator: boolean;
  ai_chat: boolean;
  excel_export: boolean;
  angebotsvergleich: boolean;
  team_multiuser: boolean;
  api_access: boolean;
  project_limit: number | null;
  // True when BETA_UNLOCK_ALL_FEATURES=true on the server. The SPA
  // uses this flag to render a single tester banner — every actual
  // gating decision still goes through the individual boolean
  // flags above so there's no forked code path to maintain.
  // Optional in the type so old cached responses during a rolling
  // deploy deserialize cleanly as "banner off".
  beta_unlock_active?: boolean;
}

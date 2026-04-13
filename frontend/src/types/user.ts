export interface User {
  id: string;
  email: string;
  full_name: string;
  company_name: string | null;
  subscription_plan: "basis" | "pro" | "enterprise";
  stripe_customer_id: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface FeatureMatrix {
  onorm_library: boolean;
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
}

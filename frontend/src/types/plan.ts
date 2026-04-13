export interface Plan {
  id: string;
  project_id: string;
  filename: string;
  file_size_bytes: number | null;
  page_count: number | null;
  plan_type: string | null;
  analysis_status: string;
  created_at: string;
}

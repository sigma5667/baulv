export interface ONormDokument {
  id: string;
  norm_nummer: string;
  titel: string | null;
  trade: string | null;
  upload_status: string;
  created_at: string;
}

export interface ONormRegel {
  id: string;
  regel_code: string;
  trade: string;
  category: string | null;
  description_de: string;
  onorm_reference: string | null;
  is_active: boolean;
}

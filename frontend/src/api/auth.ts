import api from "./client";
import type { User, TokenResponse, FeatureMatrix } from "../types/user";

export async function registerUser(data: {
  email: string;
  password: string;
  full_name: string;
  company_name?: string;
}): Promise<TokenResponse> {
  const res = await api.post("/auth/register", data);
  return res.data;
}

export async function loginUser(data: {
  email: string;
  password: string;
}): Promise<TokenResponse> {
  const res = await api.post("/auth/login", data);
  return res.data;
}

export async function fetchMe(): Promise<User> {
  const res = await api.get("/auth/me");
  return res.data;
}

export async function updateProfile(data: {
  full_name?: string;
  company_name?: string;
}): Promise<User> {
  const res = await api.put("/auth/me", data);
  return res.data;
}

export async function fetchFeatures(): Promise<FeatureMatrix> {
  const res = await api.get("/auth/me/features");
  return res.data;
}

export async function fetchUsage(): Promise<{
  project_count: number;
  project_limit: number | null;
}> {
  const res = await api.get("/auth/me/usage");
  return res.data;
}

export async function requestPasswordReset(email: string): Promise<void> {
  await api.post("/auth/password-reset", { email });
}

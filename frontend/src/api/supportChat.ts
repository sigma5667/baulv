import api from "./client";

export interface SupportMessage {
  role: "user" | "assistant";
  content: string;
}

export interface SupportChatRequest {
  messages: SupportMessage[];
}

export interface SupportChatResponse {
  reply: string;
}

/**
 * Public endpoint — no auth header needed, but axios will attach one
 * if present (harmless on a public route).
 */
export async function sendSupportMessage(
  messages: SupportMessage[]
): Promise<string> {
  const { data } = await api.post<SupportChatResponse>("/support-chat", {
    messages,
  });
  return data.reply;
}

import api from "./client";

export interface ChatSession {
  id: string;
  project_id: string | null;
  title: string | null;
  created_at: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export const createChatSession = async (
  projectId?: string,
  title?: string
): Promise<ChatSession> => {
  const { data } = await api.post("/chat/sessions", {
    project_id: projectId,
    title,
  });
  return data;
};

export const listChatSessions = async (
  projectId?: string
): Promise<ChatSession[]> => {
  const { data } = await api.get("/chat/sessions", {
    params: projectId ? { project_id: projectId } : undefined,
  });
  return data;
};

export const renameChatSession = async (
  sessionId: string,
  title: string
): Promise<ChatSession> => {
  const { data } = await api.patch(`/chat/sessions/${sessionId}`, { title });
  return data;
};

export const deleteChatSession = async (sessionId: string): Promise<void> => {
  await api.delete(`/chat/sessions/${sessionId}`);
};

export const fetchMessages = async (sessionId: string): Promise<ChatMessage[]> => {
  const { data } = await api.get(`/chat/sessions/${sessionId}/messages`);
  return data;
};

export const sendMessage = async (sessionId: string, content: string): Promise<ChatMessage> => {
  const { data } = await api.post(`/chat/sessions/${sessionId}/messages`, { content });
  return data;
};

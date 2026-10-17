import type { Answer, Conversation, FeedbackEntry, Turn, User } from "./types";

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? r.statusText);
  return r.json();
}

export const api = {
  health: () => get<{ ok: boolean; wells: number; model_ready: boolean; model_detail: string; provider?: string; model?: string }>("/api/health"),
  users: () => get<User[]>("/api/users"),
  conversations: (userId: string) => get<Conversation[]>(`/api/conversations?user_id=${encodeURIComponent(userId)}`),
  conversation: (id: string, userId: string) =>
    get<{ turns: Turn[] }>(`/api/conversations/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId)}`),
  feedback: () => get<FeedbackEntry[]>("/api/feedback?limit=30"),
  ask: async (userId: string, question: string, conversationId?: string): Promise<Answer> => {
    const r = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, question, conversation_id: conversationId }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? r.statusText);
    return r.json();
  },
};

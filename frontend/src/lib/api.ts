// Schmaler API-Client. Token im localStorage; alle Aufrufe gehen an /api/v1.
const TOKEN_KEY = "selfmailer.token";

export const auth = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = auth.get();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`/api/v1${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 204) return undefined as T;
  const data = res.headers.get("content-type")?.includes("application/json")
    ? await res.json()
    : await res.text();
  if (!res.ok) {
    const detail = (data as { detail?: string })?.detail ?? String(data);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => req<T>("GET", p),
  post: <T>(p: string, b?: unknown) => req<T>("POST", p, b),
  patch: <T>(p: string, b?: unknown) => req<T>("PATCH", p, b),
  del: (p: string) => req<void>("DELETE", p),
};

// Binaer-Download mit Auth-Header (ein <a href> kann keinen Bearer setzen).
export async function download(path: string): Promise<void> {
  const token = auth.get();
  const res = await fetch(`/api/v1${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : "download";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// ---- Typen ----
export type User = { id: number; username: string; display_name: string; role: string; is_active: boolean };
export type Note = {
  id: number; title: string; body: string; color: string; pinned: boolean;
  created_at: string; updated_at: string;
};
export type Account = {
  id: number; label: string; email: string; protocol: string;
  imap_host: string; imap_port: number; smtp_host: string; smtp_port: number;
};
export type MsgHeader = {
  uid: string; subject: string; from: string; date: string; seen: boolean; flagged: boolean;
};
export type Attachment = { index: number; filename: string; content_type: string; size: number };
export type MsgDetail = MsgHeader & {
  to: string[]; message_id: string; text: string; html: string; attachments: Attachment[];
};

export type CalEvent = {
  id: number; title: string; description: string; location: string;
  start: string; end: string; all_day: boolean;
};
export type Contact = {
  id: number; first_name: string; last_name: string; email: string;
  phone: string; organization: string; notes: string;
};

export type DavKind = "caldav" | "carddav";
export type DavAccount = {
  id: number; kind: DavKind; label: string; url: string;
  username: string; last_sync: string | null; last_status: string;
};
export type FeedToken = { token: string; calendar_url: string; contacts_url: string };
export type SyncResult = {
  ok: boolean; imported: number; updated: number; removed: number; error: string;
};

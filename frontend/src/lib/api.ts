// Schmaler API-Client. Auth laeuft im Web ueber ein httpOnly-Session-Cookie
// (vom Browser automatisch mitgesendet) — KEIN Token im localStorage mehr, damit
// es per XSS nicht ausgelesen werden kann. Die native APK nutzt weiterhin den
// Bearer-Header; das Backend akzeptiert beides.

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`/api/v1${path}`, {
    method,
    headers,
    credentials: "same-origin", // sendet das httpOnly-Session-Cookie mit
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
  put: <T>(p: string, b?: unknown) => req<T>("PUT", p, b),
  patch: <T>(p: string, b?: unknown) => req<T>("PATCH", p, b),
  del: (p: string) => req<void>("DELETE", p),
};

// Text in die Zwischenablage — mit Fallback fuer http/LAN (kein Secure Context,
// dort ist navigator.clipboard nicht verfuegbar): unsichtbares Textarea + execCommand.
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* Fallback unten */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.setAttribute("readonly", "");
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch { return false; }
}

// Binaer-Download — Auth ueber das Session-Cookie (ein <a href> kann keinen
// Bearer setzen, das Cookie wird aber automatisch mitgesendet).
export async function download(path: string): Promise<void> {
  const res = await fetch(`/api/v1${path}`, { credentials: "same-origin" });
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
export type LoginResponse = {
  token_type: string; needs_totp: boolean; mfa_token: string;
};
export type TotpStatus = { enabled: boolean; backup_codes_remaining: number };
export type TotpSetup = { secret: string; otpauth_uri: string };
export type Note = {
  id: number; title: string; body: string; color: string; pinned: boolean;
  created_at: string; updated_at: string;
};
export type Account = {
  id: number; label: string; email: string; protocol: string;
  imap_host: string; imap_port: number; imap_ssl: boolean;
  smtp_host: string; smtp_port: number; smtp_starttls: boolean;
  auth_user: string; signature: string;
};
export type MsgHeader = {
  uid: string; subject: string; from: string; date: string; seen: boolean; flagged: boolean;
  snippet: string; has_attachments: boolean;
};
export type Attachment = { index: number; filename: string; content_type: string; size: number };
export type AuthInfo = {
  spf: string | null; dkim: string | null; dmarc: string | null;
  verdict: string; self_spoof: boolean; from_domain: string; reasons: string[];
};
export type MsgDetail = MsgHeader & {
  to: string[]; message_id: string; text: string; html: string; attachments: Attachment[];
  auth?: AuthInfo | null;
};

export type CalEvent = {
  id: number; title: string; description: string; location: string;
  start: string; end: string; all_day: boolean;
  dav_account_id?: number | null;
  source_key?: string; source_name?: string; source_color?: string;
};
export type GcalCalendar = { id: string; name: string; primary: boolean; color?: string; writable?: boolean };
export type Contact = {
  id: number; first_name: string; last_name: string; email: string;
  phone: string; mobile: string; work_phone: string;
  organization: string; title: string; website: string;
  street: string; postal_code: string; city: string; country: string;
  notes: string; birthday: string | null;
};
export type Task = {
  id: number; title: string; notes: string; due: string | null;
  done: boolean; position: number;
};

export type DavKind = "caldav" | "carddav" | "ics" | "gcal";
export type DavAccount = {
  id: number; kind: DavKind; label: string; url: string;
  username: string; last_sync: string | null; last_status: string;
};
export type FeedToken = { token: string; calendar_url: string; contacts_url: string };
export type Rule = {
  id: number; field: string; value: string; target_folder: string;
  mark_read: boolean; star: boolean; enabled: boolean; position: number;
};
export type SyncResult = {
  ok: boolean; imported: number; updated: number; removed: number; error: string;
};
export type MigrateFolder = { source: string; dest: string; count: number; copied: number; skipped: number };
export type MigrateResult = { folders: MigrateFolder[]; errors: string[]; dry_run: boolean };
export type TransferResult = { copied: number; skipped: number; deleted: number; errors: string[] };

import { useEffect, useState } from "react";
import { api, type Account, type FolderCount } from "../lib/api";
import { useLang } from "../lib/i18n";

type PushConfig = { enabled: boolean; ntfy_url: string; topic: string };

function randomTopic(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  const hex = Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
  return "selfmailer-" + hex;
}
function leaf(name: string): string {
  const bySlash = name.split("/").pop() || name;
  return bySlash === name ? name.split(".").pop() || name : bySlash;
}

const card: React.CSSProperties = {
  background: "var(--self-bg-2)", borderRadius: 12, padding: 16, marginBottom: 14,
};
const input: React.CSSProperties = {
  width: "100%", padding: "10px 12px", borderRadius: 8, border: "1px solid var(--self-bg-3)",
  background: "var(--self-bg-1)", color: "var(--self-text)", boxSizing: "border-box",
};
const btn: React.CSSProperties = {
  padding: "9px 16px", borderRadius: 8, border: "none", cursor: "pointer",
  background: "var(--self-teal)", color: "#fff", fontWeight: 600,
};

export function Notify() {
  const { t } = useLang();
  const [cfg, setCfg] = useState<PushConfig>({ enabled: false, ntfy_url: "", topic: "" });
  const [url, setUrl] = useState("");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [foldersByAcc, setFoldersByAcc] = useState<Record<number, FolderCount[]>>({});
  const [enabledByAcc, setEnabledByAcc] = useState<Record<number, Set<string>>>({});
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    try {
      const c = await api.get<PushConfig>("/push");
      setCfg(c);
      setUrl(c.ntfy_url);
      setAccounts(await api.get<Account[]>("/accounts"));
    } catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  async function savePush(enabled: boolean) {
    setErr(""); setNote("");
    const topic = cfg.topic || randomTopic();
    try {
      const saved = await api.put<PushConfig>("/push", { ntfy_url: url.trim(), topic, enabled });
      setCfg(saved);
      setUrl(saved.ntfy_url);
      setNote("Gespeichert");
    } catch (e) { setErr((e as Error).message); }
  }

  async function toggleAccount(acc: Account) {
    if (open === acc.id) { setOpen(null); return; }
    setOpen(acc.id);
    if (foldersByAcc[acc.id]) return;
    try {
      const folders = await api.get<FolderCount[]>(`/mail/${acc.id}/folders/counts`);
      const enabled = await api.get<string[]>(`/push/folders?account_id=${acc.id}`);
      setFoldersByAcc((m) => ({ ...m, [acc.id]: folders }));
      setEnabledByAcc((m) => ({ ...m, [acc.id]: new Set(enabled) }));
    } catch (e) { setErr((e as Error).message); }
  }

  async function toggleFolder(accId: number, name: string, on: boolean) {
    const cur = new Set(enabledByAcc[accId] ?? new Set<string>());
    if (on) cur.add(name); else cur.delete(name);
    setEnabledByAcc((m) => ({ ...m, [accId]: cur }));
    try { await api.put("/push/folders", { account_id: accId, folders: Array.from(cur) }); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <div style={{ maxWidth: 640, margin: "0 auto", padding: 16 }}>
      <h2 style={{ color: "var(--self-text)", marginTop: 0 }}>🔔 {t("nav.notify")}</h2>

      {err && <div style={{ ...card, background: "#5b1f1f", color: "#ffd7d7" }}>{err}</div>}
      {note && <div style={{ color: "var(--self-teal-bright)", marginBottom: 10 }}>{note}</div>}

      <div style={card}>
        <div style={{ color: "var(--self-text)", fontWeight: 600, marginBottom: 4 }}>ntfy-Push (self-hosted)</div>
        <p style={{ color: "var(--self-text)", opacity: 0.7, fontSize: 13, marginTop: 0 }}>
          Der Server schickt bei neuer Mail einen Push an deinen ntfy-Server; die ntfy-App auf dem Handy
          zeigt die Benachrichtigung. Status: <b>{cfg.enabled ? "aktiv" : "aus"}</b>
        </p>
        <label style={{ color: "var(--self-text)", fontSize: 13 }}>ntfy-Server-URL</label>
        <input style={input} value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder="http://192.168.1.10:8095" />
        {cfg.topic && (
          <p style={{ color: "var(--self-text)", fontSize: 13, marginBottom: 0 }}>
            Thema (in der ntfy-App abonnieren):{" "}
            <code style={{ color: "var(--self-teal-bright)" }}>{cfg.topic}</code>
          </p>
        )}
        <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
          <button style={btn} onClick={() => savePush(true)}>Speichern & aktivieren</button>
          {cfg.enabled && (
            <button style={{ ...btn, background: "var(--self-bg-3)", color: "var(--self-text)" }}
              onClick={() => savePush(false)}>Deaktivieren</button>
          )}
        </div>
      </div>

      <div style={card}>
        <div style={{ color: "var(--self-text)", fontWeight: 600 }}>Ordner pro Konto</div>
        <p style={{ color: "var(--self-text)", opacity: 0.7, fontSize: 13, marginTop: 4 }}>
          Wähle je Konto, welche Ordner eine Benachrichtigung auslösen. Ohne Auswahl wird nicht benachrichtigt.
        </p>
        {accounts.map((acc) => {
          const isOpen = open === acc.id;
          const folders = foldersByAcc[acc.id] ?? [];
          const enabled = enabledByAcc[acc.id] ?? new Set<string>();
          return (
            <div key={acc.id} style={{ borderTop: "1px solid var(--self-bg-3)", padding: "10px 0" }}>
              <div role="button" tabIndex={0} aria-expanded={isOpen} style={{ cursor: "pointer", color: "var(--self-text)", display: "flex", justifyContent: "space-between" }}
                onClick={() => toggleAccount(acc)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleAccount(acc); } }}>
                <span><b>{acc.label}</b>{acc.label !== acc.email && <span style={{ opacity: 0.6 }}> · {acc.email}</span>}</span>
                <span style={{ opacity: 0.6 }}>{isOpen ? "▾" : "▸"}</span>
              </div>
              {isOpen && (
                <div style={{ marginTop: 4 }}>
                  {folders.length === 0 && <div style={{ opacity: 0.6, color: "var(--self-text)", fontSize: 13, padding: "8px 0" }}>Lade Ordner …</div>}
                  {folders.map((f) => (
                    <label key={f.name} style={{
                      display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
                      padding: "11px 6px", cursor: "pointer", borderTop: "1px solid var(--self-bg-3)",
                    }}>
                      <span style={{ color: "var(--self-text)", textAlign: "left", flex: 1 }}>
                        {leaf(f.name)}
                        {f.unseen > 0 && (
                          <span style={{ color: "var(--self-teal-bright)", fontSize: 12, marginLeft: 8 }}>({f.unseen})</span>
                        )}
                      </span>
                      <input type="checkbox" checked={enabled.has(f.name)}
                        onChange={(e) => toggleFolder(acc.id, f.name, e.target.checked)}
                        style={{ width: 18, height: 18, accentColor: "var(--self-teal)", cursor: "pointer", flex: "0 0 auto" }} />
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

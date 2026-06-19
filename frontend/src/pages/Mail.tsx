import { useEffect, useState } from "react";
import { api, download, type Account, type MsgHeader, type MsgDetail } from "../lib/api";
import { useLang } from "../lib/i18n";
import { Compose, emptyDraft, replyDraft, forwardDraft, type Draft } from "../components/Compose";

function fmtSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function Mail() {
  const { t } = useLang();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [folders, setFolders] = useState<string[]>([]);
  const [folder, setFolder] = useState("INBOX");
  const [messages, setMessages] = useState<MsgHeader[]>([]);
  const [open, setOpen] = useState<MsgDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get<Account[]>("/accounts").then((a) => {
      setAccounts(a);
      if (a.length) setActiveId(a[0].id);
    });
  }, []);

  // Ordnerliste laden, sobald ein Konto aktiv ist.
  useEffect(() => {
    if (activeId == null) return;
    setFolder("INBOX");
    api.get<string[]>(`/mail/${activeId}/folders`)
      .then(setFolders)
      .catch(() => setFolders([]));
  }, [activeId]);

  function reload() {
    if (activeId == null) return;
    setLoading(true); setErr(""); setOpen(null);
    api.get<MsgHeader[]>(`/mail/${activeId}/messages?folder=${encodeURIComponent(folder)}&limit=50`)
      .then(setMessages)
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false));
  }
  useEffect(reload, [activeId, folder]);

  // Lokalen Header nach einer Aktion aktualisieren (immutabel).
  function patchHeader(uid: string, patch: Partial<MsgHeader>) {
    setMessages((ms) => ms.map((m) => (m.uid === uid ? { ...m, ...patch } : m)));
  }

  async function openMsg(uid: string) {
    if (activeId == null) return;
    setErr("");
    try {
      const msg = await api.get<MsgDetail>(`/mail/${activeId}/messages/${uid}?folder=${encodeURIComponent(folder)}`);
      setOpen(msg);
      if (!msg.seen) {
        // Beim Oeffnen als gelesen markieren (best effort).
        api.post(`/mail/${activeId}/messages/${uid}/flags?folder=${encodeURIComponent(folder)}&seen=true`).catch(() => {});
        patchHeader(uid, { seen: true });
      }
    } catch (e) { setErr((e as Error).message); }
  }

  async function toggleSeen(m: MsgHeader) {
    if (activeId == null) return;
    const next = !m.seen;
    patchHeader(m.uid, { seen: next });
    try { await api.post(`/mail/${activeId}/messages/${m.uid}/flags?folder=${encodeURIComponent(folder)}&seen=${next}`); }
    catch (e) { patchHeader(m.uid, { seen: m.seen }); setErr((e as Error).message); }
  }
  async function toggleFlag(m: MsgHeader) {
    if (activeId == null) return;
    const next = !m.flagged;
    patchHeader(m.uid, { flagged: next });
    try { await api.post(`/mail/${activeId}/messages/${m.uid}/flags?folder=${encodeURIComponent(folder)}&flagged=${next}`); }
    catch (e) { patchHeader(m.uid, { flagged: m.flagged }); setErr((e as Error).message); }
  }
  async function markUnread(uid: string) {
    if (activeId == null) return;
    patchHeader(uid, { seen: false });
    setOpen(null);
    try { await api.post(`/mail/${activeId}/messages/${uid}/flags?folder=${encodeURIComponent(folder)}&seen=false`); }
    catch (e) { setErr((e as Error).message); }
  }
  async function del(m: MsgHeader) {
    if (activeId == null) return;
    if (!confirm(t("mail.confirmDelete"))) return;
    setErr("");
    try {
      await api.del(`/mail/${activeId}/messages/${m.uid}?folder=${encodeURIComponent(folder)}`);
      setMessages((ms) => ms.filter((x) => x.uid !== m.uid));
      if (open?.uid === m.uid) setOpen(null);
    } catch (e) { setErr((e as Error).message); }
  }

  if (accounts.length === 0) {
    return <p className="muted">{t("mail.noAccount")}</p>;
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <select value={activeId ?? ""} onChange={(e) => setActiveId(Number(e.target.value))} style={{ maxWidth: 260 }}>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.label || a.email}</option>)}
        </select>
        {folders.length > 0 && (
          <select value={folder} onChange={(e) => setFolder(e.target.value)} style={{ maxWidth: 200 }} aria-label={t("mail.folderLabel")}>
            {folders.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        )}
        <button className="primary" onClick={() => setDraft(emptyDraft())}>{t("mail.newMail")}</button>
        <span className="grow" />
        <button className="ghost" onClick={reload}>↻</button>
      </div>

      {err && <div className="err">{err}</div>}
      {loading && <p className="muted">{t("mail.loadingMessages")}</p>}

      {open ? (
        <div className="card" style={{ padding: "1.2rem" }}>
          <div className="row" style={{ marginBottom: "0.6rem" }}>
            <button className="ghost" onClick={() => setOpen(null)}>{t("mail.back")}</button>
            <span className="grow" />
            <button onClick={() => setDraft(replyDraft(open, t))}>{t("mail.reply")}</button>
            <button onClick={() => setDraft(forwardDraft(open, t))}>{t("mail.forward")}</button>
            <button className="ghost" onClick={() => toggleFlag(open)} title={t("mail.flag")}>
              {(messages.find((m) => m.uid === open.uid)?.flagged ?? open.flagged) ? "★" : "☆"}
            </button>
            <button className="ghost" onClick={() => markUnread(open.uid)}>{t("mail.markUnread")}</button>
            <button className="ghost" onClick={() => del(open)} title={t("mail.delete")}>🗑</button>
          </div>
          <h2 style={{ marginBottom: "0.2rem" }}>{open.subject || t("mail.noSubject")}</h2>
          <div className="mail-from">{open.from} · {open.date}</div>
          <hr style={{ borderColor: "var(--self-line)", margin: "1rem 0" }} />
          <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
            {open.text || open.html.replace(/<[^>]+>/g, "") || t("mail.emptyBody")}
          </div>
          {open.attachments?.length > 0 && (
            <div style={{ marginTop: "1.2rem", borderTop: "1px solid var(--self-line)", paddingTop: "0.8rem" }}>
              <div className="label" style={{ marginBottom: "0.5rem" }}>📎 {t("mail.attachments")}</div>
              <div className="row" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
                {open.attachments.map((att) => (
                  <button
                    key={att.index}
                    className="ghost"
                    style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}
                    onClick={() => download(`/mail/${activeId}/messages/${open.uid}/attachments/${att.index}?folder=${encodeURIComponent(folder)}`).catch((e) => setErr((e as Error).message))}
                  >
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", maxWidth: 220, whiteSpace: "nowrap" }}>⬇ {att.filename}</span>
                    <span className="muted" style={{ fontSize: "0.72rem" }}>{fmtSize(att.size)}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="mail-list">
          {messages.map((m) => (
            <div className={`mail-row ${m.seen ? "" : "unseen"}`} key={m.uid}>
              <button
                className="ghost"
                style={{ padding: "0 0.3rem", color: m.flagged ? "var(--self-cyan, #00e5c8)" : undefined }}
                onClick={() => toggleFlag(m)}
                title={t("mail.flag")}
              >
                {m.flagged ? "★" : "☆"}
              </button>
              <div className="grow" style={{ cursor: "pointer", overflow: "hidden" }} onClick={() => openMsg(m.uid)}>
                <div className="mail-subj">{m.subject || t("mail.noSubject")}</div>
                <div className="mail-from">{m.from}</div>
              </div>
              <div className="muted" style={{ fontSize: "0.78rem", whiteSpace: "nowrap" }}>{m.date?.slice(0, 16)}</div>
              <button className="ghost" style={{ padding: "0 0.3rem" }} onClick={() => toggleSeen(m)} title={m.seen ? t("mail.markUnread") : t("mail.markRead")}>
                {m.seen ? "○" : "●"}
              </button>
              <button className="ghost" style={{ padding: "0 0.3rem" }} onClick={() => del(m)} title={t("mail.delete")}>🗑</button>
            </div>
          ))}
          {!loading && messages.length === 0 && <p className="muted">{t("mail.noMessages")}</p>}
        </div>
      )}

      {draft && activeId != null && (
        <Compose accountId={activeId} draft={draft} onClose={() => setDraft(null)} />
      )}
    </div>
  );
}

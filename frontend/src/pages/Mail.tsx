import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, download, type Account, type MsgHeader, type MsgDetail } from "../lib/api";
import { useLang } from "../lib/i18n";
import { buildFolderTree, SPECIAL_ICON, type FolderNode } from "../lib/folders";
import { Compose, emptyDraft, replyDraft, forwardDraft, type Draft } from "../components/Compose";

function fmtSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function Mail({ search = "" }: { search?: string }) {
  const { t } = useLang();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [folders, setFolders] = useState<string[]>([]);
  const [folder, setFolder] = useState("INBOX");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [messages, setMessages] = useState<MsgHeader[]>([]);
  const [open, setOpen] = useState<MsgDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const tree = useMemo(() => buildFolderTree(folders), [folders]);

  useEffect(() => {
    api.get<Account[]>("/accounts").then((a) => {
      setAccounts(a);
      if (a.length) setActiveId(a[0].id);
    });
  }, []);

  function refreshFolders() {
    if (activeId == null) return;
    api.get<string[]>(`/mail/${activeId}/folders`).then(setFolders).catch(() => setFolders([]));
  }
  // Ordnerliste laden, sobald ein Konto aktiv ist.
  useEffect(() => {
    if (activeId == null) return;
    setFolder("INBOX");
    refreshFolders();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  async function newFolder() {
    if (activeId == null) return;
    const name = prompt(t("folder.newPrompt", { parent: folder }));
    if (!name || !name.trim()) return;
    try {
      await api.post(`/mail/${activeId}/folders?name=${encodeURIComponent(name.trim())}&parent=${encodeURIComponent(folder)}`);
      refreshFolders();
    } catch (e) { setErr((e as Error).message); }
  }
  async function delFolder(path: string) {
    if (activeId == null) return;
    if (!confirm(t("folder.deleteConfirm", { name: path }))) return;
    try {
      await api.del(`/mail/${activeId}/folders?name=${encodeURIComponent(path)}`);
      if (folder === path) setFolder("INBOX");
      refreshFolders();
    } catch (e) { setErr((e as Error).message); }
  }

  // Posteingang standardmäßig aufgeklappt.
  useEffect(() => {
    setExpanded(new Set(tree.filter((n) => n.special === "inbox").map((n) => n.path)));
  }, [tree]);

  function reload() {
    if (activeId == null) return;
    setLoading(true); setErr(""); setOpen(null);
    api.get<MsgHeader[]>(`/mail/${activeId}/messages?folder=${encodeURIComponent(folder)}&limit=50`)
      .then(setMessages)
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false));
  }
  useEffect(reload, [activeId, folder]);

  function patchHeader(uid: string, patch: Partial<MsgHeader>) {
    setMessages((ms) => ms.map((m) => (m.uid === uid ? { ...m, ...patch } : m)));
  }
  function toggleExpand(path: string) {
    setExpanded((s) => {
      const n = new Set(s);
      if (n.has(path)) n.delete(path); else n.add(path);
      return n;
    });
  }

  async function openMsg(uid: string) {
    if (activeId == null) return;
    setErr("");
    try {
      const msg = await api.get<MsgDetail>(`/mail/${activeId}/messages/${uid}?folder=${encodeURIComponent(folder)}`);
      setOpen(msg);
      if (!msg.seen) {
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
  async function moveMsg(uid: string, dest: string) {
    if (activeId == null || !dest) return;
    setErr("");
    try {
      await api.post(`/mail/${activeId}/messages/${uid}/move?folder=${encodeURIComponent(folder)}&dest=${encodeURIComponent(dest)}`);
      setMessages((ms) => ms.filter((x) => x.uid !== uid));
      if (open?.uid === uid) setOpen(null);
    } catch (e) { setErr((e as Error).message); }
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

  function renderNode(node: FolderNode, depth: number): ReactNode {
    const hasKids = node.children.length > 0;
    const isOpen = expanded.has(node.path);
    const label = node.special ? t(`folder.${node.special}`) : node.label;
    const icon = node.special ? SPECIAL_ICON[node.special] : "📁";
    return (
      <div key={node.path}>
        <div style={{ display: "flex", alignItems: "center" }}>
          {hasKids ? (
            <button className="mail-folder-toggle" style={{ marginLeft: depth * 12 }} onClick={() => toggleExpand(node.path)}>
              {isOpen ? "▼" : "▶"}
            </button>
          ) : (
            <span style={{ flex: "0 0 14px", width: 14, marginLeft: depth * 12 }} />
          )}
          <button
            className={`mail-folder ${node.path === folder ? "active" : ""}`}
            style={{ flex: 1, minWidth: 0 }}
            onClick={() => setFolder(node.path)}
            title={node.path}
          >
            <span>{icon}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
          </button>
          {!node.special && (
            <button
              className="mail-folder-toggle"
              style={{ flex: "0 0 auto", width: "auto", padding: "0 0.3rem" }}
              onClick={() => delFolder(node.path)}
              title={t("common.delete")}
            >
              🗑
            </button>
          )}
        </div>
        {hasKids && isOpen && node.children.map((c) => renderNode(c, depth + 1))}
      </div>
    );
  }

  if (accounts.length === 0) {
    return <p className="muted">{t("mail.noAccount")}</p>;
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <button className="primary" onClick={() => setDraft(emptyDraft())}>{t("mail.newMail")}</button>
        <span className="grow" />
        <button className="ghost" onClick={reload}>↻</button>
      </div>

      {err && <div className="err" style={{ marginBottom: "0.8rem" }}>{err}</div>}

      <div className="mail-layout">
        {/* Mailbox-Ordnerbaum */}
        <aside className="mail-folders">
          <div className="mail-mailbox-head" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <span>{t("mail.mailbox")}</span>
            <button className="mail-folder-toggle" style={{ width: "auto", fontSize: "0.9rem", padding: "0 0.2rem" }} onClick={newFolder} title={t("folder.new")}>＋</button>
          </div>
          {accounts.length > 1 && (
            <select
              value={activeId ?? ""}
              onChange={(e) => setActiveId(Number(e.target.value))}
              style={{ marginBottom: "0.5rem", fontSize: "0.82rem" }}
            >
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.label || a.email}</option>)}
            </select>
          )}
          {tree.map((n) => renderNode(n, 0))}
        </aside>

        {/* Listen-Spalte */}
        <div className="mail-listcol">
          {loading && <p className="muted">{t("mail.loadingMessages")}</p>}
          <div className="mail-list">
            {(search
              ? messages.filter((m) => `${m.subject} ${m.from}`.toLowerCase().includes(search.toLowerCase()))
              : messages
            ).map((m) => (
              <div
                className={`mail-row ${m.seen ? "" : "unseen"}`}
                key={m.uid}
                style={{ display: "flex", alignItems: "center", gap: "0.5rem", borderColor: open?.uid === m.uid ? "var(--self-teal)" : undefined }}
              >
                <button
                  className="ghost"
                  style={{ padding: "0 0.2rem", flex: "0 0 auto", color: m.flagged ? "var(--self-cyan, #00e5c8)" : undefined }}
                  onClick={() => toggleFlag(m)}
                  title={t("mail.flag")}
                >
                  {m.flagged ? "★" : "☆"}
                </button>
                <div className="grow" style={{ cursor: "pointer", overflow: "hidden", minWidth: 0 }} onClick={() => openMsg(m.uid)}>
                  <div className="mail-subj" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.subject || t("mail.noSubject")}</div>
                  <div className="mail-from" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.from}</div>
                </div>
                <div className="muted" style={{ fontSize: "0.74rem", whiteSpace: "nowrap", flex: "0 0 auto" }}>{m.date?.slice(0, 16)}</div>
                <button className="ghost" style={{ padding: "0 0.2rem", flex: "0 0 auto" }} onClick={() => toggleSeen(m)} title={m.seen ? t("mail.markUnread") : t("mail.markRead")}>
                  {m.seen ? "○" : "●"}
                </button>
                <button className="ghost" style={{ padding: "0 0.2rem", flex: "0 0 auto" }} onClick={() => del(m)} title={t("mail.delete")}>🗑</button>
              </div>
            ))}
            {!loading && messages.length === 0 && <p className="muted">{t("mail.noMessages")}</p>}
          </div>
        </div>

        {/* Lese-Spalte */}
        {open ? (
          <div className="mail-readcol">
            <div className="row" style={{ marginBottom: "0.6rem", flexWrap: "wrap" }}>
              <button onClick={() => setDraft(replyDraft(open, t))}>{t("mail.reply")}</button>
              <button onClick={() => setDraft(forwardDraft(open, t))}>{t("mail.forward")}</button>
              <button className="ghost" onClick={() => toggleFlag(open)} title={t("mail.flag")}>
                {(messages.find((m) => m.uid === open.uid)?.flagged ?? open.flagged) ? "★" : "☆"}
              </button>
              <button className="ghost" onClick={() => markUnread(open.uid)}>{t("mail.markUnread")}</button>
              {folders.length > 1 && (
                <select
                  value=""
                  title={t("mail.moveTo")}
                  onChange={(e) => { moveMsg(open.uid, e.target.value); e.currentTarget.value = ""; }}
                  style={{ fontSize: "0.82rem", maxWidth: 160 }}
                >
                  <option value="">📁 {t("mail.moveTo")}</option>
                  {folders.filter((f) => f !== folder).map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              )}
              <button className="ghost" onClick={() => del(open)} title={t("mail.delete")}>🗑</button>
              <span className="grow" />
              <button className="ghost" onClick={() => setOpen(null)} title={t("mail.back")}>✕</button>
            </div>
            <h2 style={{ marginBottom: "0.2rem", fontSize: "1.2rem" }}>{open.subject || t("mail.noSubject")}</h2>
            <div className="mail-from">{open.from} · {open.date}</div>
            <hr style={{ borderColor: "var(--self-line)", margin: "0.9rem 0" }} />
            {open.html ? (
              <iframe
                title="mail-body"
                sandbox=""
                srcDoc={open.html}
                style={{ width: "100%", height: "62vh", border: "none", background: "#fff", borderRadius: "6px" }}
              />
            ) : (
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
                {open.text || t("mail.emptyBody")}
              </div>
            )}
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
          <div className="mail-placeholder">{t("mail.selectHint")}</div>
        )}
      </div>

      {draft && activeId != null && (
        <Compose accountId={activeId} draft={draft} onClose={() => setDraft(null)} />
      )}
    </div>
  );
}

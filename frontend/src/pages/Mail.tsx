import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, download, type Account, type MsgHeader, type MsgDetail } from "../lib/api";
import { useLang } from "../lib/i18n";
import { buildFolderTree, specialKind, SPECIAL_ICON, type FolderNode } from "../lib/folders";
import { Compose, emptyDraft, replyDraft, forwardDraft, type Draft } from "../components/Compose";

type FolderCount = { name: string; unseen: number; total: number };
type Sel = { acc: number; folder: string };

function fmtSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function inDateRange(dateStr: string, from?: string, to?: string): boolean {
  if (!from && !to) return true;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return true;
  if (from && d < new Date(from)) return false;
  if (to) { const end = new Date(to); end.setHours(23, 59, 59, 999); if (d > end) return false; }
  return true;
}

type MailFilter = {
  from: string; subject: string; dateFrom: string; dateTo: string;
  unread: boolean; starred: boolean; attachments: boolean;
};

function loadSet(key: string): Set<number> {
  try { const v = JSON.parse(localStorage.getItem(key) || "[]"); return new Set(Array.isArray(v) ? v : []); }
  catch { return new Set(); }
}

export function Mail({ search = "", filter, pollMin = 5 }: { search?: string; filter?: MailFilter; pollMin?: number }) {
  const { t } = useLang();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [foldersByAcc, setFoldersByAcc] = useState<Record<number, FolderCount[]>>({});
  const [sel, setSel] = useState<Sel | null>(null);
  const [collapsedAcc, setCollapsedAcc] = useState<Set<number>>(() => loadSet("selfmailer.collapsedAcc"));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [messages, setMessages] = useState<MsgHeader[]>([]);
  const [open, setOpen] = useState<MsgDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dragUids, setDragUids] = useState<string[]>([]);
  const [dropPath, setDropPath] = useState<string | null>(null);
  const [ctxMenu, setCtxMenu] = useState<{ acc: number; node: FolderNode; x: number; y: number } | null>(null);
  const [listW, setListW] = useState<number>(() => {
    const v = Number(localStorage.getItem("selfmailer.listW"));
    return v >= 260 && v <= 760 ? v : 380;
  });
  const [foldersW, setFoldersW] = useState<number>(() => {
    const v = Number(localStorage.getItem("selfmailer.foldersW"));
    return v >= 160 && v <= 420 ? v : 210;
  });

  function makeResize(current: number, setW: (n: number) => void, key: string, min: number, max: number) {
    return (e: React.MouseEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      let last = current;
      function move(ev: MouseEvent) { last = Math.max(min, Math.min(max, current + ev.clientX - startX)); setW(last); }
      function up() {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        localStorage.setItem(key, String(last));
      }
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    };
  }
  const startResize = makeResize(listW, setListW, "selfmailer.listW", 260, 760);
  const startResizeFolders = makeResize(foldersW, setFoldersW, "selfmailer.foldersW", 160, 420);

  const activeId = sel?.acc ?? null;
  const folder = sel?.folder ?? "INBOX";

  // --- Konten + Ordner (mit Zaehlern) laden ---
  async function loadAccountFolders(a: Account) {
    try { await api.post(`/mail/${a.id}/folders/defaults`); } catch { /* egal */ }
    try {
      const fc = await api.get<FolderCount[]>(`/mail/${a.id}/folders/counts`);
      setFoldersByAcc((m) => ({ ...m, [a.id]: fc }));
    } catch {
      setFoldersByAcc((m) => ({ ...m, [a.id]: [{ name: "INBOX", unseen: 0, total: 0 }] }));
    }
  }
  useEffect(() => {
    api.get<Account[]>("/accounts").then((list) => {
      setAccounts(list);
      if (list.length) setSel((s) => s ?? { acc: list[0].id, folder: "INBOX" });
      list.forEach(loadAccountFolders);
    }).catch((e) => setErr((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const treesByAcc = useMemo(() => {
    const out: Record<number, FolderNode[]> = {};
    for (const [id, fcs] of Object.entries(foldersByAcc)) out[Number(id)] = buildFolderTree(fcs.map((f) => f.name));
    return out;
  }, [foldersByAcc]);

  function unseenOf(accId: number, path: string): number {
    return (foldersByAcc[accId] || []).find((f) => f.name === path)?.unseen ?? 0;
  }
  function rollupUnseen(accId: number): number {
    return (foldersByAcc[accId] || []).reduce((s, f) => s + (f.unseen || 0), 0);
  }
  // Ungelesen-Zaehler lokal anpassen (ohne erneuten IMAP-Abruf).
  function bumpUnseen(accId: number, path: string, delta: number) {
    setFoldersByAcc((m) => ({
      ...m,
      [accId]: (m[accId] || []).map((f) => f.name === path ? { ...f, unseen: Math.max(0, f.unseen + delta) } : f),
    }));
  }
  function refreshCounts(accId: number) {
    api.get<FolderCount[]>(`/mail/${accId}/folders/counts`)
      .then((fc) => setFoldersByAcc((m) => ({ ...m, [accId]: fc }))).catch(() => {});
  }
  // ↻ pro Konto: Filterregeln anwenden, Zaehler neu holen + aktive Liste neu laden.
  async function refreshAccount(accId: number) {
    try { await api.post(`/mail/${accId}/rules/apply`); } catch { /* egal */ }
    refreshCounts(accId);
    if (activeId === accId) reload();
  }

  function toggleAccount(accId: number) {
    setCollapsedAcc((s) => {
      const n = new Set(s);
      if (n.has(accId)) n.delete(accId); else n.add(accId);
      localStorage.setItem("selfmailer.collapsedAcc", JSON.stringify([...n]));
      return n;
    });
  }
  function expKey(accId: number, path: string) { return `${accId}:${path}`; }
  function toggleExpand(accId: number, path: string) {
    setExpanded((s) => {
      const n = new Set(s);
      const k = expKey(accId, path);
      if (n.has(k)) n.delete(k); else n.add(k);
      return n;
    });
  }
  // Posteingaenge initial aufgeklappt.
  useEffect(() => {
    setExpanded((prev) => {
      const n = new Set(prev);
      for (const [id, tree] of Object.entries(treesByAcc))
        for (const node of tree) if (node.special === "inbox") n.add(expKey(Number(id), node.path));
      return n;
    });
  }, [treesByAcc]);

  // --- Ordner-Verwaltung (pro Konto) ---
  async function newFolder(accId: number) {
    const name = prompt(t("folder.newPrompt", { parent: t("folder.inbox") }));
    if (!name || !name.trim()) return;
    try { await api.post(`/mail/${accId}/folders?name=${encodeURIComponent(name.trim())}&parent=INBOX`); loadAccountFolders(accountById(accId)); }
    catch (e) { setErr((e as Error).message); }
  }
  async function newSubfolder(accId: number, node: FolderNode) {
    const name = prompt(t("folder.newPrompt", { parent: node.special ? t(`folder.${node.special}`) : node.label }));
    if (!name || !name.trim()) return;
    try {
      await api.post(`/mail/${accId}/folders?name=${encodeURIComponent(name.trim())}&parent=${encodeURIComponent(node.path)}`);
      setExpanded((s) => new Set(s).add(expKey(accId, node.path)));
      loadAccountFolders(accountById(accId));
    } catch (e) { setErr((e as Error).message); }
  }
  async function renameFolder(accId: number, node: FolderNode) {
    const newName = prompt(t("folder.renamePrompt"), node.label);
    if (!newName || !newName.trim() || newName.trim() === node.label) return;
    try { await api.post(`/mail/${accId}/folders/rename?name=${encodeURIComponent(node.path)}&new_name=${encodeURIComponent(newName.trim())}`); loadAccountFolders(accountById(accId)); }
    catch (e) { setErr((e as Error).message); }
  }
  async function delFolder(accId: number, path: string) {
    if (!confirm(t("folder.deleteConfirm", { name: path }))) return;
    try {
      await api.del(`/mail/${accId}/folders?name=${encodeURIComponent(path)}`);
      if (activeId === accId && folder === path) setSel({ acc: accId, folder: "INBOX" });
      loadAccountFolders(accountById(accId));
    } catch (e) { setErr((e as Error).message); }
  }
  function accountById(id: number): Account { return accounts.find((a) => a.id === id)!; }

  // --- Nachrichten ---
  function reload() {
    if (!sel) return;
    setLoading(true); setErr(""); setOpen(null); setSelected(new Set());
    api.get<MsgHeader[]>(`/mail/${sel.acc}/messages?folder=${encodeURIComponent(sel.folder)}&limit=50`)
      .then(setMessages)
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false));
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [sel?.acc, sel?.folder]);

  // Auto-Abruf: alle pollMin Minuten Zaehler aller Konten + aktive Liste auffrischen.
  useEffect(() => {
    if (!pollMin || accounts.length === 0) return;
    const id = window.setInterval(() => {
      accounts.forEach((a) => refreshCounts(a.id));
      reload();
    }, pollMin * 60000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollMin, accounts, sel?.acc, sel?.folder]);

  function patchHeader(uid: string, patch: Partial<MsgHeader>) {
    setMessages((ms) => ms.map((m) => (m.uid === uid ? { ...m, ...patch } : m)));
  }

  async function openMsg(uid: string) {
    if (activeId == null) return;
    setErr("");
    try {
      const msg = await api.get<MsgDetail>(`/mail/${activeId}/messages/${uid}?folder=${encodeURIComponent(folder)}`);
      const lastPart = folder.split(/[/.]/).pop() || folder;
      if (specialKind(lastPart) === "drafts") {
        setDraft({ to: msg.to.join(", "), cc: "", bcc: "", subject: msg.subject, body: msg.text || msg.html.replace(/<[^>]+>/g, ""), in_reply_to: "" });
        return;
      }
      setOpen(msg);
      if (!msg.seen) {
        api.post(`/mail/${activeId}/messages/${uid}/flags?folder=${encodeURIComponent(folder)}&seen=true`).catch(() => {});
        patchHeader(uid, { seen: true });
        bumpUnseen(activeId, folder, -1);
      }
    } catch (e) { setErr((e as Error).message); }
  }
  async function toggleSeen(m: MsgHeader) {
    if (activeId == null) return;
    const next = !m.seen;
    patchHeader(m.uid, { seen: next });
    bumpUnseen(activeId, folder, next ? -1 : 1);
    try { await api.post(`/mail/${activeId}/messages/${m.uid}/flags?folder=${encodeURIComponent(folder)}&seen=${next}`); }
    catch (e) { patchHeader(m.uid, { seen: m.seen }); bumpUnseen(activeId, folder, next ? 1 : -1); setErr((e as Error).message); }
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
    bumpUnseen(activeId, folder, 1);
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
      refreshCounts(activeId);
    } catch (e) { setErr((e as Error).message); }
  }
  async function del(m: MsgHeader) {
    if (activeId == null) return;
    if (!confirm(t("mail.confirmDelete"))) return;
    setErr("");
    try {
      await api.del(`/mail/${activeId}/messages/${m.uid}?folder=${encodeURIComponent(folder)}`);
      setMessages((ms) => ms.filter((x) => x.uid !== m.uid));
      if (!m.seen) bumpUnseen(activeId, folder, -1);
      if (open?.uid === m.uid) setOpen(null);
    } catch (e) { setErr((e as Error).message); }
  }

  function toggleSelect(uid: string) {
    setSelected((s) => { const n = new Set(s); if (n.has(uid)) n.delete(uid); else n.add(uid); return n; });
  }
  async function delSelected() {
    if (activeId == null || selected.size === 0) return;
    if (!confirm(t("mail.confirmDelete"))) return;
    for (const uid of [...selected]) {
      try { await api.del(`/mail/${activeId}/messages/${uid}?folder=${encodeURIComponent(folder)}`); }
      catch (e) { setErr((e as Error).message); }
    }
    setMessages((ms) => ms.filter((m) => !selected.has(m.uid)));
    if (open && selected.has(open.uid)) setOpen(null);
    setSelected(new Set());
    refreshCounts(activeId);
  }
  async function markSelectedSeen(seen: boolean) {
    if (activeId == null || selected.size === 0) return;
    for (const uid of [...selected]) {
      try { await api.post(`/mail/${activeId}/messages/${uid}/flags?folder=${encodeURIComponent(folder)}&seen=${seen}`); }
      catch { /* egal */ }
    }
    setMessages((ms) => ms.map((m) => (selected.has(m.uid) ? { ...m, seen } : m)));
    setSelected(new Set());
    refreshCounts(activeId);
  }
  // Mehrere Nachrichten verschieben (Auswahl-Leiste ODER Drag&Drop).
  async function moveUids(dest: string, uids: string[]) {
    if (activeId == null || !dest || uids.length === 0) return;
    setErr("");
    for (const uid of uids) {
      try { await api.post(`/mail/${activeId}/messages/${uid}/move?folder=${encodeURIComponent(folder)}&dest=${encodeURIComponent(dest)}`); }
      catch (e) { setErr((e as Error).message); }
    }
    setMessages((ms) => ms.filter((m) => !uids.includes(m.uid)));
    if (open && uids.includes(open.uid)) setOpen(null);
    setSelected(new Set());
    refreshCounts(activeId);
  }

  function renderNode(accId: number, node: FolderNode, depth: number): ReactNode {
    const hasKids = node.children.length > 0;
    const isOpen = expanded.has(expKey(accId, node.path));
    const label = node.special ? t(`folder.${node.special}`) : node.label;
    const icon = node.special ? SPECIAL_ICON[node.special] : "📁";
    const unseen = unseenOf(accId, node.path);
    const active = activeId === accId && node.path === folder;
    return (
      <div key={node.path}>
        <div style={{ display: "flex", alignItems: "center" }}>
          {hasKids ? (
            <button className="mail-folder-toggle" onClick={() => toggleExpand(accId, node.path)}>{isOpen ? "▼" : "▶"}</button>
          ) : (
            <span style={{ flex: "0 0 14px", width: 14 }} />
          )}
          <button
            className={`mail-folder ${active ? "active" : ""} ${dropPath === node.path ? "drop" : ""}`}
            style={{ flex: 1, minWidth: 0 }}
            onClick={() => setSel({ acc: accId, folder: node.path })}
            onContextMenu={(e) => { e.preventDefault(); setCtxMenu({ acc: accId, node, x: e.clientX, y: e.clientY }); }}
            onDragOver={(e) => { if (accId === activeId && dragUids.length) { e.preventDefault(); setDropPath(node.path); } }}
            onDragLeave={() => setDropPath((p) => (p === node.path ? null : p))}
            onDrop={(e) => { e.preventDefault(); if (accId === activeId && dragUids.length && node.path !== folder) moveUids(node.path, dragUids); setDropPath(null); setDragUids([]); }}
            title={node.path}
          >
            <span>{icon}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", flex: 1 }}>{label}</span>
            {unseen > 0 && <span className="mail-badge">{unseen}</span>}
          </button>
          {!node.special && (
            <button className="mail-folder-toggle" style={{ flex: "0 0 auto", width: "auto", padding: "0 0.3rem" }} onClick={() => delFolder(accId, node.path)} title={t("common.delete")}>🗑</button>
          )}
        </div>
        {hasKids && isOpen && node.children.map((c) => renderNode(accId, c, depth + 1))}
      </div>
    );
  }

  if (accounts.length === 0) {
    return <p className="muted">{t("mail.noAccount")}</p>;
  }

  const folderNames = (foldersByAcc[activeId ?? -1] || []).map((f) => f.name);

  const visible = messages
    .filter((m) => !search || `${m.subject} ${m.from} ${m.snippet}`.toLowerCase().includes(search.toLowerCase()))
    .filter((m) => !filter?.from || m.from.toLowerCase().includes(filter.from.toLowerCase()))
    .filter((m) => !filter?.subject || m.subject.toLowerCase().includes(filter.subject.toLowerCase()))
    .filter((m) => !filter?.unread || !m.seen)
    .filter((m) => !filter?.starred || m.flagged)
    .filter((m) => !filter?.attachments || m.has_attachments)
    .filter((m) => inDateRange(m.date, filter?.dateFrom, filter?.dateTo));
  const allSelected = visible.length > 0 && visible.every((m) => selected.has(m.uid));
  function toggleSelectAll() {
    setSelected(allSelected ? new Set() : new Set(visible.map((m) => m.uid)));
  }
  function startDrag(uid: string) {
    setDragUids(selected.has(uid) && selected.size > 0 ? [...selected] : [uid]);
  }

  return (
    <div className="mail-page">
      {err && <div className="err" style={{ marginBottom: "0.8rem" }}>{err}</div>}

      <div className="mail-layout">
        {/* Konten + Ordner (Thunderbird-Stil) */}
        <aside className="mail-folders" style={{ flex: `0 0 ${foldersW}px` }}>
          <div className="row" style={{ marginBottom: "0.55rem" }}>
            <button className="primary" style={{ flex: 1 }} onClick={() => activeId != null && setDraft(emptyDraft())}>{t("mail.newMail")}</button>
          </div>

          {accounts.map((a) => {
            const collapsed = collapsedAcc.has(a.id);
            const tree = treesByAcc[a.id] || [];
            const roll = rollupUnseen(a.id);
            return (
              <div className="mail-acc" key={a.id}>
                <div className="mail-acc-head" onClick={() => toggleAccount(a.id)}>
                  <button className="mail-folder-toggle">{collapsed ? "▶" : "▼"}</button>
                  <span className="mail-acc-name" title={a.email}>{a.label || a.email}</span>
                  {collapsed && roll > 0 && <span className="mail-badge">{roll}</span>}
                  <button
                    className="mail-folder-toggle"
                    style={{ width: "auto", padding: "0 0.25rem" }}
                    onClick={(e) => { e.stopPropagation(); refreshAccount(a.id); }}
                    title="↻"
                  >↻</button>
                  <button
                    className="mail-folder-toggle"
                    style={{ width: "auto", padding: "0 0.25rem" }}
                    onClick={(e) => { e.stopPropagation(); newFolder(a.id); }}
                    title={t("folder.new")}
                  >＋</button>
                </div>
                {!collapsed && (tree.length ? tree.map((n) => renderNode(a.id, n, 0)) : <div className="muted" style={{ fontSize: "0.78rem", padding: "0.2rem 0.6rem" }}>…</div>)}
              </div>
            );
          })}
        </aside>

        <div className="resize-handle" onMouseDown={startResizeFolders} title={t("mail.resizeHint")} />

        {/* Listen-Spalte */}
        <div className="mail-listcol" style={{ flex: `0 0 ${listW}px` }}>
          {loading && <p className="muted">{t("mail.loadingMessages")}</p>}
          {selected.size > 0 && (
            <div className="row" style={{ marginBottom: "0.5rem", padding: "0.4rem 0.6rem", background: "var(--self-bg-2)", borderRadius: "6px", flexWrap: "wrap" }}>
              <span className="label">{t("mail.selected", { n: selected.size })}</span>
              <span className="grow" />
              <button className="ghost" onClick={() => markSelectedSeen(true)}>{t("mail.markRead")}</button>
              <button className="ghost" onClick={() => markSelectedSeen(false)}>{t("mail.markUnread")}</button>
              {folderNames.length > 1 && (
                <select value="" title={t("mail.moveTo")} onChange={(e) => { moveUids(e.target.value, [...selected]); e.currentTarget.value = ""; }} style={{ fontSize: "0.82rem", maxWidth: 150 }}>
                  <option value="">📁 {t("mail.moveTo")}</option>
                  {folderNames.filter((f) => f !== folder).map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              )}
              <button className="ghost" onClick={delSelected}>🗑 {t("mail.delete")}</button>
            </div>
          )}
          {visible.length > 0 && (
            <label className="row" style={{ padding: "0.1rem 0.6rem 0.4rem", gap: "0.5rem", cursor: "pointer" }}>
              <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} style={{ width: "auto" }} />
              <span className="muted" style={{ fontSize: "0.78rem" }}>{t("mail.selectAll")}</span>
            </label>
          )}
          <div className="mail-list">
            {visible.map((m) => (
              <div className={`mail-row ${m.seen ? "" : "unseen"}`} key={m.uid}
                draggable onDragStart={() => startDrag(m.uid)} onDragEnd={() => setDragUids([])}
                style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", borderColor: open?.uid === m.uid ? "var(--self-teal)" : undefined }}>
                <input type="checkbox" checked={selected.has(m.uid)} onChange={() => toggleSelect(m.uid)} style={{ flex: "0 0 auto", width: "auto", marginTop: "0.3rem" }} />
                <button className="ghost" style={{ padding: "0 0.1rem", flex: "0 0 auto", color: m.flagged ? "var(--self-cyan, #00e5c8)" : undefined }} onClick={() => toggleFlag(m)} title={t("mail.flag")}>
                  {m.flagged ? "★" : "☆"}
                </button>
                <div className="grow" style={{ cursor: "pointer", overflow: "hidden", minWidth: 0 }} onClick={() => openMsg(m.uid)}>
                  <div style={{ display: "flex", gap: "0.5rem", alignItems: "baseline" }}>
                    <span style={{ flex: 1, minWidth: 0, fontWeight: m.seen ? 400 : 700, color: "var(--self-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "0.9rem" }}>{m.from}</span>
                    <span className="muted" style={{ fontSize: "0.72rem", whiteSpace: "nowrap", flex: "0 0 auto" }}>{m.date?.slice(0, 16)}</span>
                  </div>
                  <div className="mail-subj" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.subject || t("mail.noSubject")}</span>
                    {m.has_attachments && <span style={{ flex: "0 0 auto", fontSize: "0.8rem" }}>📎</span>}
                  </div>
                  {m.snippet && <div className="muted" style={{ fontSize: "0.78rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.snippet}</div>}
                </div>
                <button className="ghost" style={{ padding: "0 0.2rem", flex: "0 0 auto" }} onClick={() => toggleSeen(m)} title={m.seen ? t("mail.markUnread") : t("mail.markRead")}>{m.seen ? "○" : "●"}</button>
                <button className="ghost" style={{ padding: "0 0.2rem", flex: "0 0 auto" }} onClick={() => del(m)} title={t("mail.delete")}>🗑</button>
              </div>
            ))}
            {!loading && messages.length === 0 && <p className="muted">{t("mail.noMessages")}</p>}
          </div>
        </div>

        <div className="resize-handle" onMouseDown={startResize} title={t("mail.resizeHint")} />

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
              {folderNames.length > 1 && (
                <select value="" title={t("mail.moveTo")} onChange={(e) => { moveMsg(open.uid, e.target.value); e.currentTarget.value = ""; }} style={{ fontSize: "0.82rem", maxWidth: 160 }}>
                  <option value="">📁 {t("mail.moveTo")}</option>
                  {folderNames.filter((f) => f !== folder).map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              )}
              <button className="ghost" onClick={() => del(open)} title={t("mail.delete")}>🗑</button>
              <span className="grow" />
              <button className="ghost" onClick={() => setOpen(null)} title={t("mail.back")}>✕</button>
            </div>
            <h2 style={{ marginBottom: "0.2rem", fontSize: "1.2rem" }}>{open.subject || t("mail.noSubject")}</h2>
            <div className="mail-from">{open.from} · {open.date}</div>
            <hr style={{ borderColor: "var(--self-line)", margin: "0.9rem 0" }} />
            {open.text ? (
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{open.text}</div>
            ) : open.html ? (
              <iframe title="mail-body" sandbox="" srcDoc={open.html} style={{ width: "100%", height: "62vh", border: "none", background: "#fff", borderRadius: "6px" }} />
            ) : (
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{t("mail.emptyBody")}</div>
            )}
            {open.attachments?.length > 0 && (
              <div style={{ marginTop: "1.2rem", borderTop: "1px solid var(--self-line)", paddingTop: "0.8rem" }}>
                <div className="label" style={{ marginBottom: "0.5rem" }}>📎 {t("mail.attachments")}</div>
                <div className="row" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
                  {open.attachments.map((att) => (
                    <button key={att.index} className="ghost" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}
                      onClick={() => download(`/mail/${activeId}/messages/${open.uid}/attachments/${att.index}?folder=${encodeURIComponent(folder)}`).catch((e) => setErr((e as Error).message))}>
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

      {ctxMenu && (
        <>
          <div className="menu-backdrop" onClick={() => setCtxMenu(null)} onContextMenu={(e) => { e.preventDefault(); setCtxMenu(null); }} />
          <div className="ctx-menu" style={{ left: ctxMenu.x, top: ctxMenu.y }}>
            <button onClick={() => { newSubfolder(ctxMenu.acc, ctxMenu.node); setCtxMenu(null); }}>📁 {t("folder.newSub")}</button>
            {!ctxMenu.node.special && <button onClick={() => { renameFolder(ctxMenu.acc, ctxMenu.node); setCtxMenu(null); }}>✏ {t("folder.rename")}</button>}
            {!ctxMenu.node.special && <button onClick={() => { delFolder(ctxMenu.acc, ctxMenu.node.path); setCtxMenu(null); }}>🗑 {t("common.delete")}</button>}
          </div>
        </>
      )}

      {draft && activeId != null && (
        <Compose accountId={activeId} draft={draft} onClose={() => { setDraft(null); }} />
      )}
    </div>
  );
}

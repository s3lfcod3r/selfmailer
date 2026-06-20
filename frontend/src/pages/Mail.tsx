import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, download, type Account, type MsgHeader, type MsgDetail, type TransferResult } from "../lib/api";
import { useLang } from "../lib/i18n";
import { buildFolderTree, specialKind, SPECIAL_ICON, type FolderNode } from "../lib/folders";
import { Compose, emptyDraft, replyDraft, forwardDraft, type Draft } from "../components/Compose";

type FolderCount = { name: string; unseen: number; total: number };
type Sel = { acc: number; folder: string };

const PAGE_SIZE = 50;  // Mails pro Seite

// Sichtbare Seitenzahlen mit Auslassung: 1 … (cur-1) cur (cur+1) … last.
function pageNumbers(cur: number, total: number): (number | "…")[] {
  if (total <= 1) return [1];
  const out: (number | "…")[] = [1];
  const from = Math.max(2, cur - 1);
  const to = Math.min(total - 1, cur + 1);
  if (from > 2) out.push("…");
  for (let p = from; p <= to; p++) out.push(p);
  if (to < total - 1) out.push("…");
  out.push(total);
  return out;
}

// Absender "Name <mail@x.de>" in Anzeigename + Adresse zerlegen.
function parseAddr(s: string): { name: string; email: string } {
  const m = /^\s*"?(.*?)"?\s*<([^>]+)>\s*$/.exec(s || "");
  if (m && m[2]) return { name: (m[1] || m[2]).trim(), email: m[2].trim() };
  return { name: s || "", email: s || "" };
}
// Server-Datumsstring hübsch lokalisiert; faellt bei Parse-Fehler auf Rohtext zurueck.
function prettyDate(s: string): string {
  const d = new Date(s);
  return isNaN(d.getTime()) ? s : d.toLocaleString();
}
// Kompaktes Datum MIT Uhrzeit fuer die Listenzeile (z. B. "20. Jun 26, 17:24").
function listDate(s: string): string {
  const d = new Date(s);
  if (isNaN(d.getTime())) return (s || "").slice(0, 16);
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

// CSP, die im Mail-iframe ALLE externen Ladevorgaenge (Bilder/Schriften/Medien)
// blockiert — nur eingebettete data:/cid:-Bilder und Inline-Styles sind erlaubt.
// So laden keine Tracking-Pixel; Skripte sind ohnehin per sandbox="" geblockt.
const _CSP_BLOCK =
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: cid:; style-src 'unsafe-inline'; font-src data:; media-src data:;">`;
function hasRemoteContent(html: string): boolean {
  return /(?:src|background)\s*=\s*["']?\s*https?:/i.test(html) || /url\(\s*['"]?\s*https?:/i.test(html);
}
function buildSrcDoc(html: string, block: boolean): string {
  return `<!DOCTYPE html><meta charset="utf-8">${block ? _CSP_BLOCK : ""}<base target="_blank">${html}`;
}

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

export function Mail({ search = "", filter, pollMin = 5, blockImages = true }: { search?: string; filter?: MailFilter; pollMin?: number; blockImages?: boolean }) {
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
  const [loadingMore, setLoadingMore] = useState(false);
  const [page, setPage] = useState(1);
  // "Alle im Ordner" (ueber alle Seiten) aktiv? Dann enthaelt `selected` alle UIDs.
  const [selectAllFolder, setSelectAllFolder] = useState(false);
  const [syncing, setSyncing] = useState(false);
  // Lese-Kopf: Mehr-Menü (⋯) und ausklappbare Detailzeilen (Von/An/Datum/Betreff).
  const [readMenu, setReadMenu] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  // Absender als Kontakt gespeichert? (kurzes Erfolgs-Feedback im Lesekopf)
  const [contactSaved, setContactSaved] = useState(false);
  // Pro geoeffneter Mail: hat der Nutzer externe Bilder freigegeben?
  const [showImages, setShowImages] = useState(false);
  const [err, setErr] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dragUids, setDragUids] = useState<string[]>([]);
  const [dropPath, setDropPath] = useState<string | null>(null);
  // Reihenfolge der Konten (per Drag&Drop, lokal gespeichert).
  const [accOrder, setAccOrder] = useState<number[]>(() => {
    try { const v = JSON.parse(localStorage.getItem("selfmailer.accOrder") || "[]"); return Array.isArray(v) ? v : []; }
    catch { return []; }
  });
  const [dragAcc, setDragAcc] = useState<number | null>(null);
  const [ctxMenu, setCtxMenu] = useState<{ acc: number; node: FolderNode; x: number; y: number } | null>(null);
  const [listW, setListW] = useState<number>(() => {
    const v = Number(localStorage.getItem("selfmailer.listW"));
    return v >= 260 && v <= 760 ? v : 380;
  });
  const [foldersW, setFoldersW] = useState<number>(() => {
    const v = Number(localStorage.getItem("selfmailer.foldersW"));
    return v >= 160 && v <= 420 ? v : 210;
  });
  // Ausgeblendete ("ignorierte") Ordner je Konto: { [accId]: pfad[] } in localStorage.
  // Rein optisch — der Ordner bleibt am Server, wird nur in der Sidebar versteckt.
  const [hiddenByAcc, setHiddenByAcc] = useState<Record<number, string[]>>(() => {
    try { const v = JSON.parse(localStorage.getItem("selfmailer.hiddenFolders") || "{}"); return v && typeof v === "object" ? v : {}; }
    catch { return {}; }
  });
  // Welche Konten zeigen ihre ausgeblendeten Ordner gerade an (zum Einblenden)?
  const [revealHidden, setRevealHidden] = useState<Set<number>>(new Set());
  // Mobile/Tablet: nur eine Spalte zur Zeit (Drill-down Ordner → Liste → Lesen).
  // Auf Desktop (>900px) ignoriert das CSS das Attribut und zeigt alle 3 Spalten.
  const [mobilePane, setMobilePane] = useState<"folders" | "list" | "read">("list");
  // Konto-Transfer: ausgewaehlte Mails ODER ganzer Ordner (uids=null) in ein
  // ANDERES Konto kopieren/verschieben.
  const [transfer, setTransfer] = useState<{ sourceAcc: number; sourceFolder: string; uids: string[] | null } | null>(null);
  const [xferAcc, setXferAcc] = useState<number>(0);
  const [xferFolder, setXferFolder] = useState<string>("");
  const [xferBusy, setXferBusy] = useState(false);
  // Ordner in anderen Ordner verschieben (Hierarchie im selben Konto umsortieren).
  const [folderMove, setFolderMove] = useState<{ acc: number; path: string } | null>(null);
  const [fmParent, setFmParent] = useState<string>("");

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
  // Suche aktiv? Dann den ganzen Ordner laden und Treffer in EINER Liste zeigen
  // (kein Seiten-Pager, der sich auf alle Ordner-Mails bezieht).
  const searchActive = (search ?? "").trim().length > 0;

  // --- Konten + Ordner (mit Zaehlern) laden ---
  async function loadAccountFolders(a: Account) {
    // Cache-first: ist die Ordnerliste schon gecacht, erscheint die Seitenleiste
    // SOFORT (kein IMAP). Danach wird im Hintergrund still live aufgefrischt.
    let hadCache = false;
    try {
      const cached = await api.get<FolderCount[]>(`/mail/${a.id}/folders/counts`);
      if (cached.length) { hadCache = true; setFoldersByAcc((m) => ({ ...m, [a.id]: cached })); }
    } catch { /* egal — unten Live/Fallback */ }

    if (!hadCache) {
      // Erstmalig fuer dieses Konto: Standard-Ordner sicherstellen, dann live
      // holen (fuellt den Cache). Nur hier blockierend — bei F5 nie mehr.
      try { await api.post(`/mail/${a.id}/folders/defaults`); } catch { /* egal */ }
      try {
        const fc = await api.get<FolderCount[]>(`/mail/${a.id}/folders/counts?live=1`);
        setFoldersByAcc((m) => ({ ...m, [a.id]: fc.length ? fc : [{ name: "INBOX", unseen: 0, total: 0 }] }));
      } catch {
        setFoldersByAcc((m) => ({ ...m, [a.id]: [{ name: "INBOX", unseen: 0, total: 0 }] }));
      }
      return;
    }

    // Folge-Laden (F5): Live-Abgleich nur im Hintergrund, ohne zu blockieren.
    api.get<FolderCount[]>(`/mail/${a.id}/folders/counts?live=1`)
      .then((fc) => { if (fc.length) setFoldersByAcc((m) => ({ ...m, [a.id]: fc })); })
      .catch(() => { /* Cache bleibt stehen */ });
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

  // Konten in gespeicherter Reihenfolge; unbekannte (neue) ans Ende.
  const orderedAccounts = useMemo(() => {
    const pos = new Map(accOrder.map((id, i) => [id, i]));
    return [...accounts].sort((a, b) => (pos.get(a.id) ?? 1e9) - (pos.get(b.id) ?? 1e9));
  }, [accounts, accOrder]);

  function reorderAccounts(dragId: number, dropId: number) {
    if (dragId === dropId) return;
    const ids = orderedAccounts.map((a) => a.id);
    const from = ids.indexOf(dragId);
    const to = ids.indexOf(dropId);
    if (from < 0 || to < 0) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    setAccOrder(ids);
    localStorage.setItem("selfmailer.accOrder", JSON.stringify(ids));
  }

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
    // Auffrischen heisst hier: echte, frische Zaehler vom Server (live) holen —
    // und damit zugleich den Cache aktualisieren.
    api.get<FolderCount[]>(`/mail/${accId}/folders/counts?live=1`)
      .then((fc) => { if (fc.length) setFoldersByAcc((m) => ({ ...m, [accId]: fc })); }).catch(() => {});
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
    // Konto-＋ legt einen Ordner auf der OBERSTEN Ebene an (neben Posteingang),
    // nicht mehr zwingend unter INBOX. Unterordner gehen weiter per Rechtsklick
    // auf einen Ordner → „Unterordner erstellen".
    const name = prompt(t("folder.newTopPrompt"));
    if (!name || !name.trim()) return;
    try { await api.post(`/mail/${accId}/folders?name=${encodeURIComponent(name.trim())}`); loadAccountFolders(accountById(accId)); }
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
  // Aktuelle Auswahl als Ref, damit ein verspaeteter Hintergrund-Sync nur dann
  // die Liste aktualisiert, wenn der Nutzer noch im selben Ordner ist.
  const selRef = useRef(sel);
  useEffect(() => { selRef.current = sel; }, [sel]);
  // Aktuelle Seite als Ref, damit der periodische Auto-Sync genau die gerade
  // sichtbare Seite auffrischt (ohne den Effekt bei jedem Seitenwechsel neu zu setzen).
  const pageRef = useRef(1);
  useEffect(() => { pageRef.current = page; }, [page]);

  // Holt genau eine Seite (offset = (p-1)*PAGE_SIZE). Cache-first im Backend.
  function fetchPage(acc: number, fol: string, p: number) {
    return api.get<MsgHeader[]>(`/mail/${acc}/messages?folder=${encodeURIComponent(fol)}&limit=${PAGE_SIZE}&offset=${(p - 1) * PAGE_SIZE}`);
  }
  // Ordnerwechsel/Neuladen: immer auf Seite 1, Auswahl zuruecksetzen.
  function reload() {
    if (!sel) return;
    const acc = sel.acc, fol = sel.folder;
    setLoading(true); setErr(""); setOpen(null); setSelected(new Set()); setSelectAllFolder(false); setPage(1);
    fetchPage(acc, fol, 1)
      .then((ms) => setMessages(ms))
      .catch((e) => setErr((e as Error).message))
      .finally(() => { setLoading(false); bgSync(acc, fol, 1); });
  }
  // Hintergrund-Sync: neue Mails/Flags nachziehen, dann die Seite p still auffrischen.
  function bgSync(acc: number, fol: string, p: number = 1) {
    setSyncing(true);
    api.post(`/mail/${acc}/sync?folder=${encodeURIComponent(fol)}`)
      .then(() => fetchPage(acc, fol, p))
      .then((ms) => {
        if (selRef.current?.acc === acc && selRef.current?.folder === fol) setMessages(ms);
        refreshCounts(acc);
      })
      .catch(() => { /* Sync ist best-effort */ })
      .finally(() => setSyncing(false));
  }
  // Zu Seite p springen: ersetzt die Liste. Auswahl bleibt erhalten (seitenuebergreifend).
  function goPage(p: number) {
    if (!sel) return;
    const clamped = Math.max(1, Math.min(totalPages, p));
    if (clamped === page) return;
    setPage(clamped); setOpen(null); setLoadingMore(true);
    fetchPage(sel.acc, sel.folder, clamped)
      .then((ms) => setMessages(ms))
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoadingMore(false));
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  // Suche: ganzen Ordner laden (bis Cache-Tiefe), damit Treffer ueber alle Seiten
  // gefunden und in einer Liste angezeigt werden.
  function loadAllForSearch() {
    if (!sel) return;
    const acc = sel.acc, fol = sel.folder;
    setLoading(true); setErr(""); setOpen(null); setSelected(new Set()); setSelectAllFolder(false);
    api.get<MsgHeader[]>(`/mail/${acc}/messages?folder=${encodeURIComponent(fol)}&limit=1000`)
      .then((ms) => setMessages(ms))
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false));
  }
  // Bei Ordnerwechsel ODER Wechsel Suche an/aus passend laden.
  useEffect(() => {
    if (!sel) return;
    if (searchActive) loadAllForSearch(); else reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel?.acc, sel?.folder, searchActive]);

  // Mobile: schließt sich die Lese-Ansicht (Löschen/Verschieben/✕), zurück zur Liste.
  useEffect(() => {
    if (!open && mobilePane === "read") setMobilePane("list");
    setContactSaved(false);  // bei jeder geoeffneten Mail das Kontakt-Feedback zuruecksetzen
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Auto-Abruf: alle pollMin Minuten Zaehler aller Konten + aktive Liste auffrischen.
  useEffect(() => {
    if (!pollMin || accounts.length === 0) return;
    const id = window.setInterval(() => {
      accounts.forEach((a) => refreshCounts(a.id));
      // Still aktualisieren: nur Delta-Sync + leise Liste — KEIN reload (kein
      // Spinner, offene Mail bleibt offen, bereits geladene Seiten bleiben).
      if (selRef.current) bgSync(selRef.current.acc, selRef.current.folder, pageRef.current);
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
      setMobilePane("read");
      setDetailsOpen(false);
      setReadMenu(false);
      setShowImages(false);
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
    const ids = selected, wasAll = selectAllFolder;
    for (const uid of [...ids]) {
      try { await api.del(`/mail/${activeId}/messages/${uid}?folder=${encodeURIComponent(folder)}`); }
      catch (e) { setErr((e as Error).message); }
    }
    if (open && ids.has(open.uid)) setOpen(null);
    setSelected(new Set()); setSelectAllFolder(false);
    refreshCounts(activeId);
    // Ganzer Ordner: viele Seiten betroffen → frisch laden; sonst optimistisch filtern.
    if (wasAll) reload(); else setMessages((ms) => ms.filter((m) => !ids.has(m.uid)));
  }
  async function markSelectedSeen(seen: boolean) {
    if (activeId == null || selected.size === 0) return;
    const ids = selected;
    for (const uid of [...ids]) {
      try { await api.post(`/mail/${activeId}/messages/${uid}/flags?folder=${encodeURIComponent(folder)}&seen=${seen}`); }
      catch { /* egal */ }
    }
    setMessages((ms) => ms.map((m) => (ids.has(m.uid) ? { ...m, seen } : m)));
    setSelected(new Set()); setSelectAllFolder(false);
    refreshCounts(activeId);
  }
  // Mehrere Nachrichten verschieben (Auswahl-Leiste ODER Drag&Drop).
  async function moveUids(dest: string, uids: string[]) {
    if (activeId == null || !dest || uids.length === 0) return;
    setErr("");
    const wasAll = selectAllFolder;
    for (const uid of uids) {
      try { await api.post(`/mail/${activeId}/messages/${uid}/move?folder=${encodeURIComponent(folder)}&dest=${encodeURIComponent(dest)}`); }
      catch (e) { setErr((e as Error).message); }
    }
    if (open && uids.includes(open.uid)) setOpen(null);
    setSelected(new Set()); setSelectAllFolder(false);
    refreshCounts(activeId);
    if (wasAll) reload(); else setMessages((ms) => ms.filter((m) => !uids.includes(m.uid)));
  }

  function openTransfer(sourceAcc: number, sourceFolder: string, uids: string[] | null) {
    setTransfer({ sourceAcc, sourceFolder, uids });
    const first = accounts.find((a) => a.id !== sourceAcc);
    setXferAcc(first?.id ?? 0);
    // Ganzer Ordner: Zielordner mit dem Quell-Namen vorbelegen (wird bei Bedarf
    // automatisch angelegt). Einzelmails: leer lassen.
    setXferFolder(uids === null ? (sourceFolder.split(/[/.]/).pop() || "") : "");
    setCtxMenu(null);
  }
  async function submitTransfer(move: boolean) {
    if (!transfer || !xferAcc || !xferFolder) return;
    setXferBusy(true); setErr("");
    try {
      const r = await api.post<TransferResult>(`/mail/${transfer.sourceAcc}/transfer`, {
        source_folder: transfer.sourceFolder, uids: transfer.uids,
        dest_account_id: xferAcc, dest_folder: xferFolder, move,
      });
      refreshCounts(transfer.sourceAcc); refreshCounts(xferAcc);
      if (move && transfer.sourceAcc === activeId) { setSelected(new Set()); setOpen(null); reload(); }
      setTransfer(null);
      if (r.errors.length) setErr(r.errors.join("; "));
    } catch (e) { setErr((e as Error).message); }
    finally { setXferBusy(false); }
  }

  async function submitFolderMove() {
    if (!folderMove) return;
    setErr("");
    try {
      await api.post(`/mail/${folderMove.acc}/folders/move?name=${encodeURIComponent(folderMove.path)}&parent=${encodeURIComponent(fmParent)}`);
      const acc = folderMove.acc;
      setFolderMove(null);
      loadAccountFolders(accountById(acc));
    } catch (e) { setErr((e as Error).message); }
  }

  // Absender der offenen Mail ins Adressbuch uebernehmen (Name + Adresse).
  async function saveSenderAsContact() {
    if (!open) return;
    const { name, email } = parseAddr(open.from);
    if (!email) return;
    try {
      await api.post("/contacts", { name: name || email, email });
      setContactSaved(true);
    } catch (e) { setErr((e as Error).message); }
  }

  function persistHidden(next: Record<number, string[]>) {
    setHiddenByAcc(next);
    localStorage.setItem("selfmailer.hiddenFolders", JSON.stringify(next));
  }
  function hideFolder(accId: number, path: string) {
    const cur = hiddenByAcc[accId] || [];
    if (cur.includes(path)) return;
    persistHidden({ ...hiddenByAcc, [accId]: [...cur, path] });
    // War der ausgeblendete Ordner gerade aktiv → zurueck auf den Posteingang.
    if (activeId === accId && folder === path) setSel({ acc: accId, folder: "INBOX" });
  }
  function unhideFolder(accId: number, path: string) {
    const cur = hiddenByAcc[accId] || [];
    persistHidden({ ...hiddenByAcc, [accId]: cur.filter((p) => p !== path) });
  }
  function toggleReveal(accId: number) {
    setRevealHidden((s) => { const n = new Set(s); if (n.has(accId)) n.delete(accId); else n.add(accId); return n; });
  }

  function renderNode(accId: number, node: FolderNode, depth: number): ReactNode {
    if ((hiddenByAcc[accId] || []).includes(node.path)) return null;  // ausgeblendet
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
            onClick={() => { setSel({ acc: accId, folder: node.path }); setMobilePane("list"); }}
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
  // Spam-Ordner des aktiven Kontos (für den Spam-Button) per Sonderordner-Heuristik.
  const spamFolder = folderNames.find((f) => specialKind(f.split(/[/.]/).pop() || f) === "spam");

  const visible = messages
    .filter((m) => !search || `${m.subject} ${m.from} ${m.snippet}`.toLowerCase().includes(search.toLowerCase()))
    .filter((m) => !filter?.from || m.from.toLowerCase().includes(filter.from.toLowerCase()))
    .filter((m) => !filter?.subject || m.subject.toLowerCase().includes(filter.subject.toLowerCase()))
    .filter((m) => !filter?.unread || !m.seen)
    .filter((m) => !filter?.starred || m.flagged)
    .filter((m) => !filter?.attachments || m.has_attachments)
    .filter((m) => inDateRange(m.date, filter?.dateFrom, filter?.dateTo));
  // Gesamtzahl/Seitenzahl des aktiven Ordners aus den Zaehlern (IMAP STATUS).
  const folderTotal = (foldersByAcc[activeId ?? -1] || []).find((f) => f.name === folder)?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(folderTotal / PAGE_SIZE));
  const allSelected = visible.length > 0 && visible.every((m) => selected.has(m.uid));
  function toggleSelectAll() {
    setSelectAllFolder(false);
    setSelected(allSelected ? new Set() : new Set(visible.map((m) => m.uid)));
  }
  // "Alle im Ordner": holt alle UIDs (auch anderer Seiten) und markiert sie.
  async function selectWholeFolder() {
    if (!sel) return;
    try {
      const uids = await api.get<string[]>(`/mail/${sel.acc}/folder-uids?folder=${encodeURIComponent(sel.folder)}`);
      setSelected(new Set(uids));
      setSelectAllFolder(true);
    } catch (e) { setErr((e as Error).message); }
  }
  function startDrag(uid: string) {
    setDragUids(selected.has(uid) && selected.size > 0 ? [...selected] : [uid]);
  }

  return (
    <div className="mail-page">
      {err && <div className="err" style={{ marginBottom: "0.8rem" }}>{err}</div>}

      <div className="mail-layout" data-pane={mobilePane}>
        {/* Konten + Ordner (Thunderbird-Stil) */}
        <aside className="mail-folders" style={{ flex: `0 0 ${foldersW}px` }}>
          <div className="row" style={{ marginBottom: "0.55rem" }}>
            <button className="primary" style={{ flex: 1 }} onClick={() => activeId != null && setDraft(emptyDraft())}>{t("mail.newMail")}</button>
          </div>

          {orderedAccounts.map((a) => {
            const collapsed = collapsedAcc.has(a.id);
            const tree = treesByAcc[a.id] || [];
            const roll = rollupUnseen(a.id);
            const hid = hiddenByAcc[a.id] || [];
            return (
              <div className={`mail-acc ${dragAcc === a.id ? "dragging" : ""}`} key={a.id}>
                <div
                  className="mail-acc-head"
                  draggable
                  onClick={() => toggleAccount(a.id)}
                  onDragStart={() => setDragAcc(a.id)}
                  onDragEnd={() => setDragAcc(null)}
                  onDragOver={(e) => { if (dragAcc != null && dragAcc !== a.id) e.preventDefault(); }}
                  onDrop={(e) => { e.preventDefault(); if (dragAcc != null) reorderAccounts(dragAcc, a.id); setDragAcc(null); }}
                  title={t("mail.accDragHint")}
                >
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
                    title={t("folder.newTop")}
                  >＋</button>
                </div>
                {!collapsed && (tree.length ? tree.map((n) => renderNode(a.id, n, 0)) : <div className="muted" style={{ fontSize: "0.78rem", padding: "0.2rem 0.6rem" }}>…</div>)}
                {!collapsed && hid.length > 0 && (
                  <div className="mail-hidden">
                    <button className="mail-hidden-toggle" onClick={() => toggleReveal(a.id)}>
                      🙈 {t("folder.hiddenCount", { n: hid.length })} {revealHidden.has(a.id) ? "▲" : "▾"}
                    </button>
                    {revealHidden.has(a.id) && hid.map((p) => (
                      <div className="mail-hidden-row" key={p}>
                        <span className="mail-hidden-name" title={p}>{p.split(/[/.]/).pop() || p}</span>
                        <button className="mail-folder-toggle" style={{ width: "auto", padding: "0 0.3rem" }} onClick={() => unhideFolder(a.id, p)} title={t("folder.unhide")}>👁</button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </aside>

        <div className="resize-handle" onMouseDown={startResizeFolders} title={t("mail.resizeHint")} />

        {/* Listen-Spalte */}
        <div className="mail-listcol" style={{ flex: `0 0 ${listW}px`, position: "relative" }}>
          {/* Nur Mobile/Tablet sichtbar (CSS .mail-mobilebar): zurück zu den Postfächern. */}
          <div className="mail-mobilebar">
            <button className="ghost" onClick={() => setMobilePane("folders")}>☰ {t("mail.mailbox")}</button>
            <span className="muted" style={{ fontSize: "0.82rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {activeId != null ? (accountById(activeId)?.label || accountById(activeId)?.email) : ""}
            </span>
          </div>
          {loading && <p className="muted">{t("mail.loadingMessages")}</p>}
          {!loading && syncing && (
            <div
              className="muted"
              style={{
                position: "absolute", top: 4, right: 8, zIndex: 5,
                fontSize: "0.68rem", padding: "0.1rem 0.45rem", borderRadius: 999,
                background: "var(--surface-2, rgba(20,24,33,0.85))",
                backdropFilter: "blur(2px)", pointerEvents: "none",
              }}
            >⟳ {t("mail.syncing")}</div>
          )}
          {!searchActive && totalPages > 1 && (
            <div className="mail-pager">
              <button className="pgbtn" disabled={page <= 1 || loadingMore} onClick={() => goPage(1)} title={t("mail.firstPage")}>«</button>
              <button className="pgbtn" disabled={page <= 1 || loadingMore} onClick={() => goPage(page - 1)} title={t("mail.prevPage")}>‹</button>
              {pageNumbers(page, totalPages).map((p, i) =>
                p === "…"
                  ? <span key={`e${i}`} className="pg-gap">…</span>
                  : <button key={p} className={`pgbtn ${p === page ? "active" : ""}`} disabled={loadingMore} onClick={() => goPage(p)}>{p}</button>,
              )}
              <button className="pgbtn" disabled={page >= totalPages || loadingMore} onClick={() => goPage(page + 1)} title={t("mail.nextPage")}>›</button>
              <button className="pgbtn" disabled={page >= totalPages || loadingMore} onClick={() => goPage(totalPages)} title={t("mail.lastPage")}>»</button>
              <span className="pg-info">{t("mail.pageOf", { p: page, n: totalPages })}</span>
            </div>
          )}
          {selected.size > 0 && (
            <div className="bulk-bar">
              <div className="bulk-count">
                <span className="bulk-num">{selected.size}</span>
                <span>{t("mail.selectedShort")}</span>
                <button className="bulk-clear" onClick={() => setSelected(new Set())} title={t("mail.clearSelection")}>✕</button>
              </div>
              <div className="bulk-actions">
                <button className="bulk-btn" onClick={() => markSelectedSeen(true)}>✓ {t("mail.markRead")}</button>
                <button className="bulk-btn" onClick={() => markSelectedSeen(false)}>● {t("mail.markUnread")}</button>
                {folderNames.length > 1 && (
                  <select className="bulk-btn" value="" title={t("mail.moveTo")} onChange={(e) => { if (e.target.value) moveUids(e.target.value, [...selected]); }}>
                    <option value="">📁 {t("mail.moveTo")}</option>
                    {folderNames.filter((f) => f !== folder).map((f) => <option key={f} value={f}>{f}</option>)}
                  </select>
                )}
                {spamFolder && folder !== spamFolder && (
                  <button className="bulk-btn" onClick={() => moveUids(spamFolder, [...selected])}>🚫 {t("mail.spam")}</button>
                )}
                <button className="bulk-btn" onClick={() => activeId != null && openTransfer(activeId, folder, [...selected])}>↪ {t("xfer.toAccount")}</button>
                <button className="bulk-btn bulk-del" onClick={delSelected}>🗑 {t("mail.delete")}</button>
              </div>
            </div>
          )}
          {visible.length > 0 && (
            <div className="mail-selbar">
              <label className="row" style={{ gap: "0.5rem", cursor: "pointer", margin: 0 }}>
                <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} style={{ width: "auto" }} />
                <span className="muted" style={{ fontSize: "0.78rem" }}>{t("mail.selectAll")}</span>
              </label>
              {/* Ordnerweites "Alle auswaehlen" NUR ohne Suche — bei Suche soll
                  "Alle auswaehlen" ausschliesslich die sichtbaren Treffer markieren. */}
              {!searchActive && (selectAllFolder ? (
                <span className="sel-all-note">
                  {t("mail.allFolderSelected", { n: folderTotal })}
                  <button className="link-btn" onClick={() => { setSelected(new Set()); setSelectAllFolder(false); }}>{t("mail.clearSelection")}</button>
                </span>
              ) : (
                allSelected && folderTotal > visible.length && (
                  <button className="link-btn" onClick={selectWholeFolder}>{t("mail.selectAllFolder", { n: folderTotal })}</button>
                )
              ))}
            </div>
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
                    <span className="muted" style={{ fontSize: "0.72rem", whiteSpace: "nowrap", flex: "0 0 auto" }}>{listDate(m.date)}</span>
                  </div>
                  <div className="mail-subj" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.subject || t("mail.noSubject")}</span>
                    {m.has_attachments && <span style={{ flex: "0 0 auto", fontSize: "0.8rem" }}>📎</span>}
                  </div>
                  {m.snippet && <div className="muted" style={{ fontSize: "0.78rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.snippet}</div>}
                </div>
                <button className="ghost" style={{ padding: "0 0.2rem", flex: "0 0 auto", color: m.seen ? undefined : "var(--self-unread)", fontSize: m.seen ? undefined : "1.1rem", lineHeight: 1 }} onClick={() => toggleSeen(m)} title={m.seen ? t("mail.markUnread") : t("mail.markRead")}>{m.seen ? "○" : "●"}</button>
                <button className="ghost" style={{ padding: "0 0.2rem", flex: "0 0 auto" }} onClick={() => del(m)} title={t("mail.delete")}>🗑</button>
              </div>
            ))}
            {!loading && messages.length === 0 && <p className="muted">{t("mail.noMessages")}</p>}
            {!searchActive && totalPages > 1 && !loading && (
              <div className="mail-pager mail-pager-bottom">
                <button className="pgbtn" disabled={page <= 1 || loadingMore} onClick={() => goPage(page - 1)} title={t("mail.prevPage")}>‹</button>
                <span className="pg-info">{t("mail.pageOf", { p: page, n: totalPages })}</span>
                <button className="pgbtn" disabled={page >= totalPages || loadingMore} onClick={() => goPage(page + 1)} title={t("mail.nextPage")}>›</button>
              </div>
            )}
          </div>
        </div>

        <div className="resize-handle" onMouseDown={startResize} title={t("mail.resizeHint")} />

        {/* Lese-Spalte */}
        {open ? (
          <div className="mail-readcol">
            <div className="mail-head">
              <div className="mail-head-top">
                <h2 className="mail-head-subject">{open.subject || t("mail.noSubject")}</h2>
                <div className="mail-head-actions">
                  <button className="icon-btn" onClick={() => setDraft(replyDraft(open, t))} title={t("mail.reply")}>↩</button>
                  <button className="icon-btn" onClick={() => setDraft(forwardDraft(open, t))} title={t("mail.forward")}>↪</button>
                  <button className={`icon-btn ${readMenu ? "on" : ""}`} onClick={() => setReadMenu((v) => !v)} title={t("mail.more")}>⋯</button>
                  <button className="icon-btn" onClick={() => { setOpen(null); setMobilePane("list"); }} title={t("mail.back")}>✕</button>
                  {readMenu && (
                    <>
                      <div className="menu-backdrop" onClick={() => setReadMenu(false)} />
                      <div className="read-menu">
                        <button onClick={() => { markUnread(open.uid); setReadMenu(false); }}>● {t("mail.markUnread")}</button>
                        {spamFolder && folder !== spamFolder && (
                          <button onClick={() => { moveMsg(open.uid, spamFolder); setReadMenu(false); }}>🚫 {t("mail.spam")}</button>
                        )}
                        <button onClick={() => { setReadMenu(false); if (activeId != null) openTransfer(activeId, folder, [open.uid]); }}>↪ {t("xfer.toAccount")}</button>
                        {folderNames.length > 1 && (
                          <label className="read-menu-move">
                            <span>📁 {t("mail.moveTo")}</span>
                            <select value="" onChange={(e) => { if (e.target.value) { moveMsg(open.uid, e.target.value); setReadMenu(false); } }}>
                              <option value="">…</option>
                              {folderNames.filter((f) => f !== folder).map((f) => <option key={f} value={f}>{f}</option>)}
                            </select>
                          </label>
                        )}
                        <hr />
                        <button className="read-menu-del" onClick={() => { setReadMenu(false); del(open); }}>🗑 {t("mail.delete")}</button>
                      </div>
                    </>
                  )}
                </div>
              </div>
              <div className="mail-head-meta">
                <button className="mail-star" onClick={() => toggleFlag(open)} title={t("mail.flag")}>
                  {(messages.find((m) => m.uid === open.uid)?.flagged ?? open.flagged) ? "★" : "☆"}
                </button>
                <div className="mail-head-from">
                  <span className="mail-head-name">{parseAddr(open.from).name}</span>
                  <span className="mail-head-addr">&lt;{parseAddr(open.from).email}&gt;</span>
                </div>
                <button className="ghost mail-head-addc" onClick={saveSenderAsContact} disabled={contactSaved}
                  title={contactSaved ? t("mail.contactSaved") : t("mail.addContact")}>
                  {contactSaved ? "✓" : "＋👤"}
                </button>
                <button className="mail-head-toexp" onClick={() => setDetailsOpen((v) => !v)} title={t("mail.details")}>
                  {t("mail.hdrTo")}: {open.to[0] || "—"} <span aria-hidden>{detailsOpen ? "▴" : "▾"}</span>
                </button>
                <span className="grow" />
                <span className="mail-head-date">{prettyDate(open.date)}</span>
              </div>
              {detailsOpen && (
                <div className="mail-head-details">
                  <div><span className="label">{t("mail.hdrFrom")}</span><span>{open.from}</span></div>
                  <div><span className="label">{t("mail.hdrTo")}</span><span>{open.to.join(", ") || "—"}</span></div>
                  <div><span className="label">{t("mail.hdrDate")}</span><span>{open.date}</span></div>
                  <div><span className="label">{t("mail.hdrSubject")}</span><span>{open.subject || t("mail.noSubject")}</span></div>
                </div>
              )}
            </div>
            <hr style={{ borderColor: "var(--self-line)", margin: "0.9rem 0" }} />
            {open.text ? (
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{open.text}</div>
            ) : open.html ? (
              <>
                {blockImages && !showImages && hasRemoteContent(open.html) && (
                  <div className="img-banner">
                    <span>🛡 {t("mail.imagesBlocked")}</span>
                    <button className="ghost" onClick={() => setShowImages(true)}>{t("mail.showImages")}</button>
                  </div>
                )}
                <iframe title="mail-body" sandbox=""
                  srcDoc={buildSrcDoc(open.html, blockImages && !showImages)}
                  style={{ width: "100%", height: "62vh", border: "none", background: "#fff", borderRadius: "6px" }} />
              </>
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
            <button onClick={() => openTransfer(ctxMenu.acc, ctxMenu.node.path, null)}>↪ {t("xfer.folderToAccount")}</button>
            {!ctxMenu.node.special && <button onClick={() => { setFolderMove({ acc: ctxMenu.acc, path: ctxMenu.node.path }); setFmParent(""); setCtxMenu(null); }}>📂 {t("folder.moveInto")}</button>}
            {!ctxMenu.node.special && <button onClick={() => { renameFolder(ctxMenu.acc, ctxMenu.node); setCtxMenu(null); }}>✏ {t("folder.rename")}</button>}
            {!ctxMenu.node.special && <button onClick={() => { delFolder(ctxMenu.acc, ctxMenu.node.path); setCtxMenu(null); }}>🗑 {t("common.delete")}</button>}
            {ctxMenu.node.special !== "inbox" && <button onClick={() => { hideFolder(ctxMenu.acc, ctxMenu.node.path); setCtxMenu(null); }}>🙈 {t("folder.hide")}</button>}
          </div>
        </>
      )}

      {folderMove && (
        <div className="modal-backdrop" onClick={() => setFolderMove(null)}>
          <div className="modal card stack" onClick={(e) => e.stopPropagation()}>
            <div className="topbar">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{t("folder.moveInto")}</h2>
              <button type="button" className="ghost" onClick={() => setFolderMove(null)}>✕</button>
            </div>
            <div className="muted" style={{ fontSize: "0.85rem" }}>{folderMove.path}</div>
            <div className="stack">
              <label className="label">{t("folder.targetParent")}</label>
              <select value={fmParent} onChange={(e) => setFmParent(e.target.value)}>
                <option value="">{t("folder.topLevel")}</option>
                {(foldersByAcc[folderMove.acc] || [])
                  .filter((f) => f.name !== folderMove.path && !f.name.startsWith(folderMove.path + ".") && !f.name.startsWith(folderMove.path + "/"))
                  .map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}
              </select>
            </div>
            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button type="button" className="ghost" onClick={() => setFolderMove(null)}>{t("common.cancel")}</button>
              <button className="primary" onClick={submitFolderMove}>{t("xfer.move")}</button>
            </div>
          </div>
        </div>
      )}

      {transfer && (
        <div className="modal-backdrop" onClick={() => setTransfer(null)}>
          <div className="modal card stack" onClick={(e) => e.stopPropagation()}>
            <div className="topbar">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{t("xfer.title")}</h2>
              <button type="button" className="ghost" onClick={() => setTransfer(null)}>✕</button>
            </div>
            <div className="muted" style={{ fontSize: "0.85rem" }}>
              {transfer.uids ? t("xfer.subjMails", { n: transfer.uids.length }) : t("xfer.subjFolder", { folder: transfer.sourceFolder })}
            </div>
            <div className="stack">
              <label className="label">{t("xfer.account")}</label>
              <select value={xferAcc} onChange={(e) => { setXferAcc(Number(e.target.value)); setXferFolder(""); }}>
                <option value={0}>{t("xfer.pickAccount")}</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.label || a.email}</option>)}
              </select>
            </div>
            <div className="stack">
              <label className="label">{t("xfer.folder")}</label>
              <input list="xfer-folders" value={xferFolder} placeholder={t("xfer.folderPlaceholder")}
                     onChange={(e) => setXferFolder(e.target.value)} />
              <datalist id="xfer-folders">
                {(foldersByAcc[xferAcc] || []).map((f) => <option key={f.name} value={f.name} />)}
              </datalist>
            </div>
            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button type="button" className="ghost" onClick={() => setTransfer(null)}>{t("common.cancel")}</button>
              <button className="ghost" disabled={xferBusy || !xferAcc || !xferFolder} onClick={() => submitTransfer(false)}>{t("xfer.copy")}</button>
              <button className="primary" disabled={xferBusy || !xferAcc || !xferFolder} onClick={() => submitTransfer(true)}>{xferBusy ? "…" : t("xfer.move")}</button>
            </div>
          </div>
        </div>
      )}

      {draft && activeId != null && (
        <Compose accountId={activeId} draft={draft} onClose={() => { setDraft(null); }} />
      )}
    </div>
  );
}

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, copyText, download, type Account, type MsgHeader, type MsgDetail, type AuthInfo, type TransferResult, type FolderCount } from "../lib/api";
import { useLang } from "../lib/i18n";
import { promptDialog } from "../lib/dialog";
import { buildFolderTree, specialKind, SPECIAL_ICON, type FolderNode } from "../lib/folders";
import { Compose, emptyDraft, replyDraft, forwardDraft, type Draft } from "../components/Compose";

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
// Server-Datumsstring hübsch lokalisiert; fällt bei Parse-Fehler auf Rohtext zurück.
function prettyDate(s: string): string {
  const d = new Date(s);
  return isNaN(d.getTime()) ? s : d.toLocaleString();
}
// Kompaktes Datum MIT Uhrzeit für die Listenzeile (z. B. "20. Jun 26, 17:24").
function listDate(s: string): string {
  const d = new Date(s);
  if (isNaN(d.getTime())) return (s || "").slice(0, 16);
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", year: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

// CSP, die im Mail-iframe ALLE externen Ladevorgänge (Bilder/Schriften/Medien)
// blockiert — nur eingebettete data:/cid:-Bilder und Inline-Styles sind erlaubt.
// So laden keine Tracking-Pixel; Skripte sind ohnehin per sandbox="" geblockt.
const _CSP_BLOCK =
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: cid:; style-src 'unsafe-inline'; font-src data:; media-src data:;">`;
function hasRemoteContent(html: string): boolean {
  return /(?:src|background)\s*=\s*["']?\s*https?:/i.test(html) || /url\(\s*['"]?\s*https?:/i.test(html);
}
// Dunkelmodus für Mails: KEIN Invertieren mehr (scheitert bei Design-Mails wie
// Bosch — Hintergrund dunkel, aber Schrift blieb dunkel/unlesbar). Stattdessen
// "Lese-Dunkelmodus": dunkler Hintergrund + Schrift IMMER hell erzwingen
// (!important schlägt die Mail-eigenen Farben), eigene Hintergründe neutralisieren,
// Links hell, Bilder unverändert. So ist JEDE Mail dunkel mit lesbarer heller Schrift.
const _DARK_STYLE =
  `<style>:root{color-scheme:dark}` +
  `html,body{background:#0d1117 !important;color:#e6edf3 !important;}` +
  `*{background-color:transparent !important;border-color:#30363d !important;}` +
  `*:not(a){color:#e6edf3 !important;}` +
  `a{color:#6cb6ff !important;}` +
  `img,picture,video,svg,canvas{filter:none !important;}` +
  `</style>`;
function buildSrcDoc(html: string, block: boolean, dark: boolean): string {
  return `<!DOCTYPE html><meta charset="utf-8">${block ? _CSP_BLOCK : ""}${dark ? _DARK_STYLE : ""}<base target="_blank">${html}`;
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

// Virtuelle Gmail-Label-Ordner: "Alle Nachrichten" (all), "Wichtig" (important)
// und "Markiert" (flagged) enthalten Kopien von Mails, die schon im echten
// Ordner liegen. Aus Ungelesen-Summen ausklammern, sonst wird doppelt gezählt.
const VIRTUAL_SPECIAL = new Set(["all", "important", "flagged"]);

function loadSet(key: string): Set<number> {
  try { const v = JSON.parse(localStorage.getItem(key) || "[]"); return new Set(Array.isArray(v) ? v : []); }
  catch { return new Set(); }
}

// Ist der Fehler ein „nicht gefunden" (Mail serverseitig weg)? Bevorzugt den
// echten HTTP-Status (ApiError.status === 404); nur als Rückfall für Nicht-
// ApiError-Fehler wird noch der Text geprüft.
function isNotFound(e: unknown): boolean {
  if (e instanceof ApiError) return e.status === 404;
  return /nicht gefunden|not found|404/i.test((e as Error)?.message || "");
}

type AuthView = {
  color: string; bg: string; border: string;
  icon: string; short: string; text: string; tip: string; chips: string;
};
// Echtheits-Status aufbereiten: kompaktes Kurzwort (Chip) + Volltext (Ausklappen)
// + SPF/DKIM/DMARC-Chips, jeweils farbcodiert.
function authView(auth: AuthInfo | null, de: boolean): AuthView {
  let border = "var(--self-line)", color = "var(--self-text-2)", bg = "var(--self-bg-3)";
  let icon = "🛈", short = de ? "Nicht prüfbar" : "Not verifiable";
  let text = de ? "Echtheit nicht prüfbar" : "Not verifiable", tip = "", chips = "";
  if (auth) {
    chips = ([["SPF", auth.spf], ["DKIM", auth.dkim], ["DMARC", auth.dmarc]] as const)
      .filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(" · ");
    if (auth.self_spoof) {
      border = "#ef4444"; color = "#fca5a5"; bg = "rgba(239,68,68,0.12)"; icon = "⚠️";
      short = de ? "Gefälscht" : "Forged";
      text = de ? "Gefälschter Absender — du wurdest NICHT gehackt" : "Forged sender — you were NOT hacked";
      tip = de
        ? "Diese E-Mail gibt vor, von deiner eigenen Adresse zu kommen, ist aber nicht authentifiziert (Spoofing/Erpressung)."
        : "This email pretends to come from your own address but is not authenticated (spoofing/extortion).";
    } else if (auth.verdict === "fail") {
      border = "#ef4444"; color = "#fca5a5"; bg = "rgba(239,68,68,0.1)"; icon = "⚠️";
      short = de ? "Evtl. gefälscht" : "Possibly forged";
      text = de ? "Möglicherweise gefälscht" : "Possibly forged";
    } else if (auth.verdict === "pass") {
      border = "#22c55e"; color = "#86efac"; bg = "rgba(34,197,94,0.1)"; icon = "✓";
      short = de ? "Echt" : "Verified";
      text = de ? "Echtheit bestätigt" : "Verified";
    }
  }
  return { color, bg, border, icon, short, text, tip, chips };
}

export function Mail({ search = "", filter, pollMin = 5, blockImages = true, darkMail = true, onUnseenChange }: { search?: string; filter?: MailFilter; pollMin?: number; blockImages?: boolean; darkMail?: boolean; onUnseenChange?: (total: number) => void }) {
  const { t, lang } = useLang();
  const de = lang === "de";
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
  // "Alle im Ordner" (über alle Seiten) aktiv? Dann enthält `selected` alle UIDs.
  const [selectAllFolder, setSelectAllFolder] = useState(false);
  const [syncing, setSyncing] = useState(false);
  // Lese-Kopf: Mehr-Menü (⋯) und ausklappbare Detailzeilen (Von/An/Datum/Betreff).
  const [readMenu, setReadMenu] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  // Absender als Kontakt gespeichert? (kurzes Erfolgs-Feedback im Lesekopf)
  const [contactSaved, setContactSaved] = useState(false);
  // Pro geöffneter Mail: hat der Nutzer externe Bilder freigegeben?
  const [showImages, setShowImages] = useState(false);
  // Übersetzung der aktuell geöffneten Mail (null = nicht übersetzt).
  const [translated, setTranslated] = useState<string | null>(null);
  const [translating, setTranslating] = useState(false);
  const [translateEnabled, setTranslateEnabled] = useState(false);
  // Doppelklick auf eine Mail öffnet sie zusätzlich in einem eigenen Popup-Fenster.
  const [popup, setPopup] = useState(false);
  // Echtheits-Details (SPF/DKIM/DMARC + Volltext) zum kompakten Status-Chip aufgeklappt?
  const [authOpen, setAuthOpen] = useState(false);
  // Lesebestätigung: pro UID gemerkt, ob schon gesendet ("sent") oder ignoriert ("hidden").
  // Nur für diese Sitzung — verhindert doppeltes Nachfragen nach der Aktion.
  const [mdnState, setMdnState] = useState<Record<string, "sent" | "hidden">>({});
  const [mdnBusy, setMdnBusy] = useState(false);
  // Helle Mails automatisch in den dunklen App-Look umfaerben. Standard kommt aus
  // der globalen Einstellung (darkMail), ist aber pro Mail umschaltbar.
  const [darkBody, setDarkBody] = useState(darkMail);
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
  // Konto-Transfer: ausgewählte Mails ODER ganzer Ordner (uids=null) in ein
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

  // --- Konten + Ordner (mit Zählern) laden ---
  // Live-Auffrischung der Ordnerzähler — NIE awaiten, damit die UI nie auf IMAP
  // wartet (das war die Ursache für den F5-Freeze bei langsamen Konten).
  function refreshFoldersLive(a: Account) {
    api.get<FolderCount[]>(`/mail/${a.id}/folders/counts?live=1`)
      .then((fc) => { if (fc.length) setFoldersByAcc((m) => ({ ...m, [a.id]: fc })); })
      .catch(() => { /* Cache/INBOX bleibt stehen */ });
  }

  async function loadAccountFolders(a: Account | undefined, liveRefresh = true) {
    if (!a) return;  // Konto nicht (mehr) vorhanden -> nichts zu laden
    // Cache-first: ist die Ordnerliste schon gecacht, erscheint die Seitenleiste
    // SOFORT (kein IMAP). Live-Auffrischung NUR fürs aktive Konto (liveRefresh),
    // damit nicht 8 langsame IMAP-Abrufe gleichzeitig die App blockieren.
    let hadCache = false;
    try {
      const cached = await api.get<FolderCount[]>(`/mail/${a.id}/folders/counts`);
      if (cached.length) { hadCache = true; setFoldersByAcc((m) => ({ ...m, [a.id]: cached })); }
    } catch { /* egal — unten Fallback */ }

    if (!hadCache) {
      // Noch kein Cache: SOFORT INBOX zeigen, nie auf IMAP warten. Live nur, wenn
      // das aktive Konto — sonst erst beim Anklicken (kein 8-fach-IMAP-Sturm).
      setFoldersByAcc((m) => ({ ...m, [a.id]: m[a.id]?.length ? m[a.id] : [{ name: "INBOX", unseen: 0, total: 0 }] }));
      if (liveRefresh) api.post(`/mail/${a.id}/folders/defaults`).catch(() => {}).then(() => refreshFoldersLive(a));
      return;
    }

    if (liveRefresh) refreshFoldersLive(a);
  }
  useEffect(() => {
    api.get<Account[]>("/accounts").then((list) => {
      setAccounts(list);
      const firstId = list.length ? list[0].id : null;
      if (firstId != null) setSel((s) => s ?? { acc: firstId, folder: "INBOX" });
      // Cache-first für ALLE (instant); Live nur fürs aktive Konto.
      list.forEach((a) => loadAccountFolders(a, a.id === firstId));
    }).catch((e) => setErr((e as Error).message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const treesByAcc = useMemo(() => {
    const out: Record<number, FolderNode[]> = {};
    // Ganze Folder-Objekte (inkl. Backend-`special`) übergeben, damit der Baum
    // Sonderordner provider-einheitlich erkennt (statt nur per Namens-Heuristik).
    for (const [id, fcs] of Object.entries(foldersByAcc)) out[Number(id)] = buildFolderTree(fcs);
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
    // Ausgeblendete Ordner NICHT mitzählen — sonst zeigt der zugeklappte Konto-
    // Kopf eine Zahl, die in keinem sichtbaren Ordner auftaucht.
    // Virtuelle Gmail-Label-Ordner ("Alle Nachrichten"/"Wichtig"/"Markiert")
    // NICHT mitzählen — sie enthalten Kopien derselben Mails, die schon im
    // echten Ordner (INBOX etc.) gezählt sind, sonst wird doppelt gezählt.
    const hidden = hiddenByAcc[accId] || [];
    return (foldersByAcc[accId] || []).reduce(
      (s, f) => s + (hidden.includes(f.name) || VIRTUAL_SPECIAL.has(f.special || "") ? 0 : (f.unseen || 0)),
      0,
    );
  }
  // Gesamt-Ungelesen (alle Konten, ohne ausgeblendete Ordner) nach oben melden —
  // für das Badge am Mail-Icon der oberen Navigation (auch außerhalb des Mailbereichs).
  useEffect(() => {
    if (!onUnseenChange) return;
    onUnseenChange(accounts.reduce((s, a) => s + rollupUnseen(a.id), 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [foldersByAcc, accounts, hiddenByAcc, onUnseenChange]);
  // Ungelesen-Zähler lokal anpassen (ohne erneuten IMAP-Abruf).
  function bumpUnseen(accId: number, path: string, delta: number) {
    setFoldersByAcc((m) => ({
      ...m,
      [accId]: (m[accId] || []).map((f) => f.name === path ? { ...f, unseen: Math.max(0, f.unseen + delta) } : f),
    }));
  }
  function refreshCounts(accId: number) {
    // Auffrischen heißt hier: echte, frische Zähler vom Server (live) holen —
    // und damit zugleich den Cache aktualisieren.
    api.get<FolderCount[]>(`/mail/${accId}/folders/counts?live=1`)
      .then((fc) => { if (fc.length) setFoldersByAcc((m) => ({ ...m, [accId]: fc })); }).catch(() => {});
  }
  // ↻ pro Konto: Filterregeln anwenden, Zähler neu holen + aktive Liste neu laden.
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
  // Posteingänge initial aufgeklappt.
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
    const name = await promptDialog(t("folder.newTopPrompt"));
    if (!name || !name.trim()) return;
    try { await api.post(`/mail/${accId}/folders?name=${encodeURIComponent(name.trim())}`); loadAccountFolders(accountById(accId)); }
    catch (e) { setErr((e as Error).message); }
  }
  async function newSubfolder(accId: number, node: FolderNode) {
    const name = await promptDialog(t("folder.newPrompt", { parent: node.special ? t(`folder.${node.special}`) : node.label }));
    if (!name || !name.trim()) return;
    try {
      await api.post(`/mail/${accId}/folders?name=${encodeURIComponent(name.trim())}&parent=${encodeURIComponent(node.path)}`);
      setExpanded((s) => new Set(s).add(expKey(accId, node.path)));
      loadAccountFolders(accountById(accId));
    } catch (e) { setErr((e as Error).message); }
  }
  async function renameFolder(accId: number, node: FolderNode) {
    const newName = await promptDialog(t("folder.renamePrompt"), node.label);
    if (!newName || !newName.trim() || newName.trim() === node.label) return;
    try { await api.post(`/mail/${accId}/folders/rename?name=${encodeURIComponent(node.path)}&new_name=${encodeURIComponent(newName.trim())}`); loadAccountFolders(accountById(accId)); }
    catch (e) { setErr((e as Error).message); }
  }
  async function delFolder(accId: number, path: string) {
    if (!(await askConfirm(t("folder.deleteConfirm", { name: path })))) return;
    try {
      await api.del(`/mail/${accId}/folders?name=${encodeURIComponent(path)}`);
      if (activeId === accId && folder === path) setSel({ acc: accId, folder: "INBOX" });
      loadAccountFolders(accountById(accId));
    } catch (e) { setErr((e as Error).message); }
  }
  // Kann undefined liefern (Konto ggf. gerade entfernt) — Aufrufer defensiv (?.).
  function accountById(id: number): Account | undefined { return accounts.find((a) => a.id === id); }

  // Eigener Bestätigungs-Dialog statt window.confirm (mittig, im App-Design).
  const [confirmBox, setConfirmBox] = useState<{ message: string; resolve: (ok: boolean) => void } | null>(null);
  function askConfirm(message: string): Promise<boolean> {
    return new Promise((resolve) => setConfirmBox({ message, resolve }));
  }

  // „Original anzeigen": rohe RFC822-Quelle laden + im Modal zeigen.
  const [rawText, setRawText] = useState<string | null>(null);
  async function showRaw(uid: string) {
    if (activeId == null) return;
    try {
      setRawText(await api.get<string>(`/mail/${activeId}/messages/${uid}/raw?folder=${encodeURIComponent(folder)}`));
    } catch (e) { setErr((e as Error).message); }
  }

  // --- Nachrichten ---
  // Aktuelle Auswahl als Ref, damit ein verspäteter Hintergrund-Sync nur dann
  // die Liste aktualisiert, wenn der Nutzer noch im selben Ordner ist.
  const selRef = useRef(sel);
  useEffect(() => { selRef.current = sel; }, [sel]);
  // Aktuelle Seite als Ref, damit der periodische Auto-Sync genau die gerade
  // sichtbare Seite auffrischt (ohne den Effekt bei jedem Seitenwechsel neu zu setzen).
  const pageRef = useRef(1);
  useEffect(() => { pageRef.current = page; }, [page]);
  // Versionszähler für loadAllForSearch: verwirft veraltete Antworten,
  // wenn während des Ladens erneut (anderer Ordner/Suche) geladen wurde.
  const searchLoadRef = useRef(0);
  // Sequenzzähler je (Konto,Ordner) für bgSync: überlappende Sync-Aufrufe
  // (Polling + 20-s-Refresh + SSE) laufen sonst parallel und schreiben in
  // beliebiger Reihenfolge zurück. Nur die JÜNGSTE Antwort pro (acc,folder)
  // darf die Liste aktualisieren; ältere Antworten werden verworfen.
  const bgSyncSeqRef = useRef<Map<string, number>>(new Map());
  // Aufeinanderfolgende Fehler je (Konto,Ordner) für den Backoff (siehe unten).
  const bgSyncFailRef = useRef<Map<string, number>>(new Map());
  // Frühester nächster Sync-Zeitpunkt je (Konto,Ordner) — für den Backoff.
  const bgSyncNextRef = useRef<Map<string, number>>(new Map());

  // Löst der 20-s-Refresh gerade aus, oder soll wegen Fehlern gewartet werden?
  // Einfacher exponentieller Backoff: nach n Fehlern frühestens nach
  // min(2^n * BASE, MAX) ms erneut versuchen. Bei Erfolg (siehe bgSync) wird der
  // Fehlerzähler geleert, sodass der nächste Aufruf sofort wieder feuert.
  function bgSyncDue(acc: number, fol: string): boolean {
    const key = `${acc}:${fol}`;
    const fails = bgSyncFailRef.current.get(key) ?? 0;
    if (fails === 0) return true;
    const now = Date.now();
    const next = bgSyncNextRef.current.get(key) ?? 0;
    if (now < next) return false;  // Backoff-Fenster noch nicht verstrichen
    const BACKOFF_BASE_MS = 20000, BACKOFF_MAX_MS = 300000;  // 20 s … 5 min
    const delay = Math.min(BACKOFF_BASE_MS * 2 ** fails, BACKOFF_MAX_MS);
    bgSyncNextRef.current.set(key, now + delay);
    return true;
  }

  // Holt genau eine Seite (offset = (p-1)*PAGE_SIZE). Cache-first im Backend.
  function fetchPage(acc: number, fol: string, p: number) {
    return api.get<MsgHeader[]>(`/mail/${acc}/messages?folder=${encodeURIComponent(fol)}&limit=${PAGE_SIZE}&offset=${(p - 1) * PAGE_SIZE}`);
  }
  // Ordnerwechsel/Neuladen: immer auf Seite 1, Auswahl zurücksetzen.
  function reload() {
    if (!sel) return;
    const acc = sel.acc, fol = sel.folder;
    setLoading(true); setErr(""); setOpen(null); setSelected(new Set()); setSelectAllFolder(false); setPage(1);
    fetchPage(acc, fol, 1)
      .then((ms) => { setMessages(ms); warmBodies(acc, fol, ms); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => { setLoading(false); bgSync(acc, fol, 1); });
  }
  // Hintergrund-Sync: neue Mails/Flags nachziehen, dann die Seite p still auffrischen.
  // Guard: pro (Konto,Ordner) einen Sequenzzähler führen; nur die jüngste Antwort
  // übernehmen — so überschreiben überlappende Aufrufe (Polling/20s/SSE) einander
  // nicht mehr mit veralteten Daten.
  function bgSync(acc: number, fol: string, p: number = 1) {
    const key = `${acc}:${fol}`;
    const ver = (bgSyncSeqRef.current.get(key) ?? 0) + 1;
    bgSyncSeqRef.current.set(key, ver);
    const isLatest = () => bgSyncSeqRef.current.get(key) === ver;
    setSyncing(true);
    api.post(`/mail/${acc}/sync?folder=${encodeURIComponent(fol)}`)
      .then(() => fetchPage(acc, fol, p))
      .then((ms) => {
        bgSyncFailRef.current.delete(key);  // Erfolg -> Backoff zurücksetzen
        if (!isLatest()) return;  // veraltete Antwort: verwerfen
        if (selRef.current?.acc === acc && selRef.current?.folder === fol) setMessages(ms);
        warmBodies(acc, fol, ms);
        refreshCounts(acc);
      })
      .catch(() => {
        // Fehlversuch zählen (für den exponentiellen Backoff der Auto-Refresher).
        bgSyncFailRef.current.set(key, (bgSyncFailRef.current.get(key) ?? 0) + 1);
      })
      .finally(() => { if (isLatest()) setSyncing(false); });
  }
  // Zu Seite p springen: ersetzt die Liste. Auswahl bleibt erhalten (seitenübergreifend).
  function goPage(p: number) {
    if (!sel) return;
    const clamped = Math.max(1, Math.min(totalPages, p));
    if (clamped === page) return;
    setPage(clamped); setOpen(null); setLoadingMore(true);
    fetchPage(sel.acc, sel.folder, clamped)
      .then((ms) => { setMessages(ms); warmBodies(sel.acc, sel.folder, ms); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoadingMore(false));
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  // Suche: ganzen Ordner laden (bis Cache-Tiefe), damit Treffer über alle Seiten
  // gefunden und in einer Liste angezeigt werden.
  function loadAllForSearch() {
    if (!sel) return;
    const acc = sel.acc, fol = sel.folder;
    const ver = ++searchLoadRef.current;
    setLoading(true); setErr(""); setOpen(null); setSelected(new Set()); setSelectAllFolder(false);
    api.get<MsgHeader[]>(`/mail/${acc}/messages?folder=${encodeURIComponent(fol)}&limit=1000`)
      .then((ms) => { if (ver !== searchLoadRef.current) return; setMessages(ms); warmBodies(acc, fol, ms); })
      .catch((e) => { if (ver !== searchLoadRef.current) return; setErr((e as Error).message); })
      .finally(() => { if (ver === searchLoadRef.current) setLoading(false); });
  }
  // Bei Ordnerwechsel ODER Wechsel Suche an/aus passend laden.
  useEffect(() => {
    if (!sel) return;
    if (searchActive) loadAllForSearch(); else reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel?.acc, sel?.folder, searchActive]);

  // Beim Wechsel auf ein Konto dessen Ordnerzähler EINMAL live auffrischen.
  // Inaktive Konten bleiben bis dahin auf dem Cache -> kein 8-fach-IMAP-Sturm.
  useEffect(() => {
    if (sel?.acc != null) refreshCounts(sel.acc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel?.acc]);

  // Mobile: schließt sich die Lese-Ansicht (Löschen/Verschieben/✕), zurück zur Liste.
  useEffect(() => {
    if (!open && mobilePane === "read") setMobilePane("list");
    setContactSaved(false);  // bei jeder geöffneten Mail das Kontakt-Feedback zurücksetzen
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Auto-Abruf: alle pollMin Minuten Zähler aller Konten + aktive Liste auffrischen.
  useEffect(() => {
    if (!pollMin || accounts.length === 0) return;
    const id = window.setInterval(() => {
      // NUR das aktive Konto live auffrischen (nicht alle 8 gleichzeitig — das
      // hat bei langsamen Konten die App blockiert).
      if (selRef.current && bgSyncDue(selRef.current.acc, selRef.current.folder)) {
        refreshCounts(selRef.current.acc);
        bgSync(selRef.current.acc, selRef.current.folder, pageRef.current);
      }
    }, pollMin * 60000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollMin, accounts, sel?.acc, sel?.folder]);

  // Schnelles Live-Gefühl im OFFENEN Postfach: solange der Tab im Vordergrund ist,
  // das aktive Konto/Ordner alle 20 s aktiv vom Server holen (bgSync = echter
  // IMAP-Abruf) — so erscheinen neue Mails fast sofort, ohne auf den Hintergrund-
  // Sync (2 Min) bzw. dessen SSE-Event zu warten. Pausiert im Hintergrund-Tab.
  useEffect(() => {
    if (accounts.length === 0) return;
    const FAST_REFRESH_MS = 20000;
    const id = window.setInterval(() => {
      if (document.hidden || !selRef.current) return;
      if (!bgSyncDue(selRef.current.acc, selRef.current.folder)) return;  // Backoff bei Fehlern
      refreshCounts(selRef.current.acc);
      bgSync(selRef.current.acc, selRef.current.folder, pageRef.current);
    }, FAST_REFRESH_MS);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accounts]);

  // Ist die Übersetzung serverseitig konfiguriert? (Button nur dann zeigen.)
  useEffect(() => {
    api.get<{ enabled: boolean }>("/translate/status").then((r) => setTranslateEnabled(!!r.enabled)).catch(() => {});
  }, []);

  // Aktuelle Mail nach Deutsch übersetzen (Toggle: nochmal = Original).
  async function doTranslate(msg: MsgDetail) {
    if (translated != null) { setTranslated(null); return; }
    const src = (msg.text && msg.text.trim()) ? msg.text : msg.html.replace(/<[^>]+>/g, " ");
    if (!src.trim()) return;
    setTranslating(true);
    try {
      const r = await api.post<{ translated: string; source: string }>("/translate", { text: src, target: "de", source: "auto" });
      setTranslated(r.translated || "(keine Übersetzung)");
    } catch (e) { setErr((e as Error).message); }
    finally { setTranslating(false); }
  }

  // Live-Sync (SSE): sobald irgendwer (anderes Gerät / Web-Tab) etwas ändert
  // oder neue Mail eintrifft, schickt der Server ein Event → aktiver Ordner +
  // Zähler frischen sofort auf. EventSource reconnectet automatisch.
  useEffect(() => {
    // Auth über das httpOnly-Session-Cookie (same-origin automatisch mitgesendet) —
    // das Token steht nicht mehr in der URL (kein Leck in Server-/Proxy-Logs).
    const es = new EventSource("/api/v1/events/stream");
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as { type?: string; account_id?: number };
        if (ev.type !== "mail") return;
        if (typeof ev.account_id === "number") refreshCounts(ev.account_id);
        if (selRef.current && selRef.current.acc === ev.account_id) {
          bgSync(selRef.current.acc, selRef.current.folder, pageRef.current);
        }
      } catch { /* ignorieren */ }
    };
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Globale Einstellung umgeschaltet -> aktuell offene Mail sofort mitziehen.
  useEffect(() => { setDarkBody(darkMail); }, [darkMail]);

  function patchHeader(uid: string, patch: Partial<MsgHeader>) {
    setMessages((ms) => ms.map((m) => (m.uid === uid ? { ...m, ...patch } : m)));
  }

  async function openMsg(uid: string, asPopup = false) {
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
      setPopup(asPopup);
      setMobilePane("read");
      setDetailsOpen(false);
      setAuthOpen(false);
      setReadMenu(false);
      setShowImages(false);
      setTranslated(null);
      setDarkBody(darkMail);
      if (!msg.seen) {
        api.post(`/mail/${activeId}/messages/${uid}/flags?folder=${encodeURIComponent(folder)}&seen=true`).catch(() => {});
        patchHeader(uid, { seen: true });
        bumpUnseen(activeId, folder, -1);
      }
    } catch (e) {
      // Mail serverseitig weg (Cache war kurz veraltet): Zeile entfernen, klare
      // Meldung statt rohem Fehler, und still neu synchronisieren (selbstheilend).
      if (isNotFound(e)) {
        setMessages((ms) => ms.filter((x) => x.uid !== uid));
        prefetchedRef.current.delete(`${activeId}:${folder}:${uid}`);
        if (open?.uid === uid) setOpen(null);
        setErr(t("mail.gone"));
        if (sel) bgSync(sel.acc, sel.folder, pageRef.current);
      } else {
        setErr((e as Error).message || "");
      }
    }
  }
  // Body beim Drüberfahren vorladen: der erste Live-Abruf landet im DB-Cache,
  // sodass der anschließende Klick die Mail SOFORT aus dem Cache zeigt. Pro
  // (Konto/Ordner/uid) nur einmal — kein Mehrfach-Login beim Hin- und Herfahren.
  // Ganze Listenseite in EINEM Request vorwaermen: das Backend holt die noch
  // nicht gecachten Bodies in einer IMAP-Session und legt sie in der DB ab.
  // Danach kommt JEDER Klick sofort aus dem Cache — kein Gedenkzeit-Login mehr.
  function warmBodies(acc: number, fol: string, msgs: MsgHeader[]) {
    const uids = msgs.map((m) => m.uid).filter(Boolean);
    if (uids.length === 0) return;
    api.post(`/mail/${acc}/messages/prefetch`, { folder: fol, uids })
      .then(() => { uids.forEach((u) => prefetchedRef.current.add(`${acc}:${fol}:${u}`)); })
      .catch(() => { /* Vorwärmen ist best-effort */ });
  }
  const prefetchedRef = useRef<Set<string>>(new Set());
  function prefetchMsg(uid: string) {
    if (activeId == null) return;
    const key = `${activeId}:${folder}:${uid}`;
    if (prefetchedRef.current.has(key)) return;
    prefetchedRef.current.add(key);  // genau EIN Versuch je Mail — KEIN Retry-Sturm
    api.get<MsgDetail>(`/mail/${activeId}/messages/${uid}?folder=${encodeURIComponent(folder)}`)
      .catch((e) => {
        // Geister-Mail (serverseitig weg) beim Hover: still aus der Liste nehmen,
        // NICHT erneut versuchen (Key bleibt gesetzt). Verhindert 404-Endlosschleife.
        if (isNotFound(e)) {
          setMessages((ms) => ms.filter((x) => x.uid !== uid));
          if (open?.uid === uid) setOpen(null);
        }
      });
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
    if (!(await askConfirm(t("mail.confirmDelete")))) return;
    setErr("");
    try {
      await api.del(`/mail/${activeId}/messages/${m.uid}?folder=${encodeURIComponent(folder)}`);
      setMessages((ms) => ms.filter((x) => x.uid !== m.uid));
      if (!m.seen) bumpUnseen(activeId, folder, -1);
      if (open?.uid === m.uid) setOpen(null);
    } catch (e) { setErr((e as Error).message); }
  }

  async function blockSender() {
    if (activeId == null || !open) return;
    const addr = parseAddr(open.from).email.trim();
    if (!addr) return;
    if (!(await askConfirm(t("mail.blockConfirm", { sender: addr })))) return;
    setErr("");
    const acc = activeId, lower = addr.toLowerCase();
    try {
      await api.post(`/mail/${acc}/block-sender`, { sender: addr, delete_existing: true });
      // Sichtbare Mails dieses Absenders sofort aus der Liste nehmen + Lesefenster schließen.
      setMessages((ms) => ms.filter((x) => !parseAddr(x.from).email.toLowerCase().includes(lower)));
      setOpen(null);
      refreshCounts(acc);
    } catch (e) { setErr((e as Error).message); }
  }

  function toggleSelect(uid: string) {
    setSelected((s) => { const n = new Set(s); if (n.has(uid)) n.delete(uid); else n.add(uid); return n; });
  }
  async function delSelected() {
    if (activeId == null || selected.size === 0) return;
    if (!(await askConfirm(t("mail.confirmDelete")))) return;
    const ids = new Set(selected), wasAll = selectAllFolder, acc = activeId, fol = folder;
    // SOFORT reagieren: Zeilen weg + Auswahl leeren — NICHT auf den Server warten.
    if (open && ids.has(open.uid)) setOpen(null);
    setMessages((ms) => ms.filter((m) => !ids.has(m.uid)));
    setSelected(new Set()); setSelectAllFolder(false);
    // EIN Request statt N: alle UIDs in einer IMAP-Session löschen (ein Login).
    // Im Hintergrund; bei Fehler echte Liste wiederherstellen.
    api.post(`/mail/${acc}/messages/batch-delete`, { folder: fol, uids: [...ids] })
      .then(() => { refreshCounts(acc); if (wasAll && selRef.current?.acc === acc && selRef.current?.folder === fol) reload(); })
      .catch((e) => { setErr((e as Error).message); if (selRef.current?.acc === acc && selRef.current?.folder === fol) reload(); });
  }
  async function markSelectedSeen(seen: boolean) {
    if (activeId == null || selected.size === 0) return;
    const ids = [...selected], acc = activeId, fol = folder;
    // SOFORT optisch markieren, dann EIN Batch-Request (eine IMAP-Session) statt N.
    setMessages((ms) => ms.map((m) => (selected.has(m.uid) ? { ...m, seen } : m)));
    setSelected(new Set()); setSelectAllFolder(false);
    try {
      await api.post(`/mail/${acc}/messages/batch-flags?seen=${seen}`, { folder: fol, uids: ids });
      refreshCounts(acc);
    } catch (e) {
      setErr((e as Error).message);
      if (selRef.current?.acc === acc && selRef.current?.folder === fol) reload();
    }
  }
  // Mehrere Nachrichten verschieben (Auswahl-Leiste ODER Drag&Drop).
  async function moveUids(dest: string, uids: string[]) {
    if (activeId == null || !dest || uids.length === 0) return;
    setErr("");
    const ids = [...uids], wasAll = selectAllFolder, acc = activeId, fol = folder;
    // SOFORT reagieren: Zeilen weg + Auswahl leeren — NICHT auf den Server warten.
    if (open && ids.includes(open.uid)) setOpen(null);
    setMessages((ms) => ms.filter((m) => !ids.includes(m.uid)));
    setSelected(new Set()); setSelectAllFolder(false);
    // EIN Request statt N: alle UIDs in einer IMAP-Session verschieben (ein Login).
    api.post(`/mail/${acc}/messages/batch-move`, { folder: fol, uids: ids, dest })
      .then(() => { refreshCounts(acc); if (wasAll && selRef.current?.acc === acc && selRef.current?.folder === fol) reload(); })
      .catch((e) => { setErr((e as Error).message); if (selRef.current?.acc === acc && selRef.current?.folder === fol) reload(); });
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

  // Absender der offenen Mail ins Adressbuch übernehmen (Name + Adresse).
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
    // War der ausgeblendete Ordner gerade aktiv → zurück auf den Posteingang.
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

  const activeFolders = foldersByAcc[activeId ?? -1] || [];
  const folderNames = activeFolders.map((f) => f.name);
  // Spam-Ordner des aktiven Kontos (für den Spam-Button): Backend-`special`
  // bevorzugen, sonst auf die Namens-Heuristik zurückfallen.
  const spamFolder = (
    activeFolders.find((f) => f.special === "spam")?.name ??
    folderNames.find((f) => specialKind(f.split(/[/.]/).pop() || f) === "spam")
  );

  const visible = messages
    .filter((m) => !search || `${m.subject} ${m.from} ${m.snippet}`.toLowerCase().includes(search.toLowerCase()))
    .filter((m) => !filter?.from || m.from.toLowerCase().includes(filter.from.toLowerCase()))
    .filter((m) => !filter?.subject || m.subject.toLowerCase().includes(filter.subject.toLowerCase()))
    .filter((m) => !filter?.unread || !m.seen)
    .filter((m) => !filter?.starred || m.flagged)
    .filter((m) => !filter?.attachments || m.has_attachments)
    .filter((m) => inDateRange(m.date, filter?.dateFrom, filter?.dateTo));
  // Gesamtzahl/Seitenzahl des aktiven Ordners aus den Zählern (IMAP STATUS).
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

  // Kompakter Echtheits-Chip (klickbar → Details ausklappen) + „Bilder anzeigen",
  // beides für den Kopf der Lese-Ansicht UND das Doppelklick-Popup.
  function authCluster(msg: MsgDetail): ReactNode {
    const v = authView(msg.auth ?? null, de);
    const imagesBlocked = !!msg.html && blockImages && !showImages && hasRemoteContent(msg.html);
    if (!msg.auth && !imagesBlocked && !translateEnabled) return null;
    return (
      <>
        {msg.auth && (
          <button
            type="button"
            onClick={() => setAuthOpen((v2) => !v2)}
            title={v.tip || undefined}
            style={{ display: "inline-flex", alignItems: "center", gap: 5, flex: "0 0 auto", padding: "2px 9px", borderRadius: 999, fontSize: "0.8rem", lineHeight: 1.3, color: v.color, background: v.bg, border: `1px solid ${v.border}`, cursor: "pointer", whiteSpace: "nowrap" }}
          >
            <span>{v.icon}</span>
            <strong>{v.short}</strong>
            <span aria-hidden style={{ opacity: 0.8 }}>{authOpen ? "▴" : "▾"}</span>
          </button>
        )}
        {imagesBlocked && (
          <button className="ghost" style={{ flex: "0 0 auto", whiteSpace: "nowrap" }} title={t("mail.imagesBlocked")} onClick={() => setShowImages(true)}>
            🛡 {t("mail.showImages")}
          </button>
        )}
        {msg.html && (
          <button
            className="ghost"
            style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}
            title={darkBody ? (de ? "Original-Farben der Mail anzeigen" : "Show original colors") : (de ? "Helle Mail dunkel einfärben" : "Tint light mail dark")}
            onClick={() => setDarkBody((v) => !v)}
          >
            {darkBody ? (de ? "☀️ Original" : "☀️ Original") : (de ? "🌙 Dunkel" : "🌙 Dark")}
          </button>
        )}
        {translateEnabled && (
          <button
            className="ghost"
            style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}
            disabled={translating}
            title={de ? "Mail nach Deutsch übersetzen" : "Translate to German"}
            onClick={() => doTranslate(msg)}
          >
            {translating ? "⏳" : "🌐"} {translated != null ? (de ? "Original" : "Original") : (de ? "Übersetzen" : "Translate")}
          </button>
        )}
      </>
    );
  }

  // Übersetzungs-Panel (über dem Mailtext), wenn eine Übersetzung vorliegt.
  function translatePanel(): ReactNode {
    if (translated == null) return null;
    return (
      <div style={{ margin: "0 0 10px", padding: "10px 12px", borderRadius: 8, background: "var(--self-bg-3)", border: "1px solid var(--self-line)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
        <div style={{ fontSize: "0.75rem", color: "var(--self-text-3)", marginBottom: 6 }}>🌐 {de ? "Übersetzung (Deutsch)" : "Translation (German)"}</div>
        {translated}
      </div>
    );
  }
  // Ausgeklappter Volltext zum Echtheits-Chip (Warnung/Bestätigung + SPF/DKIM/DMARC).
  function authDetail(msg: MsgDetail): ReactNode {
    if (!authOpen || !msg.auth) return null;
    const v = authView(msg.auth, de);
    return (
      <div
        title={v.tip || undefined}
        style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, margin: "8px 0 0", padding: "6px 10px", borderRadius: 8, fontSize: "0.8rem", lineHeight: 1.4, color: v.color, background: v.bg, border: `1px solid ${v.border}` }}
      >
        <span style={{ flexShrink: 0 }}>{v.icon}</span>
        <strong>{v.text}</strong>
        {v.chips && <span style={{ opacity: 0.75 }}>· {v.chips}</span>}
      </div>
    );
  }

  async function sendReadReceipt(msg: MsgDetail) {
    if (activeId == null || mdnBusy) return;
    setMdnBusy(true);
    try {
      await api.post(`/mail/${activeId}/messages/${msg.uid}/read-receipt?folder=${encodeURIComponent(folder)}`);
      setMdnState((s) => ({ ...s, [msg.uid]: "sent" }));
    } catch (e) {
      setErr((e as Error).message || t("mail.mdnError"));
    } finally {
      setMdnBusy(false);
    }
  }

  // Hinweisleiste, wenn der Absender eine Lesebestätigung angefordert hat.
  function mdnBanner(msg: MsgDetail): ReactNode {
    const addr = (msg.mdn_request || "").trim();
    if (!addr) return null;
    const state = mdnState[msg.uid];
    if (state === "hidden") return null;
    // Nicht bei selbst gesendeten Mails (Gesendet/Entwürfe) nachfragen.
    const own = accountById(activeId ?? -1)?.email?.toLowerCase() || "";
    if (own && parseAddr(msg.from).email.toLowerCase() === own) return null;
    if (state === "sent") {
      return (
        <div style={{ margin: "8px 0 0", padding: "6px 10px", borderRadius: 8, fontSize: "0.8rem", color: "var(--self-text)", background: "rgba(20,184,166,0.12)", border: "1px solid var(--self-line)" }}>
          ✓ {t("mail.mdnSent")}
        </div>
      );
    }
    return (
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, margin: "8px 0 0", padding: "6px 10px", borderRadius: 8, fontSize: "0.8rem", lineHeight: 1.4, color: "var(--self-text)", background: "rgba(234,179,8,0.12)", border: "1px solid rgba(234,179,8,0.4)" }}>
        <span style={{ flexShrink: 0 }} aria-hidden>📩</span>
        <span style={{ flex: 1, minWidth: 140 }}>{t("mail.mdnPrompt")}</span>
        <button className="primary" style={{ width: "auto", padding: "2px 10px" }} disabled={mdnBusy} onClick={() => sendReadReceipt(msg)}>{t("mail.mdnSend")}</button>
        <button className="ghost" style={{ width: "auto", padding: "2px 10px" }} disabled={mdnBusy} onClick={() => setMdnState((s) => ({ ...s, [msg.uid]: "hidden" }))}>{t("mail.mdnIgnore")}</button>
      </div>
    );
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
                        {unseenOf(a.id, p) > 0 && <span className="mail-badge">{unseenOf(a.id, p)}</span>}
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
                  : <button key={p} className={`pgbtn ${p === page ? "active" : ""}`} aria-current={p === page ? "page" : undefined} disabled={loadingMore} onClick={() => goPage(p)}>{p}</button>,
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
              {/* Ordnerweites "Alle auswählen" NUR ohne Suche — bei Suche soll
                  "Alle auswählen" ausschließlich die sichtbaren Treffer markieren. */}
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
                <div className="grow" role="button" tabIndex={0} style={{ cursor: "pointer", overflow: "hidden", minWidth: 0 }} onClick={() => openMsg(m.uid)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openMsg(m.uid); } }} onDoubleClick={() => openMsg(m.uid, true)} onMouseEnter={() => prefetchMsg(m.uid)}>
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
                  {spamFolder && folder !== spamFolder && (
                    <button className="icon-btn" onClick={() => moveMsg(open.uid, spamFolder)} title={t("mail.spam")}>🚫</button>
                  )}
                  <button className="icon-btn read-del" onClick={() => del(open)} title={t("mail.delete")}>🗑</button>
                  <button className={`icon-btn ${readMenu ? "on" : ""}`} onClick={() => setReadMenu((v) => !v)} title={t("mail.more")}>⋯</button>
                  <button className="icon-btn" onClick={() => { setOpen(null); setMobilePane("list"); }} title={t("mail.back")}>✕</button>
                  {readMenu && (
                    <>
                      <div className="menu-backdrop" onClick={() => setReadMenu(false)} />
                      <div className="read-menu">
                        <button onClick={() => { saveSenderAsContact(); setReadMenu(false); }} disabled={contactSaved}>
                          {contactSaved ? "✓" : "👤"} {contactSaved ? t("mail.contactSaved") : t("mail.addContact")}
                        </button>
                        <button onClick={() => { markUnread(open.uid); setReadMenu(false); }}>● {t("mail.markUnread")}</button>
                        <button className="read-menu-danger" onClick={() => { setReadMenu(false); blockSender(); }}>🚫 {t("mail.blockSender")}</button>
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
                        <button onClick={() => { setReadMenu(false); showRaw(open.uid); }}>📄 {de ? "Original anzeigen" : "View source"}</button>
                      </div>
                    </>
                  )}
                </div>
              </div>
              <div className="mail-head-meta">
                <button className="mail-star" onClick={() => toggleFlag(open)} title={t("mail.flag")}>
                  {(messages.find((m) => m.uid === open.uid)?.flagged ?? open.flagged) ? "★" : "☆"}
                </button>
                {(() => {
                  const f = parseAddr(open.from);
                  // Hat der Absender keinen echten Anzeigenamen, liefert parseAddr Name == Adresse.
                  // Dann nur EINMAL die Adresse zeigen statt „mail@x <mail@x>".
                  const sameAddr = f.name.trim().toLowerCase() === f.email.trim().toLowerCase();
                  return (
                    <div className="mail-head-from">
                      <span className="mail-head-name">{f.name}</span>
                      {!sameAddr && <span className="mail-head-addr">&lt;{f.email}&gt;</span>}
                    </div>
                  );
                })()}
                {/* Empfängeradresse (oft die eigene) nicht im Kopf zeigen — sie steht
                    in den aufgeklappten Details. Hier nur der Aufklapp-Schalter. */}
                <button className="mail-head-toexp" onClick={() => setDetailsOpen((v) => !v)} title={t("mail.details")}>
                  Details <span aria-hidden>{detailsOpen ? "▴" : "▾"}</span>
                </button>
                {/* Echtheits-Chip + „Bilder anzeigen" direkt neben „Details". */}
                {authCluster(open)}
                <span className="grow" />
                <span className="mail-head-date">{prettyDate(open.date)}</span>
              </div>
              {authDetail(open)}
              {mdnBanner(open)}
              {detailsOpen && (
                <div className="mail-head-details">
                  <div><span className="label">{t("mail.hdrFrom")}</span><span>{open.from}</span></div>
                  <div><span className="label">{t("mail.hdrTo")}</span><span>{open.to.join(", ") || "—"}</span></div>
                  <div><span className="label">{t("mail.hdrDate")}</span><span>{open.date}</span></div>
                </div>
              )}
            </div>
            <hr style={{ borderColor: "var(--self-line)", margin: "0.5rem 0 0.55rem" }} />
            {translatePanel()}
            {open.html ? (
              <iframe title="mail-body" sandbox="allow-popups allow-popups-to-escape-sandbox" className="mail-body-frame"
                srcDoc={buildSrcDoc(open.html, blockImages && !showImages, darkBody)} />
            ) : open.text ? (
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{open.text}</div>
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

      {confirmBox && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="mail-confirm-title"
          onClick={(e) => { if (e.target === e.currentTarget) { confirmBox.resolve(false); setConfirmBox(null); } }}
          style={{ position: "fixed", inset: 0, zIndex: 10060, background: "rgba(0,0,0,0.55)", backdropFilter: "blur(2px)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}
        >
          <div style={{ width: "min(420px, 100%)", background: "var(--self-bg-2)", border: "1px solid var(--self-line)", borderRadius: 14, boxShadow: "0 12px 40px rgba(0,0,0,0.5)", padding: 20 }}>
            <p id="mail-confirm-title" style={{ margin: 0, fontSize: 14, color: "var(--self-text)", lineHeight: 1.55 }}>{confirmBox.message}</p>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
              <button type="button" className="ghost" onClick={() => { confirmBox.resolve(false); setConfirmBox(null); }}>
                {t("common.cancel")}
              </button>
              <button type="button" className="primary" autoFocus onClick={() => { confirmBox.resolve(true); setConfirmBox(null); }}>
                OK
              </button>
            </div>
          </div>
        </div>
      )}

      {rawText !== null && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="mail-rawtext-title"
          onClick={(e) => { if (e.target === e.currentTarget) setRawText(null); }}
          style={{ position: "fixed", inset: 0, zIndex: 10065, background: "rgba(0,0,0,0.6)", backdropFilter: "blur(2px)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}
        >
          <div style={{ width: "min(900px, 100%)", maxHeight: "85vh", background: "var(--self-bg-2)", border: "1px solid var(--self-line)", borderRadius: 12, boxShadow: "0 12px 40px rgba(0,0,0,0.5)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: "1px solid var(--self-line)" }}>
              <strong id="mail-rawtext-title" style={{ fontSize: 14, color: "var(--self-text)" }}>{de ? "Original (Quelltext)" : "Original (source)"}</strong>
              <div style={{ display: "flex", gap: 8 }}>
                <button type="button" className="ghost" onClick={async () => { const ok = await copyText(rawText); setErr(ok ? (de ? "Kopiert ✓" : "Copied ✓") : (de ? "Kopieren fehlgeschlagen" : "Copy failed")); }}>{de ? "Kopieren" : "Copy"}</button>
                <button type="button" className="ghost" onClick={() => setRawText(null)}>{de ? "Schließen" : "Close"}</button>
              </div>
            </div>
            <pre style={{ margin: 0, padding: 16, overflow: "auto", fontSize: 12, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--self-text)", fontFamily: "ui-monospace, Menlo, Consolas, monospace" }}>{rawText}</pre>
          </div>
        </div>
      )}

      {/* Doppelklick-Popup: dieselbe Mail in einem eigenen, zentrierten Fenster. */}
      {popup && open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="mail-popup-title"
          onClick={(e) => { if (e.target === e.currentTarget) setPopup(false); }}
          style={{ position: "fixed", inset: 0, zIndex: 10068, background: "rgba(0,0,0,0.6)", backdropFilter: "blur(2px)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}
        >
          <div style={{ width: "min(900px, 100%)", maxHeight: "88vh", background: "var(--self-bg-2)", border: "1px solid var(--self-line)", borderRadius: 12, boxShadow: "0 12px 40px rgba(0,0,0,0.5)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "14px 16px", borderBottom: "1px solid var(--self-line)" }}>
              <h2 id="mail-popup-title" style={{ flex: 1, minWidth: 0, margin: 0, fontSize: "1.1rem", fontWeight: 700, lineHeight: 1.3, color: "var(--self-text)" }}>{open.subject || t("mail.noSubject")}</h2>
              <div style={{ display: "flex", gap: 6, flex: "0 0 auto" }}>
                <button className="icon-btn" onClick={() => { setDraft(replyDraft(open, t)); setPopup(false); }} title={t("mail.reply")}>↩</button>
                <button className="icon-btn" onClick={() => { setDraft(forwardDraft(open, t)); setPopup(false); }} title={t("mail.forward")}>↪</button>
                <button className="icon-btn" onClick={() => setPopup(false)} title={t("mail.back")}>✕</button>
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 16px", borderBottom: "1px solid var(--self-line)", fontSize: "0.85rem", color: "var(--self-text-2)" }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--self-text)", minWidth: 0 }}>{open.from}</span>
              {/* Echtheits-Chip + „Bilder anzeigen" auch im Popup. */}
              {authCluster(open)}
              <span className="grow" style={{ flex: 1 }} />
              <span style={{ flex: "0 0 auto", color: "var(--self-text-3)" }}>{prettyDate(open.date)}</span>
            </div>
            {authOpen && open.auth && (
              <div style={{ padding: "0 16px" }}>{authDetail(open)}{mdnBanner(open)}</div>
            )}
            <div style={{ overflow: "auto", padding: 16, flex: "1 1 auto", minHeight: 0, display: "flex", flexDirection: "column" }}>
              {translatePanel()}
              {open.html ? (
                <iframe title="mail-body-popup" sandbox="allow-popups allow-popups-to-escape-sandbox" className="mail-body-frame"
                  srcDoc={buildSrcDoc(open.html, blockImages && !showImages, darkBody)} />
              ) : open.text ? (
                <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55, color: "var(--self-text)" }}>{open.text}</div>
              ) : (
                <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55, color: "var(--self-text)" }}>{t("mail.emptyBody")}</div>
              )}
              {open.attachments?.length > 0 && (
                <div style={{ marginTop: "1.2rem", borderTop: "1px solid var(--self-line)", paddingTop: "0.8rem" }}>
                  <div className="label" style={{ marginBottom: "0.5rem" }}>📎 {t("mail.attachments")}</div>
                  <div className="row" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
                    {open.attachments.map((att) => (
                      <button key={att.index} className="ghost" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}
                        onClick={() => download(`/mail/${activeId}/messages/${open.uid}/attachments/${att.index}?folder=${encodeURIComponent(folder)}`).catch((e) => setErr((e as Error).message))}>
                        {att.filename || `att-${att.index}`}{att.size ? <span className="muted" style={{ fontSize: "0.75rem" }}>({fmtSize(att.size)})</span> : null}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

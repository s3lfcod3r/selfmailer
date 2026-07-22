import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, copyText, download, type Account, type MsgHeader, type MsgDetail, type AuthInfo, type TransferResult, type FolderCount, type SearchResult } from "../lib/api";
import { useLang } from "../lib/i18n";
import { promptDialog } from "../lib/dialog";
import { buildFolderTree, specialKind, SPECIAL_ICON, type FolderNode } from "../lib/folders";
import { Compose, emptyDraft, replyDraft, forwardDraft, type Draft } from "../components/Compose";
import { parseAddr, prettyDate, listDate, hasRemoteContent, buildSrcDoc, fmtSize, avatarFor } from "../lib/mailview";
import { ThreadReader } from "../components/ThreadReader";
import { groupThreads, type Conversation } from "../lib/threads";

type Sel = { acc: number; folder: string };

const PAGE_SIZE = 50;  // Mails pro Seite
const SEARCH_LIMIT = 1000;  // Obergrenze der bei aktiver Suche geladenen Mails

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

// Stabile Callback-Referenzen für eine Listenzeile — als eigenes, memoisiertes
// Bündel übergeben, damit React.memo Zeilen überspringen kann, deren eigene
// Daten (m/isSelected/isActive) sich nicht geändert haben.
type RowHandlers = {
  // folder: nur bei Volltext-Treffern gesetzt (Mail liegt woanders als im
  // gerade geöffneten Ordner) — siehe openMsg(uid, asPopup, fromFolder).
  onOpen: (uid: string, folder?: string) => void;
  onOpenPopup: (uid: string, folder?: string) => void;
  onPrefetch: (uid: string) => void;
  onToggleFlag: (m: MsgHeader) => void;
  onToggleSeen: (m: MsgHeader) => void;
  onToggleSelect: (uid: string) => void;
  onDelete: (m: MsgHeader) => void;
  onDragStart: (uid: string) => void;
  onDragEnd: () => void;
};
type RowLabels = { flag: string; noSubject: string; markUnread: string; markRead: string; delete: string };

// Eine Nachrichtenzeile der Liste. React.memo: rendert nur neu, wenn sich die
// eigenen Props ändern — bei hunderten/tausenden Treffern (Suche) reconcilen so
// nicht mehr alle Zeilen bei jeder Interaktion (Auswahl/Öffnen/Sync).
const MailRow = memo(function MailRow({ m, isSelected, isActive, handlers, labels }: {
  m: MsgHeader; isSelected: boolean; isActive: boolean; handlers: RowHandlers; labels: RowLabels;
}) {
  return (
    <div className={`mail-row ${m.seen ? "" : "unseen"}`}
      draggable onDragStart={() => handlers.onDragStart(m.uid)} onDragEnd={handlers.onDragEnd}
      style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", borderColor: isActive ? "var(--self-teal)" : undefined }}>
      <input type="checkbox" checked={isSelected} onChange={() => handlers.onToggleSelect(m.uid)} style={{ flex: "0 0 auto", width: "auto", marginTop: "0.3rem" }} />
      <button className="ghost" style={{ padding: "0 0.1rem", flex: "0 0 auto", color: m.flagged ? "var(--self-cyan, #00e5c8)" : undefined }} onClick={() => handlers.onToggleFlag(m)} title={labels.flag}>
        {m.flagged ? "★" : "☆"}
      </button>
      <div className="grow" role="button" tabIndex={0} style={{ cursor: "pointer", overflow: "hidden", minWidth: 0 }} onClick={() => handlers.onOpen(m.uid, m.folder)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); handlers.onOpen(m.uid, m.folder); } }} onDoubleClick={() => handlers.onOpenPopup(m.uid, m.folder)} onMouseEnter={() => handlers.onPrefetch(m.uid)}>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "baseline" }}>
          <span style={{ flex: 1, minWidth: 0, fontWeight: m.seen ? 400 : 700, color: "var(--self-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "0.9rem" }}>{m.from}</span>
          <span className="muted" style={{ fontSize: "0.72rem", whiteSpace: "nowrap", flex: "0 0 auto" }}>{listDate(m.date)}</span>
        </div>
        <div className="mail-subj" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.subject || labels.noSubject}</span>
          {m.has_attachments && <span style={{ flex: "0 0 auto", fontSize: "0.8rem" }}>📎</span>}
          {/* Bei Volltext-Treffern: aus welchem Ordner stammt die Mail? Ohne das
              wirkt eine Trefferliste über mehrere Ordner zusammenhanglos. */}
          {m.folder && <span className="mail-folder-tag" title={m.folder}>{m.folder.split(/[/.]/).pop()}</span>}
        </div>
        {m.snippet && <div className="muted" style={{ fontSize: "0.78rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.snippet}</div>}
      </div>
      <button className="ghost" style={{ padding: "0 0.2rem", flex: "0 0 auto", color: m.seen ? undefined : "var(--self-unread)", fontSize: m.seen ? undefined : "1.1rem", lineHeight: 1 }} onClick={() => handlers.onToggleSeen(m)} title={m.seen ? labels.markUnread : labels.markRead}>{m.seen ? "○" : "●"}</button>
      <button className="ghost" style={{ padding: "0 0.2rem", flex: "0 0 auto" }} onClick={() => handlers.onDelete(m)} title={labels.delete}>🗑</button>
    </div>
  );
});

// Eine ZUSAMMENGEFASSTE Konversationszeile (mehrere Mails). Zeigt die Teilnehmer,
// den Betreff, die Vorschau der neuesten Mail und eine Zähler-Plakette. Ein Klick
// öffnet den gestapelten Verlauf (ThreadReader).
const ConvRow = memo(function ConvRow({ conv, isSelected, isActive, onOpen, onToggleFlag, onToggleSelect, labels }: {
  conv: Conversation; isSelected: boolean; isActive: boolean;
  onOpen: () => void; onToggleFlag: () => void; onToggleSelect: () => void; labels: RowLabels;
}) {
  const latest = conv.latest;
  // Teilnehmer kompakt: bis zu 3 Namen, sonst "A, B +N".
  const names = conv.fromNames;
  const who = names.length <= 3 ? names.join(", ") : `${names.slice(0, 2).join(", ")} +${names.length - 2}`;
  const av = avatarFor(names[0] || latest.from);
  return (
    <div className={`mail-row conv-row ${conv.anyUnseen ? "unseen" : ""}`}
      style={{ display: "flex", alignItems: "flex-start", gap: "0.5rem", borderColor: isActive ? "var(--self-teal)" : undefined }}>
      <input type="checkbox" checked={isSelected} onChange={onToggleSelect} style={{ flex: "0 0 auto", width: "auto", marginTop: "0.3rem" }} />
      <button className="ghost" style={{ padding: "0 0.1rem", flex: "0 0 auto", color: conv.anyFlagged ? "var(--self-cyan, #00e5c8)" : undefined }} onClick={onToggleFlag} title={labels.flag}>
        {conv.anyFlagged ? "★" : "☆"}
      </button>
      <span className="thread-avatar" aria-hidden style={{ width: 30, height: 30, background: av.color, fontSize: 12, marginTop: "0.1rem", flex: "0 0 auto" }}>{av.initials}</span>
      <div className="grow" role="button" tabIndex={0} style={{ cursor: "pointer", overflow: "hidden", minWidth: 0 }} onClick={onOpen} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "baseline" }}>
          <span style={{ flex: 1, minWidth: 0, fontWeight: conv.anyUnseen ? 700 : 400, color: "var(--self-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: "0.9rem" }}>{who}</span>
          <span className="conv-badge" title={`${conv.count}`}>{conv.count}</span>
          <span className="muted" style={{ fontSize: "0.72rem", whiteSpace: "nowrap", flex: "0 0 auto" }}>{listDate(latest.date)}</span>
        </div>
        <div className="mail-subj" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{latest.subject || labels.noSubject}</span>
          {conv.anyAttachment && <span style={{ flex: "0 0 auto", fontSize: "0.8rem" }}>📎</span>}
        </div>
        {latest.snippet && <div className="muted" style={{ fontSize: "0.78rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{latest.snippet}</div>}
      </div>
    </div>
  );
});

export function Mail({ search = "", filter, pollMin = 5, blockImages = true, darkMail = true, pinFlagged = false, conversationView = false, onUnseenChange }: { search?: string; filter?: MailFilter; pollMin?: number; blockImages?: boolean; darkMail?: boolean; pinFlagged?: boolean; conversationView?: boolean; onUnseenChange?: (total: number) => void }) {
  const { t, lang } = useLang();
  const de = lang === "de";
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [foldersByAcc, setFoldersByAcc] = useState<Record<number, FolderCount[]>>({});
  const [sel, setSel] = useState<Sel | null>(null);
  const [collapsedAcc, setCollapsedAcc] = useState<Set<number>>(() => loadSet("selfmailer.collapsedAcc"));
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [messages, setMessages] = useState<MsgHeader[]>([]);
  const [open, setOpen] = useState<MsgDetail | null>(null);
  const [opening, setOpening] = useState(false); // Body wird geladen → sofort Ladeanzeige
  // Geöffnete Konversation (Thread-Lesebereich). Hat Vorrang vor `open`: ist ein
  // Thread offen, zeigt die Lesespalte den gestapelten Verlauf statt einer Einzelmail.
  const [openThread, setOpenThread] = useState<Conversation | null>(null);
  // Gecachte „Gesendet"-Kopfzeilen je Konto — damit der Listen-Zähler einer
  // Konversation auch die EIGENEN Antworten mitzählt (wie Synology). Wird im
  // Hintergrund geladen, sobald die Konversations-Ansicht aktiv ist.
  const [sentByAcc, setSentByAcc] = useState<Record<number, MsgHeader[]>>({});
  // Suche hat die Obergrenze (SEARCH_LIMIT) erreicht → sichtbarer Hinweis, dass
  // die Trefferliste abgeschnitten ist (sonst wirkt sie irreführend vollständig).
  const [searchTruncated, setSearchTruncated] = useState(false);
  // Volltextsuche (IMAP, serverseitig). null = aus; dann gilt die gewohnte
  // Sofort-Filterung über die geladene Liste. Bewusst getrennt gehalten, statt
  // `messages` zu überschreiben: so bleibt die Ordnerliste im Hintergrund intakt
  // und „Volltext verlassen" braucht kein Neuladen.
  const [ftResults, setFtResults] = useState<MsgHeader[] | null>(null);
  const [ftInfo, setFtInfo] = useState<SearchResult | null>(null);
  const [ftLoading, setFtLoading] = useState(false);
  // Begriff, zu dem die Treffer gehören — tippt der Nutzer weiter, passt die
  // Trefferliste nicht mehr zur Eingabe und wird verworfen.
  const [ftQuery, setFtQuery] = useState("");
  const ftSeqRef = useRef(0);
  // SSE-Live-Verbindung gerade unterbrochen? (nicht fatal — Browser reconnectet)
  const [liveDown, setLiveDown] = useState(false);
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
    // Pointer-Events + setPointerCapture: die Bewegungs-/Loslassen-Events kommen
    // AUCH dann noch am Griff an, wenn der Zeiger über dem Mail-iframe landet.
    // Früher hörte document das mouseup dort nicht mehr → hängengebliebenes
    // Ziehen. pointercancel + Capture-Freigabe als zusätzliche Absicherung.
    return (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      const startX = e.clientX;
      const el = e.currentTarget;
      const pid = e.pointerId;
      try { el.setPointerCapture(pid); } catch { /* ältere Browser: Fallback über Element-Listener */ }
      let last = current;
      function move(ev: PointerEvent) { last = Math.max(min, Math.min(max, current + ev.clientX - startX)); setW(last); }
      function up() {
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        el.removeEventListener("pointercancel", up);
        try { el.releasePointerCapture(pid); } catch { /* egal */ }
        localStorage.setItem(key, String(last));
      }
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
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
  // Ungelesene eines Ordners INKL. aller Unterordner (ausgeblendete/virtuelle
  // Gmail-Label-Ordner zählen wie beim Konto-Rollup nicht mit). Damit kann ein
  // zugeklappter Eltern-Ordner die im Baum sonst unsichtbaren Treffer anzeigen.
  function subtreeUnseen(accId: number, node: FolderNode): number {
    if ((hiddenByAcc[accId] || []).includes(node.path)) return 0;
    const self = VIRTUAL_SPECIAL.has(node.special || "") ? 0 : unseenOf(accId, node.path);
    return node.children.reduce((s, c) => s + subtreeUnseen(accId, c), self);
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
  useEffect(() => { searchActiveRef.current = searchActive; }, [searchActive]);
  // Versionszähler für loadAllForSearch: verwirft veraltete Antworten,
  // wenn während des Ladens erneut (anderer Ordner/Suche) geladen wurde.
  const searchLoadRef = useRef(0);
  // Suchmodus als Ref, damit bgSync ihn kennt: bgSync läuft aus Intervallen/SSE
  // heraus und sähe den State sonst veraltet. Ohne diesen Guard holte bgSync nur
  // EINE Seite (PAGE_SIZE) und überschrieb damit die vollständige Trefferliste —
  // die Suche "verlor" nach ~20 s alle Treffer außerhalb der neuesten Seite.
  const searchActiveRef = useRef(false);
  // Versionszähler für openMsg: ein langsamer erster Klick darf einen
  // schnelleren zweiten (andere Mail) nicht überschreiben. Nur die JÜNGSTE
  // Öffnung darf ihren Zustand setzen (siehe isLatest() in openMsg).
  const openSeqRef = useRef(0);
  // Versionszähler fürs ordnerübergreifende Nachladen eines Threads (Gesendet-
  // Antworten): eine langsame Antwort darf einen inzwischen anderen offenen Thread
  // nicht überschreiben.
  const threadReqRef = useRef(0);
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
  // Sortier-Zusatz für alle Listenabrufe: markierte Mails oben anheften.
  // Serverseitig, damit es auch über Seitengrenzen hinweg gilt.
  const pinParam = pinFlagged ? "&pin_flagged=1" : "";
  function fetchPage(acc: number, fol: string, p: number) {
    return api.get<MsgHeader[]>(`/mail/${acc}/messages?folder=${encodeURIComponent(fol)}&limit=${PAGE_SIZE}&offset=${(p - 1) * PAGE_SIZE}${pinParam}`);
  }
  // Holt den ganzen Ordner (bis Cache-Tiefe) für die Suche — von loadAllForSearch
  // und von bgSync im Suchmodus genutzt, damit beide dieselbe Menge liefern.
  function fetchAllForSearch(acc: number, fol: string) {
    return api.get<MsgHeader[]>(`/mail/${acc}/messages?folder=${encodeURIComponent(fol)}&limit=${SEARCH_LIMIT}${pinParam}`);
  }
  // Ordnerwechsel/Neuladen: immer auf Seite 1, Auswahl zurücksetzen.
  function reload() {
    if (!sel) return;
    const acc = sel.acc, fol = sel.folder;
    setLoading(true); setErr(""); setOpen(null); setOpenThread(null); setSelected(new Set()); setSelectAllFolder(false); setPage(1); setSearchTruncated(false);
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
      // Bei aktiver Suche den GANZEN Ordner nachladen (wie loadAllForSearch),
      // sonst nur die sichtbare Seite. Andernfalls ersetzt eine 50er-Seite die
      // vollständige Trefferliste und die Suche wirkt "leergelaufen".
      .then(() => searchActiveRef.current ? fetchAllForSearch(acc, fol) : fetchPage(acc, fol, p))
      .then((ms) => {
        bgSyncFailRef.current.delete(key);  // Erfolg -> Backoff zurücksetzen
        if (!isLatest()) return;  // veraltete Antwort: verwerfen
        if (selRef.current?.acc === acc && selRef.current?.folder === fol) {
          setMessages(ms);
          // Hinweis "nur die ersten N" mitziehen, sonst bleibt er nach einem
          // Sync auf dem alten Stand stehen.
          if (searchActiveRef.current) setSearchTruncated(ms.length >= SEARCH_LIMIT);
        }
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
    setPage(clamped); setOpen(null); setOpenThread(null); setLoadingMore(true);
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
    setLoading(true); setErr(""); setOpen(null); setOpenThread(null); setSelected(new Set()); setSelectAllFolder(false); setSearchTruncated(false);
    fetchAllForSearch(acc, fol)
      .then((ms) => { if (ver !== searchLoadRef.current) return; setMessages(ms); setSearchTruncated(ms.length >= SEARCH_LIMIT); warmBodies(acc, fol, ms); })
      .catch((e) => { if (ver !== searchLoadRef.current) return; setErr((e as Error).message); })
      .finally(() => { if (ver === searchLoadRef.current) setLoading(false); });
  }
  // Volltextsuche über IMAP: durchsucht Kopfzeilen UND Mailtext, standardmäßig
  // in ALLEN Ordnern des Kontos — auch in Mails, die nie im Cache waren.
  // Bewusst nur auf Knopfdruck: ein IMAP-SEARCH über ~35 Ordner dauert Sekunden
  // und würde bei jedem Tastendruck den Mailserver fluten.
  function runFullText() {
    if (!sel) return;
    const q = (search ?? "").trim();
    if (q.length < 2) return;
    const acc = sel.acc;
    const ver = ++ftSeqRef.current;
    setFtLoading(true); setErr(""); setOpen(null);
    setSelected(new Set()); setSelectAllFolder(false);
    api.get<SearchResult>(`/mail/${acc}/search?q=${encodeURIComponent(q)}&folder=${encodeURIComponent(sel.folder)}&all_folders=1`)
      .then((r) => {
        if (ver !== ftSeqRef.current) return;  // überholte Suche: verwerfen
        setFtResults(r.items); setFtInfo(r); setFtQuery(q);
      })
      .catch((e) => { if (ver === ftSeqRef.current) setErr((e as Error).message); })
      .finally(() => { if (ver === ftSeqRef.current) setFtLoading(false); });
  }
  // Volltextmodus verlassen — zurück zur normalen Ordnerliste.
  function clearFullText() {
    ftSeqRef.current++;  // laufende Suche entwerten
    setFtResults(null); setFtInfo(null); setFtQuery(""); setFtLoading(false);
  }
  // Volltext-Treffer verwerfen, sobald sie nicht mehr zur Lage passen:
  // anderer Suchbegriff, geleerte Suche oder Konto-/Ordnerwechsel. Sonst
  // stünden Treffer zu einem alten Begriff über einer neuen Eingabe.
  useEffect(() => {
    if (ftResults === null) return;
    if (!searchActive || (search ?? "").trim() !== ftQuery) clearFullText();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, searchActive, sel?.acc, sel?.folder]);

  // Bei Ordnerwechsel ODER Wechsel Suche an/aus passend laden.
  useEffect(() => {
    if (!sel) return;
    // Vorlade-Merker beim Ordner-/Kontowechsel leeren — sonst wächst das Set in
    // langen Sitzungen unbegrenzt (Keys enthalten ohnehin Konto+Ordner).
    prefetchedRef.current.clear();
    if (searchActive) loadAllForSearch(); else reload();
    // pinFlagged mit in den Deps: der Schalter ändert die SERVER-Sortierung,
    // die Liste muss also neu geholt werden (Umsortieren im Client reicht nicht,
    // weil dabei Mails von anderen Seiten nach vorn rutschen können).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel?.acc, sel?.folder, searchActive, pinFlagged]);

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
    const src = (msg.text && msg.text.trim()) ? msg.text : (msg.html || "").replace(/<[^>]+>/g, " ");
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
    // Verbindung offen → evtl. gezeigten „pausiert"-Hinweis entfernen.
    es.onopen = () => setLiveDown(false);
    // Fehler ist NICHT fatal: der Browser reconnectet automatisch. Nur ein
    // dezenter, transienter Hinweis, dass Live-Updates gerade pausieren.
    es.onerror = () => setLiveDown(true);
    es.onmessage = (e) => {
      setLiveDown(false);
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
  // Konversations-Ansicht ausgeschaltet -> offenen Thread schließen (sonst bliebe
  // die Lesespalte an einem Thread hängen, den es in der flachen Liste nicht gibt).
  useEffect(() => { if (!conversationView) setOpenThread(null); }, [conversationView]);
  // „Gesendet"-Kopfzeilen des aktiven Kontos im Hintergrund laden (einmal je Konto),
  // damit die Listen-Konversationen die eigenen Antworten mitzählen. Der Ordner wird
  // über die special-Kennung (bzw. Namensheuristik) gefunden.
  useEffect(() => {
    if (!conversationView || activeId == null) return;
    if (sentByAcc[activeId] !== undefined) return;             // schon geladen
    const fols = foldersByAcc[activeId] || [];
    const sf = fols.find((f) => f.special === "sent")?.name
      ?? fols.map((f) => f.name).find((n) => specialKind(n.split(/[/.]/).pop() || n) === "sent");
    if (!sf) return;                                           // Ordnerliste noch nicht da
    const acc = activeId;
    let alive = true;
    api.get<MsgHeader[]>(`/mail/${acc}/messages?folder=${encodeURIComponent(sf)}&limit=200&offset=0`)
      .then((ms) => { if (alive) setSentByAcc((prev) => ({ ...prev, [acc]: ms.map((m) => ({ ...m, folder: sf })) })); })
      .catch(() => { if (alive) setSentByAcc((prev) => ({ ...prev, [acc]: [] })); });
    return () => { alive = false; };
  }, [conversationView, activeId, foldersByAcc, sentByAcc]);

  function patchHeader(uid: string, patch: Partial<MsgHeader>) {
    setMessages((ms) => ms.map((m) => (m.uid === uid ? { ...m, ...patch } : m)));
  }

  // Gleiche Nachricht? UIDs sind nur INNERHALB eines Ordners eindeutig — daher
  // IMMER Ordner + UID vergleichen (sonst trifft man im Thread die falsche Mail).
  const sameMsg = (a: MsgHeader, b: MsgHeader) => a.uid === b.uid && (a.folder ?? "") === (b.folder ?? "");

  // Konversation öffnen: Einzelmail wie gewohnt (öffnet die Leseansicht), ein
  // echter Thread (mehrere Mails) den gestapelten Verlauf im ThreadReader.
  // Beim Thread wird zusätzlich ordnerübergreifend nachgeladen (Gesendet-
  // Antworten einweben) — das läuft ASYNCHRON, der Thread erscheint sofort.
  function openConversation(conv: Conversation) {
    if (conv.count <= 1) {
      setOpenThread(null);
      openMsg(conv.latest.uid, false, conv.latest.folder);
      return;
    }
    setOpen(null);
    setOpenThread(conv);
    setMobilePane("read");
    augmentThread(conv);
  }

  // Ordnerübergreifende Nachlade-Logik: holt alle Thread-Mails (inkl. Gesendet),
  // führt sie mit den bereits geladenen zusammen und ersetzt den offenen Thread.
  function augmentThread(conv: Conversation) {
    if (activeId == null) return;
    const acc = activeId;
    const req = ++threadReqRef.current;
    const fol = conv.latest.folder || folder;
    api.get<MsgHeader[]>(`/mail/${acc}/thread?folder=${encodeURIComponent(fol)}&uid=${encodeURIComponent(conv.latest.uid)}`)
      .then((extra) => {
        if (req !== threadReqRef.current) return;          // ein anderer Thread ist inzwischen offen
        if (!extra || extra.length === 0) return;
        const merged = mergeThread(conv.messages, extra);
        if (merged.length <= conv.messages.length) return; // nichts Neues dazugekommen
        const groups = groupThreads(merged);
        const g = groups.find((c) => c.messages.some((m) => sameMsg(m, conv.latest))) ?? groups[0];
        if (g) setOpenThread(g);
      })
      .catch(() => { /* Nachladen ist Komfort — Thread bleibt mit Ordner-Mails bestehen */ });
  }

  // Zwei Nachrichtenlisten vereinen (Duplikate raus: gleiche Ordner+UID ODER
  // gleiche Message-ID — dieselbe Mail kann in zwei Ordnern liegen).
  function mergeThread(base: MsgHeader[], extra: MsgHeader[]): MsgHeader[] {
    const out = [...base];
    const keys = new Set(base.map((m) => `${m.folder ?? ""}:${m.uid}`));
    const mids = new Set(base.map((m) => (m.message_id || "").trim()).filter(Boolean));
    for (const m of extra) {
      const k = `${m.folder ?? ""}:${m.uid}`;
      const mid = (m.message_id || "").trim();
      if (keys.has(k) || (mid && mids.has(mid))) continue;
      out.push(m); keys.add(k); if (mid) mids.add(mid);
    }
    return out;
  }

  // Eine Thread-Nachricht patchen: im offenen Thread UND (wenn sie im aktuellen
  // Ordner liegt) in der Listen-`messages`, damit beide konsistent bleiben.
  function patchThreadMsg(target: MsgHeader, patch: Partial<MsgHeader>) {
    setOpenThread((prev) => prev
      ? { ...prev, messages: prev.messages.map((x) => (sameMsg(x, target) ? { ...x, ...patch } : x)) }
      : prev);
    if ((target.folder ?? folder) === folder) patchHeader(target.uid, patch);
  }

  // Eine (vorher ungelesene) Thread-Nachricht wurde geöffnet: Flag serverseitig
  // setzen, Kopf patchen und den Ungelesen-Zähler des RICHTIGEN Ordners anpassen.
  function markThreadSeen(m: MsgHeader) {
    if (activeId == null || m.seen) return;
    const fol = m.folder || folder;
    api.post(`/mail/${activeId}/messages/${m.uid}/flags?folder=${encodeURIComponent(fol)}&seen=true`).catch(() => {});
    patchThreadMsg(m, { seen: true });
    bumpUnseen(activeId, fol, -1);
  }

  // Stern einer Thread-Nachricht umschalten (ordner-bewusst).
  function flagThreadMsg(m: MsgHeader) {
    if (activeId == null) return;
    const fol = m.folder || folder;
    const next = !m.flagged;
    patchThreadMsg(m, { flagged: next });
    api.post(`/mail/${activeId}/messages/${m.uid}/flags?folder=${encodeURIComponent(fol)}&flagged=${next}`)
      .catch((e) => { patchThreadMsg(m, { flagged: m.flagged }); setErr((e as Error).message); });
  }

  // Eine einzelne Thread-Nachricht löschen (im RICHTIGEN Ordner — UIDs sind nur
  // ordnerintern eindeutig, ein hartkodierter Ordner würde die falsche Mail treffen).
  async function delThreadMsg(m: MsgHeader) {
    if (activeId == null) return;
    const fol = m.folder || folder;
    if (!(await askConfirm(t("mail.confirmDelete")))) return;
    setErr("");
    try {
      await api.del(`/mail/${activeId}/messages/${m.uid}?folder=${encodeURIComponent(fol)}`);
      if (fol === folder) {
        setMessages((ms) => ms.filter((x) => x.uid !== m.uid));
        if (!m.seen) bumpUnseen(activeId, fol, -1);
      }
      setOpenThread((prev) => {
        if (!prev) return prev;
        const rest = prev.messages.filter((x) => !sameMsg(x, m));
        if (rest.length === 0) { setMobilePane("list"); return null; }  // letzte Mail weg → Thread schließen
        return { ...prev, messages: rest, count: rest.length };
      });
    } catch (e) { setErr((e as Error).message); }
  }

  async function openMsg(uid: string, asPopup = false, fromFolder?: string) {
    if (activeId == null) return;
    // Diese Öffnung als jüngste markieren; Konto/Ordner beim Klick festhalten.
    const ver = ++openSeqRef.current;
    // fromFolder: Volltext-Treffer können in einem ANDEREN Ordner liegen als dem
    // gerade geöffneten — dann muss die Mail von dort geladen werden.
    const acc = activeId, fol = fromFolder || folder;
    const crossFolder = !!fromFolder && fromFolder !== folder;
    // Darf diese (evtl. langsame) Antwort noch Zustand setzen? Nur wenn sie die
    // JÜNGSTE Öffnung ist UND der Nutzer noch im selben Konto/Ordner steht.
    // Bei ordnerfremden Treffern entfällt die Ordner-Prüfung — sonst würde die
    // Antwort immer verworfen, weil der ausgewählte Ordner ein anderer ist.
    const isLatest = () =>
      ver === openSeqRef.current &&
      selRef.current?.acc === acc &&
      (crossFolder || selRef.current?.folder === fol);
    setErr("");
    // SOFORT Feedback: Leseansicht + Ladeanzeige zeigen, BEVOR der Body geladen
    // wird. Sonst passiert bei kaltem (nicht vorgeladenem) Body sichtbar nichts,
    // bis der Live-Abruf durch ist → fühlt sich an wie „öffnet nicht".
    if (!asPopup) { setOpening(true); setMobilePane("read"); setOpenThread(null); }
    setReadMenu(false);
    try {
      const msg = await api.get<MsgDetail>(`/mail/${acc}/messages/${uid}?folder=${encodeURIComponent(fol)}`);
      if (!isLatest()) return;  // veraltete/überholte Öffnung: nichts setzen
      const lastPart = fol.split(/[/.]/).pop() || fol;
      if (specialKind(lastPart) === "drafts") {
        setDraft({ to: (msg.to ?? []).join(", "), cc: "", bcc: "", subject: msg.subject, body: msg.text || (msg.html || "").replace(/<[^>]+>/g, ""), in_reply_to: "" });
        setMobilePane("list");
        return;
      }
      setOpen(msg);
      setPopup(asPopup);
      setMobilePane("read");
      setDetailsOpen(false);
      setAuthOpen(false);
      setShowImages(false);
      setTranslated(null);
      setDarkBody(darkMail);
      if (!msg.seen) {
        api.post(`/mail/${acc}/messages/${uid}/flags?folder=${encodeURIComponent(fol)}&seen=true`).catch(() => {});
        patchHeader(uid, { seen: true });
        bumpUnseen(acc, fol, -1);
      }
    } catch (e) {
      // Mail serverseitig weg (Cache war kurz veraltet): Zeile entfernen, klare
      // Meldung statt rohem Fehler, und still neu synchronisieren (selbstheilend).
      if (isNotFound(e)) {
        setMessages((ms) => ms.filter((x) => x.uid !== uid));
        prefetchedRef.current.delete(`${acc}:${fol}:${uid}`);
        setOpen((o) => (o?.uid === uid ? null : o));
        if (isLatest()) setErr(t("mail.gone"));
        if (selRef.current) bgSync(selRef.current.acc, selRef.current.folder, pageRef.current);
      } else if (isLatest()) {
        setErr((e as Error).message || "");
      }
      if (isLatest() && !open) setMobilePane("list"); // nichts offen → zurück zur Liste (mobil)
    } finally {
      // Ladeanzeige nur dann beenden, wenn KEINE jüngere Öffnung mehr läuft
      // (sonst würde ein veralteter Aufruf den Spinner der aktuellen abwürgen).
      if (ver === openSeqRef.current) setOpening(false);
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
          setOpen((o) => (o?.uid === uid ? null : o));
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
  // Konversation als Ganzes an-/abwählen (alle enthaltenen UIDs).
  function toggleConvSelect(conv: Conversation) {
    const uids = conv.messages.map((m) => m.uid);
    const allSel = uids.every((u) => selected.has(u));
    setSelectAllFolder(false);
    setSelected((s) => {
      const n = new Set(s);
      for (const u of uids) { if (allSel) n.delete(u); else n.add(u); }
      return n;
    });
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
    // Zugeklappter Eltern-Ordner: Treffer der (unsichtbaren) Unterordner
    // mitzählen, damit die Konto-Zahl im Baum auffindbar bleibt.
    const unseen = hasKids && !isOpen ? subtreeUnseen(accId, node) : unseenOf(accId, node.path);
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

  // Sichtbare Zeilen (Suche + Filter) memoisieren — bei aktiver Suche kann die
  // Rohliste sehr lang sein; ohne Memo würde bei jeder Interaktion neu gefiltert.
  // MUSS vor dem Early-Return unten stehen (Regeln der Hooks).
  // Im Volltextmodus kommt die Liste vom Server. Der Textfilter wird dann
  // ÜBERSPRUNGEN: der Server hat im ganzen Mailtext gesucht, der Client kennt
  // aber nur 160 Zeichen Vorschau — er würde genau die Treffer wegwerfen, deren
  // Fundstelle weiter unten im Text liegt. Die übrigen Filter (ungelesen,
  // markiert, Anhang, Zeitraum) bleiben aktiv, die kann der Client beurteilen.
  const visible = useMemo(() => (ftResults ?? messages)
    .filter((m) => ftResults !== null || !search || `${m.subject} ${m.from} ${m.snippet}`.toLowerCase().includes(search.toLowerCase()))
    .filter((m) => !filter?.from || m.from.toLowerCase().includes(filter.from.toLowerCase()))
    .filter((m) => !filter?.subject || m.subject.toLowerCase().includes(filter.subject.toLowerCase()))
    .filter((m) => !filter?.unread || !m.seen)
    .filter((m) => !filter?.starred || m.flagged)
    .filter((m) => !filter?.attachments || m.has_attachments)
    .filter((m) => inDateRange(m.date, filter?.dateFrom, filter?.dateTo)),
    [messages, ftResults, search, filter]);

  // Eigene Absenderadressen (alle Konten) — für „Ich"-Anzeige. Stabil memoisiert.
  const ownEmails = useMemo(() => accounts.map((a) => a.email).filter(Boolean), [accounts]);
  const meLabel = t("mail.me");

  // Konversations-Ansicht: sichtbare Mails in Threads gruppieren. Bei ausgeschaltetem
  // Schalter leer — dann rendert die Liste wie gewohnt Einzelmails.
  // Zusätzlich werden die gecachten „Gesendet"-Mails eingemischt, damit der Zähler
  // die eigenen Antworten mitzählt — aber NUR Konversationen behalten, die auch eine
  // Mail des aktuellen Ordners enthalten (keine reinen Gesendet-Threads in der INBOX).
  const conversations = useMemo(() => {
    if (!conversationView) return [];
    const sent = sentByAcc[activeId ?? -1] || [];
    let all = visible;
    if (sent.length) {
      const seenKey = new Set<string>();
      const seenMid = new Set<string>();
      all = [];
      for (const m of [...visible, ...sent]) {
        const k = `${m.folder ?? ""}:${m.uid}`;
        const mid = (m.message_id || "").trim();
        if (seenKey.has(k) || (mid && seenMid.has(mid))) continue;
        seenKey.add(k); if (mid) seenMid.add(mid); all.push(m);
      }
    }
    const groups = groupThreads(all, ownEmails, meLabel);
    if (!sent.length) return groups;
    const visKeys = new Set(visible.map((m) => `${m.folder ?? ""}:${m.uid}`));
    return groups.filter((g) => g.messages.some((m) => visKeys.has(`${m.folder ?? ""}:${m.uid}`)));
  }, [conversationView, visible, sentByAcc, activeId, ownEmails, meLabel]);
  // Der offene Thread ist die alleinige Wahrheit für den Lesebereich. Alle
  // Änderungen (gelesen/Stern/löschen) pflegen ihn direkt (patchThreadMsg &Co.).
  // Bewusst NICHT aus `conversations` nachgezogen: die enthalten nur den aktuellen
  // Ordner, das ordnerübergreifende Nachladen (Gesendet) würde sonst verworfen.
  const liveThread = openThread;

  // Stabile Handler- und Label-Bündel für die memoisierten Listenzeilen (MailRow).
  // handlersRef zeigt immer auf die aktuellen Closures (mit frischem State), die
  // nach außen gegebenen Callbacks behalten aber ihre Identität → React.memo
  // kann unveränderte Zeilen überspringen.
  const handlersRef = useRef({ openMsg, prefetchMsg, toggleFlag, toggleSeen, toggleSelect, del, startDrag, setDragUids });
  handlersRef.current = { openMsg, prefetchMsg, toggleFlag, toggleSeen, toggleSelect, del, startDrag, setDragUids };
  const rowHandlers = useMemo<RowHandlers>(() => ({
    onOpen: (uid, fol) => handlersRef.current.openMsg(uid, false, fol),
    onOpenPopup: (uid, fol) => handlersRef.current.openMsg(uid, true, fol),
    onPrefetch: (uid) => handlersRef.current.prefetchMsg(uid),
    onToggleFlag: (m) => handlersRef.current.toggleFlag(m),
    onToggleSeen: (m) => handlersRef.current.toggleSeen(m),
    onToggleSelect: (uid) => handlersRef.current.toggleSelect(uid),
    onDelete: (m) => handlersRef.current.del(m),
    onDragStart: (uid) => handlersRef.current.startDrag(uid),
    onDragEnd: () => handlersRef.current.setDragUids([]),
  }), []);
  const rowLabels = useMemo<RowLabels>(() => ({
    flag: t("mail.flag"), noSubject: t("mail.noSubject"),
    markUnread: t("mail.markUnread"), markRead: t("mail.markRead"), delete: t("mail.delete"),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [lang]);

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
      {liveDown && (
        <div className="muted" style={{ marginBottom: "0.5rem", fontSize: "0.75rem" }}>
          ⚠ {de ? "Live-Updates pausiert – Verbindung wird wiederhergestellt." : "Live updates paused – reconnecting."}
        </div>
      )}

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

        <div className="resize-handle" onPointerDown={startResizeFolders} title={t("mail.resizeHint")} />

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
          {searchActive && searchTruncated && ftResults === null && (
            <div className="muted" style={{ fontSize: "0.75rem", padding: "0.3rem 0.55rem", borderBottom: "1px solid var(--self-line)" }}>
              ⚠ {de ? `Zeige die ersten ${SEARCH_LIMIT} — Suche verfeinern.` : `Showing the first ${SEARCH_LIMIT} — refine your search.`}
            </div>
          )}
          {/* Volltextsuche: die Sofort-Filterung oben sieht nur Betreff, Absender und
              160 Zeichen Vorschau des GELADENEN Ordners. Der Knopf startet die echte
              Suche über IMAP — in allen Ordnern und im vollen Mailtext. */}
          {searchActive && ftResults === null && (
            <div className="mail-ft-bar">
              <span className="muted" style={{ fontSize: "0.75rem", flex: 1, minWidth: 0 }}>
                {de ? "Nur dieser Ordner, ohne Mailtext." : "This folder only, no message body."}
              </span>
              <button className="link-btn" onClick={runFullText} disabled={ftLoading}>
                {ftLoading
                  ? (de ? "🔎 Sucht im ganzen Konto…" : "🔎 Searching whole account…")
                  : (de ? "🔎 Volltext im ganzen Konto" : "🔎 Full text, whole account")}
              </button>
            </div>
          )}
          {ftResults !== null && (
            <div className="mail-ft-bar">
              <span style={{ fontSize: "0.75rem", flex: 1, minWidth: 0 }}>
                🔎 {de
                  ? `${ftResults.length} Volltext-Treffer aus ${ftInfo?.folders_searched ?? 0} Ordnern`
                  : `${ftResults.length} full-text hits from ${ftInfo?.folders_searched ?? 0} folders`}
                {ftInfo?.timed_out && (
                  <span className="muted"> — {de
                    ? `abgebrochen nach ${ftInfo.folders_searched}/${ftInfo.folders_total} Ordnern`
                    : `stopped after ${ftInfo.folders_searched}/${ftInfo.folders_total} folders`}</span>
                )}
                {ftInfo?.truncated && (
                  <span className="muted"> — {de ? "gekürzt, Suche verfeinern" : "truncated, refine search"}</span>
                )}
              </span>
              <button className="link-btn" onClick={clearFullText}>
                {de ? "✕ zurück zum Ordner" : "✕ back to folder"}
              </button>
            </div>
          )}
          <div className="mail-list">
            {/* Ordner MIT in den Schlüssel: UIDs sind nur INNERHALB eines Ordners
                eindeutig — bei ordnerübergreifenden Volltext-Treffern kollidieren
                sie sonst und React zeigt Zeilen doppelt oder gar nicht. */}
            {conversationView
              ? conversations.map((conv) => (
                  conv.count <= 1 ? (
                    <MailRow
                      key={conv.key}
                      m={conv.latest}
                      isSelected={selected.has(conv.latest.uid)}
                      isActive={open?.uid === conv.latest.uid}
                      handlers={rowHandlers}
                      labels={rowLabels}
                    />
                  ) : (
                    <ConvRow
                      key={conv.key}
                      conv={conv}
                      isSelected={conv.messages.every((m) => selected.has(m.uid))}
                      isActive={openThread?.key === conv.key}
                      onOpen={() => openConversation(conv)}
                      onToggleFlag={() => toggleFlag(conv.latest)}
                      onToggleSelect={() => toggleConvSelect(conv)}
                      labels={rowLabels}
                    />
                  )
                ))
              : visible.map((m) => (
                  <MailRow
                    key={`${m.folder ?? ""}:${m.uid}`}
                    m={m}
                    isSelected={selected.has(m.uid)}
                    isActive={open?.uid === m.uid}
                    handlers={rowHandlers}
                    labels={rowLabels}
                  />
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

        <div className="resize-handle" onPointerDown={startResize} title={t("mail.resizeHint")} />

        {/* Lese-Spalte */}
        {liveThread ? (
          <ThreadReader
            key={liveThread.key}
            accountId={activeId ?? 0}
            folder={folder}
            conversation={liveThread}
            blockImages={blockImages}
            darkMail={darkMail}
            ownEmails={ownEmails}
            meLabel={meLabel}
            onClose={() => { setOpenThread(null); setMobilePane("list"); }}
            onReply={(d) => setDraft(replyDraft(d, t))}
            onForward={(d) => setDraft(forwardDraft(d, t))}
            onDelete={(m) => delThreadMsg(m)}
            onFlag={(m) => flagThreadMsg(m)}
            onSeen={(m) => markThreadSeen(m)}
          />
        ) : opening ? (
          <div className="mail-readcol mail-loading"><span className="mail-spinner" aria-hidden /></div>
        ) : open ? (
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
                  <div><span className="label">{t("mail.hdrTo")}</span><span>{(open.to ?? []).join(", ") || "—"}</span></div>
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

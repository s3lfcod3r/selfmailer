// Baut aus flachen IMAP-Ordnernamen einen Hierarchie-Baum und erkennt Sonderordner
// (Posteingang/Entwürfe/Gesendet/Spam/Papierkorb/Archiv) per Namensheuristik (DE+EN).

// Die 6 oberen Sonderordner bilden die einheitliche Gruppe (in dieser Reihenfolge).
// "all" = Gmail „Alle Nachrichten" — eigene Art, wird aber UNTEN bei den normalen
// Ordnern geführt (eigenes Icon), NICHT in der oberen Gruppe.
export type SpecialKind = "inbox" | "drafts" | "sent" | "spam" | "trash" | "archive" | "all";

export type FolderNode = {
  path: string; // voller IMAP-Name (für API-Aufrufe)
  label: string; // Anzeigename = letzter Pfadteil
  children: FolderNode[];
  special: SpecialKind | null;
};

// Minimal-Form eines Ordners, wie buildFolderTree ihn braucht: voller IMAP-Name
// plus optional die vom Backend gelieferte Sonderordner-Art (`special`).
export type FolderLike = { name: string; special?: string };

const SPECIAL_PATTERNS: [SpecialKind, RegExp][] = [
  ["inbox", /^inbox$/i],
  ["drafts", /^(drafts?|entw[uü]rfe?|entwurf)$/i],
  ["sent", /^(sent|sent items|gesendet|gesendete objekte)$/i],
  ["spam", /^(spam|junk|junk[- ]?e-?mail|werbung)$/i],
  ["trash", /^(trash|deleted|deleted items|papierkorb|gel[oö]schte? objekte)$/i],
  ["archive", /^(archive|archiv|archiviert)$/i],
];

// Reihenfolge der Sonderordner in der Sidebar (Posteingang zuerst). "all" gehört
// NICHT in die obere Gruppe -> hoher Wert, damit es bei den normalen Ordnern landet.
export const SPECIAL_ORDER: Record<SpecialKind, number> = {
  inbox: 0, drafts: 1, sent: 2, spam: 3, trash: 4, archive: 5, all: 100,
};

export const SPECIAL_ICON: Record<SpecialKind, string> = {
  inbox: "📥", drafts: "📝", sent: "📤", spam: "🚫", trash: "🗑", archive: "📦", all: "📚",
};

// Namens-Heuristik (Fallback, wenn das Backend kein `special` liefert). Sie kennt
// "all" NICHT — diese Art kann nur das Backend setzen.
export function specialKind(lastPart: string): SpecialKind | null {
  for (const [kind, re] of SPECIAL_PATTERNS) if (re.test(lastPart)) return kind;
  return null;
}

// Backend-`special`-String auf eine bekannte SpecialKind abbilden (oder null).
// "noselect" wird hier NICHT abgebildet (das ist ein Container, kein Sonderordner).
const BACKEND_SPECIAL_KINDS: ReadonlySet<string> = new Set([
  "inbox", "drafts", "sent", "spam", "trash", "archive", "all",
]);
function specialFromBackend(special?: string): SpecialKind | null {
  return special && BACKEND_SPECIAL_KINDS.has(special) ? (special as SpecialKind) : null;
}

// Provider-„Reste", die komplett aus der Sidebar fliegen (Regel 1): Gmail
// „Alle Nachrichten" (all), „Markiert" (flagged) und „Wichtig" (important).
// Diese sind keine echten Postfach-Ordner, sondern virtuelle Sichten; das Backend
// liefert sie via SPECIAL-USE. Server ohne SPECIAL-USE setzen sie nie -> kein Risiko.
const RESIDUAL_BACKEND_SPECIALS: ReadonlySet<string> = new Set([
  "all", "flagged", "important",
]);

// Provider-„Reste" per Anzeigename (Regel 2): web.de „Postausgang" & Co. Solche
// Outbox-Ordner sind im Webmailer nutzlos und werden ausgeblendet.
const OUTBOX_NAME = /^(postausgang|outbox|ausgang|unsent( messages?)?|postausgangskorb)$/i;

// Entscheidet, ob ein Wurzel-Ordner ein auszublendender Provider-Rest ist
// (Regel 1: Backend-`special` ∈ all/flagged/important, oder Regel 2: Outbox-Name).
export function isProviderResidual(node: FolderNode, rawSpecial?: string): boolean {
  if (rawSpecial && RESIDUAL_BACKEND_SPECIALS.has(rawSpecial)) return true;
  return OUTBOX_NAME.test(node.label);
}

// Kanonischer (Provider-Standard-)Name je Sonderordner-Art. Manche Server haben
// mehrere Ordner derselben Art — z. B. web.de bringt sowohl "Entwürfe" (echter
// Drafts-Ordner) als auch einen leeren "Entwurf" mit. Ohne Dedup würden BEIDE als
// lokalisiertes "Entwürfe" erscheinen (verwirrendes Doppel). Es gilt nur der
// kanonische als Sonderordner; die übrigen behalten ihren echten Namen.
const CANONICAL_NAMES: Record<SpecialKind, RegExp> = {
  inbox: /^inbox$/i,
  drafts: /^(drafts|entw[uü]rfe)$/i,
  sent: /^(sent|sent items|gesendet|gesendete objekte)$/i,
  spam: /^(spam|junk|junk[- ]?e-?mail)$/i,
  trash: /^(trash|deleted items|papierkorb)$/i,
  archive: /^(archive|archiv)$/i,
  // "all" kommt nur vom Backend und kann nicht heuristisch doppelt entstehen;
  // ein nie matchender Eintrag hält nur den Record-Typ vollständig.
  all: /(?!)/,
};

// Behält je Sonderordner-Art nur EINEN (den kanonischen); weitere Duplikate werden
// AUSGEBLENDET (nicht als normaler Ordner gezeigt). Kanonischer Ordner = exakter
// Standardname, sonst der erste. Deckt z. B. web.de „Entwurf" (2. drafts neben
// „Entwürfe") ab. Liefert die auszublendenden Duplikat-Knoten zurück.
function dedupeSpecialKinds(roots: FolderNode[]): Set<FolderNode> {
  const hidden = new Set<FolderNode>();
  const byKind = new Map<SpecialKind, FolderNode[]>();
  for (const r of roots) {
    if (!r.special) continue;
    const arr = byKind.get(r.special);
    if (arr) arr.push(r);
    else byKind.set(r.special, [r]);
  }
  for (const [kind, nodes] of byKind) {
    if (nodes.length < 2) continue;
    const canon = nodes.find((n) => CANONICAL_NAMES[kind].test(n.label)) ?? nodes[0];
    for (const n of nodes) if (n !== canon) hidden.add(n);
  }
  return hidden;
}

// Erkennt das Hierarchie-Trennzeichen: "/" (Gmail/Dovecot) oder "." (INBOX.Sent bei vielen Servern).
function detectDelimiter(names: string[]): string {
  if (names.some((n) => n.includes("/"))) return "/";
  // "." ist das Trennzeichen, wenn entweder ein INBOX.x-Ordner existiert ODER
  // ein "x.y"-Ordner, dessen Punkt-Elternteil "x" ebenfalls ein Ordner ist
  // (z. B. eigener Top-Level "Ordner" + Unterordner "Ordner.Synology"). Ohne
  // diese zweite Bedingung wurden eigene Unterordner nicht geschachtelt.
  const set = new Set(names);
  const dotNested = names.some((n) => {
    const i = n.indexOf(".");
    return i > 0 && set.has(n.slice(0, i));
  });
  if (dotNested || names.some((n) => /^INBOX\./i.test(n))) return ".";
  return "/";
}

export function buildFolderTree(folders: FolderLike[]): FolderNode[] {
  const names = folders.map((f) => f.name);
  const delim = detectDelimiter(names);
  // Backend-`special` je voller IMAP-Name (zum provider-einheitlichen Erkennen).
  const backendSpecial = new Map<string, string>();
  for (const f of folders) if (f.special) backendSpecial.set(f.name, f.special);

  let roots: FolderNode[] = [];
  const byPath = new Map<string, FolderNode>();
  // Nicht-selektierbare Container (Gmail „[Gmail]") merken, um sie später
  // aufzulösen (ihre Kinder werden auf die Wurzel-Ebene hochgezogen).
  const containers: FolderNode[] = [];

  // Eltern vor Kindern einsortieren (nach Tiefe, dann alphabetisch).
  const sorted = [...names].sort(
    (a, b) => a.split(delim).length - b.split(delim).length || a.localeCompare(b),
  );

  for (const name of sorted) {
    const parts = name.split(delim);
    const last = parts[parts.length - 1] || name;
    const raw = backendSpecial.get(name);
    const isContainer = raw === "noselect";
    // Backend-`special` bevorzugen; nur wenn leer/unbekannt, Namens-Heuristik.
    // Container (noselect) sind selbst kein Sonderordner (special = null).
    const special = isContainer ? null : (specialFromBackend(raw) ?? specialKind(last));
    const node: FolderNode = { path: name, label: last, children: [], special };
    byPath.set(name, node);
    if (isContainer) containers.push(node);
    if (parts.length === 1) {
      roots.push(node);
    } else {
      const parent = byPath.get(parts.slice(0, -1).join(delim));
      if (parent) parent.children.push(node);
      else roots.push(node); // verwaister Knoten ohne sichtbares Elternteil
    }
  }

  // Nicht-selektierbare Container (Gmail „[Gmail]") auflisten: der Container selbst
  // verschwindet, seine Kinder rücken auf die Wurzel-Ebene (so wird aus
  // „[Gmail]/Gesendet" ein Wurzel-„Gesendet" → landet oben in der Sondergruppe).
  if (containers.length) {
    const drop = new Set(containers);
    for (const c of containers) {
      roots.push(...c.children); // Kinder hochziehen
      c.children = [];
    }
    roots = roots.filter((r) => !drop.has(r)); // Container-Knoten selbst entfernen
  }

  // Sonderordner, die technisch als INBOX-Unterordner liegen (web.de: INBOX.Sent …),
  // in der Anzeige als eigenständige Top-Level-Ordner behandeln (wie gängige Clients).
  const inbox = roots.find((r) => r.special === "inbox");
  if (inbox) {
    const promoted = inbox.children.filter((c) => c.special && c.special !== "inbox");
    if (promoted.length) {
      inbox.children = inbox.children.filter((c) => !promoted.includes(c));
      roots.push(...promoted);
    }
  }

  // Provider-„Reste" (Regel 1+2) komplett aus der Sidebar entfernen: Gmail
  // all/flagged/important sowie Outbox-artige Ordner (web.de „Postausgang").
  roots = roots.filter((r) => !isProviderResidual(r, backendSpecial.get(r.path)));

  // Mehrfache Sonderordner derselben Art (Regel 3): nur der kanonische bleibt,
  // die übrigen Duplikate werden ausgeblendet (nicht als normaler Ordner gezeigt).
  const dupes = dedupeSpecialKinds(roots);
  if (dupes.size) roots = roots.filter((r) => !dupes.has(r));

  roots.sort((a, b) => {
    const oa = a.special ? SPECIAL_ORDER[a.special] : 100;
    const ob = b.special ? SPECIAL_ORDER[b.special] : 100;
    return oa - ob || a.label.localeCompare(b.label);
  });
  return roots;
}

// Baut aus flachen IMAP-Ordnernamen einen Hierarchie-Baum und erkennt Sonderordner
// (Posteingang/Entwürfe/Gesendet/Spam/Papierkorb/Archiv) per Namensheuristik (DE+EN).

export type SpecialKind = "inbox" | "drafts" | "sent" | "spam" | "trash" | "archive";

export type FolderNode = {
  path: string; // voller IMAP-Name (für API-Aufrufe)
  label: string; // Anzeigename = letzter Pfadteil
  children: FolderNode[];
  special: SpecialKind | null;
};

const SPECIAL_PATTERNS: [SpecialKind, RegExp][] = [
  ["inbox", /^inbox$/i],
  ["drafts", /^(drafts?|entw[uü]rfe?|entwurf)$/i],
  ["sent", /^(sent|sent items|gesendet|gesendete objekte)$/i],
  ["spam", /^(spam|junk|junk[- ]?e-?mail|werbung)$/i],
  ["trash", /^(trash|deleted|deleted items|papierkorb|gel[oö]schte? objekte)$/i],
  ["archive", /^(archive|archiv|archiviert)$/i],
];

// Reihenfolge der Sonderordner in der Sidebar (Posteingang zuerst).
export const SPECIAL_ORDER: Record<SpecialKind, number> = {
  inbox: 0, drafts: 1, sent: 2, spam: 3, trash: 4, archive: 5,
};

export const SPECIAL_ICON: Record<SpecialKind, string> = {
  inbox: "📥", drafts: "📝", sent: "📤", spam: "🚫", trash: "🗑", archive: "📦",
};

export function specialKind(lastPart: string): SpecialKind | null {
  for (const [kind, re] of SPECIAL_PATTERNS) if (re.test(lastPart)) return kind;
  return null;
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
};

// Behält je Sonderordner-Art nur EINEN als special; weitere werden zu normalen
// Ordnern (mit echtem Namen + Lösch-/Umbenennen-Optionen). Kanonischer Ordner =
// exakter Standardname, sonst der erste.
function dedupeSpecialKinds(roots: FolderNode[]): void {
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
    for (const n of nodes) if (n !== canon) n.special = null;
  }
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

export function buildFolderTree(names: string[]): FolderNode[] {
  const delim = detectDelimiter(names);
  const roots: FolderNode[] = [];
  const byPath = new Map<string, FolderNode>();

  // Eltern vor Kindern einsortieren (nach Tiefe, dann alphabetisch).
  const sorted = [...names].sort(
    (a, b) => a.split(delim).length - b.split(delim).length || a.localeCompare(b),
  );

  for (const name of sorted) {
    const parts = name.split(delim);
    const last = parts[parts.length - 1] || name;
    const node: FolderNode = { path: name, label: last, children: [], special: specialKind(last) };
    byPath.set(name, node);
    if (parts.length === 1) {
      roots.push(node);
    } else {
      const parent = byPath.get(parts.slice(0, -1).join(delim));
      if (parent) parent.children.push(node);
      else roots.push(node); // verwaister Knoten ohne sichtbares Elternteil
    }
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

  // Mehrfache Sonderordner derselben Art entdoppeln (nur einer bleibt special).
  dedupeSpecialKinds(roots);

  roots.sort((a, b) => {
    const oa = a.special ? SPECIAL_ORDER[a.special] : 100;
    const ob = b.special ? SPECIAL_ORDER[b.special] : 100;
    return oa - ob || a.label.localeCompare(b.label);
  });
  return roots;
}

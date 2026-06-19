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

// Erkennt das Hierarchie-Trennzeichen: "/" (Gmail/Dovecot) oder "." (INBOX.Sent bei vielen Servern).
function detectDelimiter(names: string[]): string {
  if (names.some((n) => n.includes("/"))) return "/";
  if (names.some((n) => /^INBOX\./i.test(n))) return ".";
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

  roots.sort((a, b) => {
    const oa = a.special ? SPECIAL_ORDER[a.special] : 100;
    const ob = b.special ? SPECIAL_ORDER[b.special] : 100;
    return oa - ob || a.label.localeCompare(b.label);
  });
  return roots;
}

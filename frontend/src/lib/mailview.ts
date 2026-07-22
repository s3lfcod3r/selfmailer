// Reine Darstellungs-Helfer rund um Mails. Ausgelagert aus Mail.tsx, damit sowohl
// die Listen-/Leseansicht als auch der Thread-Lesebereich (ThreadReader) dieselbe
// Formatierung und dasselbe iframe-Gerüst nutzen — ohne Zirkelbezug zwischen den
// beiden Komponenten.

// Absender "Name <mail@x.de>" in Anzeigename + Adresse zerlegen.
export function parseAddr(s: string): { name: string; email: string } {
  const m = /^\s*"?(.*?)"?\s*<([^>]+)>\s*$/.exec(s || "");
  if (m && m[2]) return { name: (m[1] || m[2]).trim(), email: m[2].trim() };
  return { name: s || "", email: s || "" };
}

// Server-Datumsstring hübsch lokalisiert; fällt bei Parse-Fehler auf Rohtext zurück.
export function prettyDate(s: string): string {
  const d = new Date(s);
  return isNaN(d.getTime()) ? s : d.toLocaleString();
}

// Kompaktes Datum MIT Uhrzeit für die Listenzeile (z. B. "20. Jun 26, 17:24").
export function listDate(s: string): string {
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

export function hasRemoteContent(html: string): boolean {
  return /(?:src|background)\s*=\s*["']?\s*https?:/i.test(html) || /url\(\s*['"]?\s*https?:/i.test(html);
}

// "Lese-Dunkelmodus": dunkler Hintergrund + Schrift IMMER hell erzwingen
// (!important schlägt die Mail-eigenen Farben), eigene Hintergründe neutralisieren.
const _DARK_STYLE =
  `<style>:root{color-scheme:dark}` +
  `html,body{background:#0d1117 !important;color:#e6edf3 !important;}` +
  `*{background-color:transparent !important;border-color:#30363d !important;}` +
  `*:not(a){color:#e6edf3 !important;}` +
  `a{color:#6cb6ff !important;}` +
  `img,picture,video,svg,canvas{filter:none !important;}` +
  `</style>`;

export function buildSrcDoc(html: string, block: boolean, dark: boolean): string {
  return `<!DOCTYPE html><meta charset="utf-8">${block ? _CSP_BLOCK : ""}${dark ? _DARK_STYLE : ""}<base target="_blank">${html}`;
}

export function fmtSize(bytes: number): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

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

// Absender-Avatar (Initialen + stabile Farbe aus dem Namen) — wie Synology/die
// Android-App. Buchstaben aus bis zu zwei Wortanfängen, sonst erster Buchstabe.
const _AVATAR_COLORS = [
  "#e05a5a", "#e0865a", "#d9a441", "#5aa85a", "#3fa9a0",
  "#4f8bd4", "#6a6ad0", "#a45ad0", "#d05a9e", "#8a8f98",
];
export function avatarFor(nameOrEmail: string): { initials: string; color: string } {
  const s = (nameOrEmail || "").trim();
  const letters = s.replace(/[<>"]/g, "").split(/[\s@._-]+/).filter(Boolean);
  let initials = "?";
  if (letters.length >= 2) initials = (letters[0][0] + letters[1][0]).toUpperCase();
  else if (letters.length === 1) initials = letters[0].slice(0, 2).toUpperCase();
  let hash = 0;
  for (let i = 0; i < s.length; i++) hash = (hash * 31 + s.charCodeAt(i)) >>> 0;
  return { initials, color: _AVATAR_COLORS[hash % _AVATAR_COLORS.length] };
}

// Erkennt die typische Zitat-Einleitung ("Am … schrieb …:", "On … wrote:",
// "-----Original…", Outlook-Kopf "Von:/From:"). Kurz gehalten, damit ein normaler
// Satz, der zufällig so anfängt, nicht fälschlich als Zitat gilt.
const _ATTR_LINE = /^\s*(Am\s.+\sschrieb.*:|On\s.+\swrote:|Le\s.+\sécrit\s*:|El\s.+\sescribió:|-{3,}\s*(Original|Ursprüngliche|Weitergeleitete).*|_{5,}|Von:\s.+|From:\s.+|Gesendet:\s.+|Sent:\s.+)\s*$/i;
const _ATTR_SHORT = /^(Am\s.+\sschrieb.*:|On\s.+\swrote:|Le\s.+\sécrit\s*:|El\s.+\sescribió:)$/i;

/**
 * Trennt bei einer Text-Mail den NEUEN Teil vom zitierten Verlauf ab.
 * Schneidet an der ersten Zitat-Einleitung ODER dem ersten ">"-Zitatblock.
 * Bleibt vorne nichts übrig, wird NICHT gekürzt (dann ist die ganze Mail Zitat).
 */
export function trimQuotedText(text: string): { text: string; trimmed: boolean } {
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    if (_ATTR_LINE.test(lines[i]) || /^\s*>/.test(lines[i])) {
      const head = lines.slice(0, i).join("\n").replace(/\s+$/, "");
      if (head.trim()) return { text: head, trimmed: true };
      return { text, trimmed: false };
    }
  }
  return { text, trimmed: false };
}

/**
 * Trennt bei einer HTML-Mail den NEUEN Teil vom zitierten Verlauf ab. Erkennt die
 * gängigen Zitat-Container (blockquote, Gmail/Thunderbird/Outlook) sowie eine
 * vorangestellte "Am … schrieb:"-Zeile und entfernt sie samt allem, was danach
 * folgt. Läuft rein im Browser (DOMParser). Bei Fehlern/leerem Rest: nicht kürzen.
 */
export function trimQuotedHtml(html: string): { html: string; trimmed: boolean } {
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const body = doc.body;
    if (!body) return { html, trimmed: false };
    const sel = "blockquote, .gmail_quote, [class*='gmail_quote'], .gmail_extra, div.moz-cite-prefix, #divRplyFwdMsg, [id*='divRplyFwdMsg']";
    let cut: Element | null = body.querySelector(sel);
    if (!cut) {
      // Fallback: kurzer Element-Knoten, dessen Text wie eine Zitat-Einleitung aussieht.
      const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
      let node = walker.nextNode() as Element | null;
      while (node) {
        const txt = (node.textContent || "").trim();
        if (txt.length < 200 && _ATTR_SHORT.test(txt)) { cut = node; break; }
        node = walker.nextNode() as Element | null;
      }
    }
    if (!cut) return { html, trimmed: false };
    // Auf das direkte body-Kind hochklettern.
    let top: Element = cut;
    while (top.parentElement && top.parentElement !== body) top = top.parentElement;
    // Eine unmittelbar davor stehende "Am … schrieb:"-Zeile mit entfernen.
    const prev = top.previousElementSibling;
    if (prev) {
      const ptxt = (prev.textContent || "").trim();
      if (ptxt.length < 200 && _ATTR_SHORT.test(ptxt)) top = prev;
    }
    // top + alle folgenden Geschwister löschen.
    let n: Element | null = top;
    const rm: Element[] = [];
    while (n) { rm.push(n); n = n.nextElementSibling; }
    rm.forEach((el) => el.remove());
    const trimmed = body.innerHTML.trim();
    if (!trimmed) return { html, trimmed: false };
    return { html: trimmed, trimmed: true };
  } catch {
    return { html, trimmed: false };
  }
}

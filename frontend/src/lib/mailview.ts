import DOMPurify from "dompurify";

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

// CSP fuers Mail-iframe. Es gibt ZWEI Varianten und immer genau eine davon ist
// gesetzt (Befund 6: frueher stand gar keine CSP mehr im Dokument, sobald der
// Nutzer "Bilder anzeigen" geklickt hat - damit waren frame-src, object-src,
// media-src und font-src wieder offen und nur noch das sandbox-Attribut hat
// geschuetzt).
//  - _CSP_BLOCK: nichts Externes, nur eingebettete data:/cid:-Bilder.
//  - _CSP_ALLOW: genau EINE Oeffnung, naemlich Bilder ueber http(s). Alles
//    andere (Skripte, Rahmen, Plugins, Schriften, Medien) bleibt zu.
const _CSP_BLOCK =
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: cid:; style-src 'unsafe-inline'; font-src data:; media-src data:;">`;
const _CSP_ALLOW =
  `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: cid: https: http:; style-src 'unsafe-inline'; font-src data:; media-src data:;">`;

// sandbox-Werte fuer Mail-HTML an EINER Stelle (Befund 9: es gab drei
// verschiedene, einer davon mit allow-same-origin). Keiner enthaelt
// allow-scripts - fremdes Mail-HTML darf niemals Code ausfuehren.
//
// MAIL_SANDBOX ist der Normalfall (Leseansicht), ohne same-origin.
// MAIL_SANDBOX_MEASURED braucht der ThreadReader, weil er die Inhaltshoehe
// ueber contentDocument.scrollHeight misst; das geht ohne same-origin nicht,
// und ohne Skripte gibt es auch keinen postMessage-Weg. Ohne allow-scripts
// ist same-origin folgenlos - deshalb darf an diesen Konstanten NIE
// allow-scripts ergaenzt werden (Test: tests/mailview-security.cjs).
export const MAIL_SANDBOX = "allow-popups allow-popups-to-escape-sandbox";
export const MAIL_SANDBOX_MEASURED = "allow-popups allow-popups-to-escape-sandbox allow-same-origin";
// Druck-iframe: das Elternfenster schreibt das Dokument hinein und ruft
// print(); dafuer sind same-origin und modals noetig, Popups nicht.
export const MAIL_SANDBOX_PRINT = "allow-same-origin allow-modals";

// Prueft, ob die Mail externe Inhalte nachladen wuerde (Tracking-Pixel).
// Befund 17: frueher liefen dafuer zwei regulaere Ausdruecke ueber den Rohtext.
// Das meldete "externe Inhalte" auch dann, wenn src=http nur als sichtbarer
// Text oder in einem Kommentar stand, und uebersah Attribute mit Zeilenumbruch.
// Jetzt wird derselbe DOM gefragt, der nachher auch gerendert wird. Der alte
// Ausdruck bleibt Rueckfallebene, falls DOMParser wirft - dann lieber einmal zu
// viel warnen als zu wenig.
function _remoteRegex(html: string): boolean {
  return /(?:src|background)\s*=\s*["']?\s*(?:https?:)?\/\//i.test(html) || /url\(\s*['"]?\s*(?:https?:)?\/\//i.test(html);
}

export function hasRemoteContent(html: string): boolean {
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const remote = (v: string | null) => !!v && /^\s*(?:https?:)?\/\//i.test(v);
    const tags = "img, source, video, audio, embed, iframe, object, input, track";
    for (const el of Array.from(doc.querySelectorAll(tags))) {
      if (remote(el.getAttribute("src"))) return true;
      if (remote(el.getAttribute("srcset"))) return true;
      if (remote(el.getAttribute("data"))) return true;
      if (remote(el.getAttribute("poster"))) return true;
    }
    for (const el of Array.from(doc.querySelectorAll("[background]"))) {
      if (remote(el.getAttribute("background"))) return true;
    }
    for (const el of Array.from(doc.querySelectorAll("link[href]"))) {
      if (remote(el.getAttribute("href"))) return true;
    }
    for (const el of Array.from(doc.querySelectorAll("[style]"))) {
      if (/url\(\s*['"]?\s*(?:https?:)?\/\//i.test(el.getAttribute("style") || "")) return true;
    }
    for (const el of Array.from(doc.querySelectorAll("style"))) {
      if (/url\(\s*['"]?\s*(?:https?:)?\/\//i.test(el.textContent || "")) return true;
    }
    return false;
  } catch {
    return _remoteRegex(html);
  }
}

// "Lese-Dunkelmodus": dunkler Hintergrund + Schrift IMMER hell erzwingen
// (!important schlägt die Mail-eigenen Farben), eigene Hintergründe neutralisieren.
//  - background-image:none — dekorative HELLE Hintergrundbilder (Newsletter-Hero-
//    Kacheln) raus, sonst liegt die erzwungene helle Schrift auf hellem Bild = unlesbar.
//  - img{background:#fff} — helle "Sicherheitsplatte" hinter Bildern, damit dunkle/
//    transparente Logos (schwarzes PNG) auf dem dunklen Grund sichtbar/erkennbar bleiben.
//    Wirkt nur durch transparente Stellen; deckende Fotos bleiben unberührt.
const _DARK_STYLE =
  `<style>:root{color-scheme:dark}` +
  `html,body{background:#0d1117 !important;color:#e6edf3 !important;}` +
  `*{background-color:transparent !important;background-image:none !important;border-color:#30363d !important;}` +
  `*:not(a){color:#e6edf3 !important;}` +
  `a{color:#6cb6ff !important;}` +
  `img{background:#ffffff !important;}` +
  `img,picture,video,svg,canvas{filter:none !important;}` +
  `</style>`;

// Entfernt bei blockierten Bildern die externen Verweise KOMPLETT aus dem DOM.
// Ohne das bleibt zwar durch die CSP der Ladevorgang blockiert, der Browser
// zeigt aber trotzdem das hässliche „kaputtes Bild"-Symbol für jedes <img>.
// Nach dem Strippen ist ein src-loses <img> unsichtbar; „Bilder anzeigen"
// rendert den Original-HTML ohne block=true einfach neu.
// Bereitet den Mail-HTML fürs iframe auf:
//  - Reverse-Tabnabbing-Schutz: JEDER Link bekommt rel="noopener noreferrer",
//    damit eine im neuen Tab geöffnete (Angreifer-)Seite NICHT über window.opener
//    unseren Mail-Tab auf eine Phishing-Seite umleiten kann. Läuft IMMER.
//  - Bei ``block`` zusätzlich: externe Bild-/Hintergrund-Verweise KOMPLETT raus
//    (sonst zeigt der Browser trotz CSP das „kaputtes Bild"-Symbol). „Bilder
//    anzeigen" rendert den Original-HTML dann ohne block neu.
//  - Befund 8: das Mail-HTML laeuft zuerst durch DOMPurify. Auf dem Schreibpfad
//    (Compose/RichEditor) war das schon immer so, auf dem LESEpfad hing der
//    ganze Schutz am sandbox-Attribut. Die Sandbox bleibt selbstverstaendlich -
//    das hier ist die zweite Verteidigungslinie, falls ein Browser die Sandbox
//    einmal falsch umsetzt oder jemand spaeter allow-scripts ergaenzt.
function _prepareMailHtml(html: string, block: boolean): string {
  try {
    const sauber = DOMPurify.sanitize(html, {
      FORBID_TAGS: ["script", "iframe", "object", "embed", "form", "base"],
      FORBID_ATTR: ["srcdoc", "formaction", "ping"],
    });
    const doc = new DOMParser().parseFromString(sauber, "text/html");
    // Links härten (immer).
    doc.querySelectorAll("a").forEach((a) => {
      a.setAttribute("rel", "noopener noreferrer");
    });
    if (block) {
      const isRemote = (v: string | null) => !!v && /^\s*(?:https?:)?\/\//i.test(v);
      doc.querySelectorAll("img, source").forEach((el) => {
        if (isRemote(el.getAttribute("src"))) el.removeAttribute("src");
        if (isRemote(el.getAttribute("srcset"))) el.removeAttribute("srcset");
        // Verwaistes <img> ohne src ganz ausblenden (kein Rahmen/Alt-Text-Rest).
        if (el.tagName === "IMG" && !el.getAttribute("src")) {
          (el as HTMLElement).style.display = "none";
        }
      });
      // background="http…" und inline background-image:url(http…)
      doc.querySelectorAll<HTMLElement>("[background], [style*='url(']").forEach((el) => {
        if (isRemote(el.getAttribute("background"))) el.removeAttribute("background");
        const st = el.getAttribute("style");
        if (st && /url\(\s*['"]?\s*(?:https?:)?\/\//i.test(st)) {
          el.setAttribute("style", st.replace(/url\(\s*['"]?\s*(?:https?:)?\/\/[^)]*\)/gi, "none"));
        }
      });
    }
    return doc.body ? doc.body.innerHTML : html;
  } catch {
    return html;
  }
}

export function buildSrcDoc(html: string, block: boolean, dark: boolean): string {
  const body = _prepareMailHtml(html, block);
  // Immer eine CSP, nur der Bild-Teil unterscheidet sich (Befund 6).
  const csp = block ? _CSP_BLOCK : _CSP_ALLOW;
  return `<!DOCTYPE html><meta charset="utf-8">${csp}${dark ? _DARK_STYLE : ""}<base target="_blank">${body}`;
}

// Druckfassung einer Mail: derselbe aufbereitete Rumpf wie in der Leseansicht
// (Befund 7 - der Druckpfad hat frueher das ROHE Mail-HTML genommen und dabei
// externe Bilder nachgeladen, obwohl der Nutzer sie in der Ansicht blockiert
// hatte; ueber den Druckknopf war die Bildblockade also umgehbar).
export function buildPrintBody(html: string, block: boolean): string {
  return _prepareMailHtml(html, block);
}

// CSP-Zeile fuers Druckdokument, gleiche Regel wie im Lese-iframe (Befund 7).
export function printCsp(block: boolean): string {
  return block ? _CSP_BLOCK : _CSP_ALLOW;
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
// Zitat-Kopfzeile MITTEN im Text (Verlauf ohne Zeilenumbrüche, ">" inline) — z. B.
// "Am Thu, 23 Jul 2026 15:00:41 +0200 schrieb x@y.de:> …". Verlangt Jahr (4 Ziffern)
// vor "schrieb/wrote", damit ein normaler Satz ("Am Montag schrieb ich …") NICHT greift.
const _ATTR_INLINE = /(?:-{3,}\s*)?\b(?:Am|On|Le|El)\s.{0,80}?\d{4}.{0,80}?\s(?:schrieb|wrote|écrit|escribió)\b/i;

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
  // Fallback: verschachtelter Verlauf ohne Zeilenumbrüche → an der Inline-Kopfzeile
  // ("Am … 2026 … schrieb …") abschneiden, wenn davor echter neuer Text steht.
  const m = _ATTR_INLINE.exec(text);
  if (m && m.index > 0) {
    const head = text.slice(0, m.index).replace(/\s+$/, "");
    if (head.trim()) return { text: head, trimmed: true };
  }
  return { text, trimmed: false };
}

// Zitat-Kopfzeile, die einen (Text-)Knoten EINLEITET ("Am … 2026 … schrieb …").
// Am Knoten-Anfang verankert (^) — daher kein Wortgrenzen-Problem (im HTML-textContent
// kleben Block-Enden zusammen, z. B. "SvenAm Thu"; ein `\b` würde da versagen). Verlangt
// Jahr (4 Ziffern), damit ein normaler Satz nicht fälschlich als Zitat gilt.
const _ATTR_START = /^\s*(?:-{3,}\s*)?(?:Am|On|Le|El)\s.{0,80}?\d{4}.{0,80}?\s(?:schrieb|wrote|écrit|escribió)\b/i;

/**
 * Trennt bei einer HTML-Mail den NEUEN Teil vom zitierten Verlauf ab.
 *
 * WICHTIG: Ein nacktes ``<blockquote>`` ist KEIN verlässlicher Marker — man zitiert auch
 * IM eigenen Text (z. B. eine fremde Aussage). Der zuverlässige Marker ist die Kopfzeile
 * "Am … schrieb …:" bzw. ein client-spezifischer Antwort-Container (Gmail/Thunderbird/
 * Outlook). Geschnitten wird am ERSTEN dieser Marker: dem Textknoten, der mit der
 * Kopfzeile beginnt, ODER dem Container-Element. Läuft rein im Browser (DOMParser).
 */
export function trimQuotedHtml(html: string): { html: string; trimmed: boolean } {
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const body = doc.body;
    if (!body) return { html, trimmed: false };
    // 1) Client-spezifischer Antwort-Container (KEIN nacktes blockquote).
    let cut: Node | null = body.querySelector(
      ".gmail_quote, [class*='gmail_quote'], .gmail_extra, div.moz-cite-prefix, #divRplyFwdMsg, [id*='divRplyFwdMsg']",
    );
    // 2) Sonst: erster Textknoten, der MIT der Attributions-Kopfzeile beginnt.
    if (!cut) {
      const walker = doc.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      let node = walker.nextNode();
      while (node) {
        if (_ATTR_START.test(node.nodeValue || "")) { cut = node; break; }
        node = walker.nextNode();
      }
    }
    if (!cut) return { html, trimmed: false };
    // Auf das direkte body-Kind hochklettern und dieses + alle folgenden Geschwister löschen.
    let top: Node = cut;
    while (top.parentNode && top.parentNode !== body) top = top.parentNode;
    let n: Node | null = top;
    const rm: Node[] = [];
    while (n) { rm.push(n); n = n.nextSibling; }
    rm.forEach((el) => el.parentNode?.removeChild(el));
    // Übrig gebliebene <br>/Leer-Textknoten am Ende (vor dem Schnitt) wegräumen.
    while (
      body.lastChild &&
      (body.lastChild.nodeName === "BR" ||
        (body.lastChild.nodeType === 3 && !(body.lastChild.nodeValue || "").trim()))
    ) {
      body.removeChild(body.lastChild);
    }
    const trimmed = body.innerHTML.trim();
    if (!trimmed) return { html, trimmed: false };
    return { html: trimmed, trimmed: true };
  } catch {
    return { html, trimmed: false };
  }
}

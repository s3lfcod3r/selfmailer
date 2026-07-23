// Konversations-Gruppierung: fasst zusammengehörende Mails (Antwortketten) zu
// einer Konversation zusammen — wie Synology MailPlus / Thunderbird.
//
// Zwei Signale werden kombiniert (Union-Find):
//   1. Echte Thread-Header: In-Reply-To / References verweisen per Message-ID auf
//      die beantwortete(n) Mail(en). Das ist die zuverlässige Quelle.
//   2. Betreff-Heuristik als Rückfall: gleicher (um "Re:/AW:/Fwd:" bereinigter)
//      Betreff. Fängt Fälle ab, in denen ein Client keine References mitschickt
//      oder eine Altbestand-Mail im Cache noch keine Header nachgetragen hat.
//
// Bewusst NICHT nach Absender getrennt: eine Konversation hat mehrere Teilnehmer.
import type { MsgHeader } from "./api";
import { parseAddr } from "./mailview";

export type Conversation = {
  /** Stabiler Schlüssel (Ordner + UID der neuesten Mail) für React-Keys/Selektion. */
  key: string;
  subject: string;
  /** Chronologisch aufsteigend (älteste zuerst) — so liest man einen Thread. */
  messages: MsgHeader[];
  /** Neueste Mail — bestimmt Datum/Vorschau in der Liste. */
  latest: MsgHeader;
  /** Neueste Mail eines ANDEREN (nicht eigenen) Absenders — bestimmt Name+Avatar
   *  in der Liste. So steht dort der Gesprächspartner, auch wenn meine eigene
   *  Antwort die zeitlich neueste Mail ist (wie Synology/Gmail). */
  displayFrom: MsgHeader;
  count: number;
  anyUnseen: boolean;
  anyFlagged: boolean;
  anyAttachment: boolean;
  /** Eindeutige Absender-Anzeigenamen (neueste zuerst) für "A, B, C". */
  fromNames: string[];
  /** true, wenn ALLE Mails der Gruppe von eigenen Konten stammen (kein Gegenüber
   *  in den gerade geladenen Daten). Dann kann die Liste auf einen zuvor gemerkten
   *  Gesprächspartner zurückgreifen, statt „Ich" anzuzeigen. */
  selfOnly: boolean;
};

// "Re: AW: Fwd: …" u. Ä. vorne wiederholt abschneiden und normalisieren.
const _PREFIX_RE = /^\s*(re|aw|fwd?|wg|sv|antw?|antwort|回复|转发)\s*(\[\d+\])?\s*:\s*/i;
export function normalizeSubject(subject: string): string {
  let s = (subject || "").trim();
  // Mehrfach-Präfixe ("Re: AW: …") in einer Schleife entfernen.
  for (let i = 0; i < 10; i++) {
    const next = s.replace(_PREFIX_RE, "");
    if (next === s) break;
    s = next;
  }
  return s.replace(/\s+/g, " ").trim().toLowerCase();
}

function msgTime(m: MsgHeader): number {
  const t = new Date(m.date).getTime();
  return isNaN(t) ? 0 : t;
}

// Message-IDs aus einem References/In-Reply-To-Header: "<a@x> <b@y>" -> ["<a@x>","<b@y>"].
function idsIn(raw: string | undefined): string[] {
  if (!raw) return [];
  const found = raw.match(/<[^<>]+>/g);
  if (found) return found;
  const t = raw.trim();
  return t ? [t] : [];
}

// --- Union-Find (Disjoint Set) über Nachrichten-Indizes ---
function makeUF(n: number) {
  const parent = Array.from({ length: n }, (_, i) => i);
  function find(x: number): number {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]];
      x = parent[x];
    }
    return x;
  }
  function union(a: number, b: number) {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent[ra] = rb;
  }
  return { find, union };
}

/**
 * Gruppiert eine (bereits sortierte) Nachrichtenliste in Konversationen.
 * Die Reihenfolge der Konversationen folgt der Eingangsreihenfolge: jede
 * Konversation erbt die Position ihrer am weitesten oben stehenden Mail. So
 * bleiben z. B. oben angeheftete markierte Mails oben.
 *
 * ``ownEmails``/``meLabel``: eigene Absenderadressen werden in ``fromNames`` als
 * „Ich" ausgewiesen (wie Synology/Gmail).
 */
export function groupThreads(
  messages: MsgHeader[],
  ownEmails: string[] = [],
  meLabel = "",
): Conversation[] {
  const own = new Set(ownEmails.map((e) => e.trim().toLowerCase()).filter(Boolean));
  const n = messages.length;
  if (n === 0) return [];
  const uf = makeUF(n);

  // 1) Index je Message-ID (für Referenz-Auflösung). Bei Duplikaten gewinnt der
  //    erste Eintrag — Union-Find fügt später ohnehin zusammen.
  const byId = new Map<string, number>();
  messages.forEach((m, i) => {
    const id = (m.message_id || "").trim();
    if (id && !byId.has(id)) byId.set(id, i);
  });

  // 2) Verweise verbinden (In-Reply-To + References).
  messages.forEach((m, i) => {
    const refs = [...idsIn(m.in_reply_to), ...idsIn(m.references)];
    for (const ref of refs) {
      const j = byId.get(ref.trim());
      if (j !== undefined) uf.union(i, j);
    }
  });

  // 3) Betreff-Rückfall: gleicher normalisierter Betreff -> selbe Konversation.
  //    Leere Betreffs NICHT zusammenwerfen (sonst landen alle "(kein Betreff)"
  //    in einem Klumpen).
  const bySubject = new Map<string, number>();
  messages.forEach((m, i) => {
    const key = normalizeSubject(m.subject);
    if (!key) return;
    const j = bySubject.get(key);
    if (j === undefined) bySubject.set(key, i);
    else uf.union(i, j);
  });

  // 4) Komponenten einsammeln, dabei die kleinste (oberste) Eingangsposition merken.
  const groups = new Map<number, number[]>();
  const firstPos = new Map<number, number>();
  messages.forEach((_, i) => {
    const root = uf.find(i);
    if (!groups.has(root)) { groups.set(root, []); firstPos.set(root, i); }
    groups.get(root)!.push(i);
  });

  const convs: { pos: number; conv: Conversation }[] = [];
  for (const [root, idxs] of groups) {
    const members = idxs.map((i) => messages[i]);
    // Chronologisch aufsteigend fürs Lesen.
    const sorted = [...members].sort((a, b) => msgTime(a) - msgTime(b));
    // Neueste Mail bestimmt Datum/Vorschau.
    const latest = sorted.reduce((acc, m) => (msgTime(m) >= msgTime(acc) ? m : acc), sorted[0]);
    const isOwn = (m: MsgHeader) => {
      const e = parseAddr(m.from).email.trim().toLowerCase();
      return !!e && own.has(e);
    };
    // Teilnehmer-Namen: die ANDEREN zuerst (neueste zuerst, eindeutig), „Ich" nur
    // ans Ende, falls ich mitgeschrieben habe. So steht der Gesprächspartner vorn,
    // auch wenn meine Antwort die zeitlich neueste Mail ist.
    const others: string[] = [];
    let iParticipated = false;
    for (let k = sorted.length - 1; k >= 0; k--) {
      if (isOwn(sorted[k])) { iParticipated = true; continue; }
      const a = parseAddr(sorted[k].from);
      const nm = a.name || a.email || sorted[k].from;
      if (nm && !others.includes(nm)) others.push(nm);
    }
    const names = others.length
      ? (iParticipated && meLabel ? [...others, meLabel] : others)
      : (meLabel ? [meLabel] : [parseAddr(latest.from).name || latest.from]);
    // Avatar/Name-Quelle: neueste Mail eines ANDEREN Absenders (sonst die neueste).
    let displayFrom = latest;
    for (let k = sorted.length - 1; k >= 0; k--) {
      if (!isOwn(sorted[k])) { displayFrom = sorted[k]; break; }
    }
    convs.push({
      pos: firstPos.get(root)!,
      conv: {
        key: `${latest.folder ?? ""}:${latest.uid}`,
        subject: latest.subject,
        messages: sorted,
        latest,
        displayFrom,
        count: sorted.length,
        anyUnseen: sorted.some((m) => !m.seen),
        anyFlagged: sorted.some((m) => m.flagged),
        anyAttachment: sorted.some((m) => m.has_attachments),
        fromNames: names,
        selfOnly: others.length === 0,
      },
    });
  }

  convs.sort((a, b) => a.pos - b.pos);
  return convs.map((c) => c.conv);
}

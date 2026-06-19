import { useState } from "react";
import { api } from "../lib/api";

export type Draft = {
  to: string; cc: string; subject: string; body: string; in_reply_to: string;
};

export function emptyDraft(): Draft {
  return { to: "", cc: "", subject: "", body: "", in_reply_to: "" };
}

function quoteText(text: string, html: string): string {
  const src = text || html.replace(/<[^>]+>/g, "");
  return src.split("\n").map((l) => "> " + l).join("\n");
}

// Antwort-Entwurf aus einer geoeffneten Nachricht.
export function replyDraft(d: {
  from: string; subject: string; date: string; text: string; html: string; message_id: string;
}): Draft {
  return {
    to: d.from,
    cc: "",
    subject: d.subject.startsWith("Re:") ? d.subject : "Re: " + d.subject,
    body: "\n\nAm " + d.date + " schrieb " + d.from + ":\n" + quoteText(d.text, d.html),
    in_reply_to: d.message_id,
  };
}

export function forwardDraft(d: {
  from: string; subject: string; date: string; text: string; html: string;
}): Draft {
  const orig = d.text || d.html.replace(/<[^>]+>/g, "");
  const head = "\n\n---------- Weitergeleitete Nachricht ----------\n";
  return {
    to: "", cc: "",
    subject: d.subject.startsWith("Fwd:") ? d.subject : "Fwd: " + d.subject,
    body: head + "Von: " + d.from + "\nDatum: " + d.date + "\nBetreff: " + d.subject + "\n\n" + orig,
    in_reply_to: "",
  };
}

function split(v: string): string[] {
  return v.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
}

export function Compose({
  accountId, draft, onClose,
}: { accountId: number; draft: Draft; onClose: () => void }) {
  const [d, setD] = useState<Draft>(draft);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  function set<K extends keyof Draft>(k: K, v: Draft[K]) { setD((p) => ({ ...p, [k]: v })); }

  async function send() {
    setErr("");
    if (split(d.to).length === 0) { setErr("Mindestens einen Empfänger angeben."); return; }
    setBusy(true);
    try {
      await api.post(`/mail/${accountId}/send`, {
        to: split(d.to), cc: split(d.cc), subject: d.subject, body: d.body,
        in_reply_to: d.in_reply_to,
      });
      onClose();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal card" onClick={(e) => e.stopPropagation()}>
        <div className="topbar">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Neue Nachricht</h2>
          <button className="ghost" onClick={onClose}>✕</button>
        </div>
        <div className="stack">
          <input placeholder="An (Komma-getrennt)" value={d.to} onChange={(e) => set("to", e.target.value)} />
          <input placeholder="Cc" value={d.cc} onChange={(e) => set("cc", e.target.value)} />
          <input placeholder="Betreff" value={d.subject} onChange={(e) => set("subject", e.target.value)} />
          <textarea rows={12} placeholder="Nachricht…" value={d.body} onChange={(e) => set("body", e.target.value)} />
          {err && <div className="err">{err}</div>}
          <div className="row">
            <span className="grow" />
            <button className="ghost" onClick={onClose}>Abbrechen</button>
            <button className="primary" onClick={send} disabled={busy}>{busy ? "Sende…" : "Senden"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

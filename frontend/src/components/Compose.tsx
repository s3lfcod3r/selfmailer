import { useState } from "react";
import { api } from "../lib/api";
import { useLang, type TFunc } from "../lib/i18n";

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

// Antwort-Entwurf aus einer geoeffneten Nachricht. t für lokalisierte Zitat-Zeile.
export function replyDraft(d: {
  from: string; subject: string; date: string; text: string; html: string; message_id: string;
}, t: TFunc): Draft {
  return {
    to: d.from,
    cc: "",
    subject: d.subject.startsWith("Re:") ? d.subject : "Re: " + d.subject,
    body: "\n\n" + t("compose.replyIntro", { date: d.date, from: d.from }) + "\n" + quoteText(d.text, d.html),
    in_reply_to: d.message_id,
  };
}

export function forwardDraft(d: {
  from: string; subject: string; date: string; text: string; html: string;
}, t: TFunc): Draft {
  const orig = d.text || d.html.replace(/<[^>]+>/g, "");
  const head = "\n\n" + t("compose.forwardHeader") + "\n";
  return {
    to: "", cc: "",
    subject: d.subject.startsWith("Fwd:") ? d.subject : "Fwd: " + d.subject,
    body: head
      + t("compose.fwdFrom") + " " + d.from + "\n"
      + t("compose.fwdDate") + " " + d.date + "\n"
      + t("compose.fwdSubject") + " " + d.subject + "\n\n" + orig,
    in_reply_to: "",
  };
}

function split(v: string): string[] {
  return v.split(/[,;]/).map((s) => s.trim()).filter(Boolean);
}

export function Compose({
  accountId, draft, onClose,
}: { accountId: number; draft: Draft; onClose: () => void }) {
  const { t } = useLang();
  const [d, setD] = useState<Draft>(draft);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  function set<K extends keyof Draft>(k: K, v: Draft[K]) { setD((p) => ({ ...p, [k]: v })); }

  async function send() {
    setErr("");
    if (split(d.to).length === 0) { setErr(t("compose.needRecipient")); return; }
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
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{t("compose.new")}</h2>
          <button className="ghost" onClick={onClose}>✕</button>
        </div>
        <div className="stack">
          <input placeholder={t("compose.to")} value={d.to} onChange={(e) => set("to", e.target.value)} />
          <input placeholder={t("compose.cc")} value={d.cc} onChange={(e) => set("cc", e.target.value)} />
          <input placeholder={t("compose.subject")} value={d.subject} onChange={(e) => set("subject", e.target.value)} />
          <textarea rows={12} placeholder={t("compose.body")} value={d.body} onChange={(e) => set("body", e.target.value)} />
          {err && <div className="err">{err}</div>}
          <div className="row">
            <span className="grow" />
            <button className="ghost" onClick={onClose}>{t("common.cancel")}</button>
            <button className="primary" onClick={send} disabled={busy}>{busy ? t("compose.sending") : t("compose.send")}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

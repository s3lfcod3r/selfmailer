import { useEffect, useState } from "react";
import { api, type Account } from "../lib/api";
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

const MAX_ATTACH_BYTES = 20 * 1024 * 1024; // 20 MB gesamt

// Liest eine Datei als base64 (ohne data:-Praefix).
function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

export function Compose({
  accountId, draft, onClose,
}: { accountId: number; draft: Draft; onClose: () => void }) {
  const { t } = useLang();
  const [d, setD] = useState<Draft>(draft);
  const [files, setFiles] = useState<File[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [fromId, setFromId] = useState<number>(accountId);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { api.get<Account[]>("/accounts").then(setAccounts).catch(() => {}); }, []);

  function set<K extends keyof Draft>(k: K, v: Draft[K]) { setD((p) => ({ ...p, [k]: v })); }

  function addFiles(list: FileList | null) {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
  }
  function removeFile(idx: number) {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  async function send() {
    setErr("");
    if (split(d.to).length === 0) { setErr(t("compose.needRecipient")); return; }
    if (files.reduce((s, f) => s + f.size, 0) > MAX_ATTACH_BYTES) { setErr(t("compose.tooLarge")); return; }
    setBusy(true);
    try {
      const attachments = await Promise.all(
        files.map(async (f) => ({
          filename: f.name,
          content_type: f.type || "application/octet-stream",
          content_b64: await fileToB64(f),
        })),
      );
      await api.post(`/mail/${fromId}/send`, {
        to: split(d.to), cc: split(d.cc), subject: d.subject, body: d.body,
        in_reply_to: d.in_reply_to, attachments,
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
          {accounts.length > 1 && (
            <div className="row" style={{ gap: "0.5rem", alignItems: "center" }}>
              <span className="label" style={{ minWidth: 44 }}>{t("compose.from")}</span>
              <select value={fromId} onChange={(e) => setFromId(Number(e.target.value))} style={{ flex: 1 }}>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.label ? `${a.label} — ${a.email}` : a.email}</option>
                ))}
              </select>
            </div>
          )}
          <input placeholder={t("compose.to")} value={d.to} onChange={(e) => set("to", e.target.value)} />
          <input placeholder={t("compose.cc")} value={d.cc} onChange={(e) => set("cc", e.target.value)} />
          <input placeholder={t("compose.subject")} value={d.subject} onChange={(e) => set("subject", e.target.value)} />
          <textarea rows={10} placeholder={t("compose.body")} value={d.body} onChange={(e) => set("body", e.target.value)} />

          {files.length > 0 && (
            <div className="row" style={{ flexWrap: "wrap", gap: "0.4rem" }}>
              {files.map((f, i) => (
                <span key={i} className="label" style={{ display: "inline-flex", gap: "0.4rem", alignItems: "center" }}>
                  📎 {f.name}
                  <button className="ghost" style={{ padding: "0 0.2rem" }} onClick={() => removeFile(i)} title={t("common.remove")}>✕</button>
                </span>
              ))}
            </div>
          )}

          {err && <div className="err">{err}</div>}
          <div className="row">
            <label className="ghost" style={{ cursor: "pointer" }}>
              📎 {t("compose.attach")}
              <input type="file" multiple style={{ display: "none" }} onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
            </label>
            <span className="grow" />
            <button className="ghost" onClick={onClose}>{t("common.cancel")}</button>
            <button className="primary" onClick={send} disabled={busy}>{busy ? t("compose.sending") : t("compose.send")}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

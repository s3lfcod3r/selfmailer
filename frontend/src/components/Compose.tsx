import { useEffect, useRef, useState } from "react";
import { api, type Account } from "../lib/api";
import { useLang, type TFunc } from "../lib/i18n";
import { RecipientField } from "./RecipientField";

export type Draft = {
  to: string; cc: string; bcc: string; subject: string; body: string; in_reply_to: string;
};

export function emptyDraft(): Draft {
  return { to: "", cc: "", bcc: "", subject: "", body: "", in_reply_to: "" };
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
    bcc: "",
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
    to: "", cc: "", bcc: "",
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

// Signatur kann HTML (neuer Rich-Editor) ODER alter Plaintext sein.
function isHtmlSig(sig: string): boolean {
  return /<[a-z][\s\S]*>/i.test(sig);
}
// Plaintext-Fassung der Signatur (fuer den text/plain-Teil der Mail).
function sigText(sig: string): string {
  if (!sig) return "";
  const plain = isHtmlSig(sig)
    ? sig
        .replace(/<br\s*\/?>(?!\n)/gi, "\n")
        .replace(/<\/(p|div|li|h[1-6])>/gi, "\n")
        .replace(/<[^>]+>/g, "")
        .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
        .trim()
    : sig;
  return "\n\n-- \n" + plain;
}
// HTML-Fassung (fuer den text/html-Teil). Plaintext wird escaped + nl2br.
function sigHtml(sig: string): string {
  if (!sig) return "";
  const inner = isHtmlSig(sig)
    ? sig
    : sig.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br>");
  return "<br><br>-- <br>" + inner;
}

const MAX_ATTACH_BYTES = 20 * 1024 * 1024; // 20 MB gesamt

function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",", 2)[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

// Formatier-Buttons (eigene Leiste via execCommand).
const FORMATS: { cmd: string; arg?: string; label: string; title: string }[] = [
  { cmd: "bold", label: "B", title: "Fett" },
  { cmd: "italic", label: "I", title: "Kursiv" },
  { cmd: "underline", label: "U", title: "Unterstrichen" },
  { cmd: "strikeThrough", label: "S", title: "Durchgestrichen" },
  { cmd: "insertUnorderedList", label: "•", title: "Aufzählung" },
  { cmd: "insertOrderedList", label: "1.", title: "Nummerierung" },
  { cmd: "justifyLeft", label: "⯈|", title: "Linksbündig" },
  { cmd: "justifyCenter", label: "≡", title: "Zentriert" },
  { cmd: "removeFormat", label: "⌫", title: "Format entfernen" },
];

export function Compose({
  accountId, draft, onClose,
}: { accountId: number; draft: Draft; onClose: () => void }) {
  const { t } = useLang();
  const [d, setD] = useState<Draft>(draft);
  const [files, setFiles] = useState<File[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [fromId, setFromId] = useState<number>(accountId);
  const [showCc, setShowCc] = useState<boolean>(!!draft.cc);
  const [showBcc, setShowBcc] = useState<boolean>(!!draft.bcc);
  const [moreOpen, setMoreOpen] = useState(false);
  const [readReceipt, setReadReceipt] = useState(false);
  const [deliveryReceipt, setDeliveryReceipt] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const editorRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api.get<Account[]>("/accounts").then(setAccounts).catch(() => {}); }, []);
  // Editor einmalig mit dem Entwurfstext füllen (Zeilenumbrüche bleiben erhalten).
  useEffect(() => { if (editorRef.current) editorRef.current.innerText = draft.body; }, [draft.body]);

  function set<K extends keyof Draft>(k: K, v: Draft[K]) { setD((p) => ({ ...p, [k]: v })); }
  function exec(cmd: string, arg?: string) {
    document.execCommand(cmd, false, arg);
    editorRef.current?.focus();
  }
  function addLink() {
    const url = prompt(t("compose.linkPrompt"));
    if (url) exec("createLink", url);
  }

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
      const sig = accounts.find((a) => a.id === fromId)?.signature ?? "";
      const html = (editorRef.current?.innerHTML ?? "") + sigHtml(sig);
      const body = (editorRef.current?.innerText ?? d.body) + sigText(sig);
      await api.post(`/mail/${fromId}/send`, {
        to: split(d.to), cc: split(d.cc), bcc: split(d.bcc),
        subject: d.subject, body, html,
        in_reply_to: d.in_reply_to, attachments,
        read_receipt: readReceipt, delivery_receipt: deliveryReceipt,
      });
      onClose();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  // ✕ schließt und speichert ungesendete Eingaben als Entwurf (nicht verwerfen).
  async function closeAsDraft() {
    if (busy) return;
    const sig = accounts.find((a) => a.id === fromId)?.signature ?? "";
    const html = editorRef.current?.innerHTML ?? "";
    const body = editorRef.current?.innerText ?? "";
    const hasContent = !!(d.to || d.cc || d.bcc || d.subject || body.trim());
    if (hasContent) {
      try {
        await api.post(`/mail/${fromId}/draft`, {
          to: split(d.to), cc: split(d.cc), bcc: split(d.bcc),
          subject: d.subject, body: body + sigText(sig), html: html + sigHtml(sig),
        });
      } catch { /* Entwurf-Fehler ignorieren, trotzdem schließen */ }
    }
    onClose();
  }

  return (
    // Klick auf den Hintergrund schließt NICHT (kein versehentliches Verwerfen).
    <div className="modal-backdrop">
      <div className="modal card compose-modal" onClick={(e) => e.stopPropagation()}>
        <div className="topbar">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{t("compose.new")}</h2>
          <button className="ghost" onClick={closeAsDraft} title={t("compose.closeDraft")}>✕</button>
        </div>
        <div className="stack">
          {accounts.length > 0 && (
            <div className="row" style={{ gap: "0.5rem", alignItems: "center" }}>
              <span className="label" style={{ minWidth: 44 }}>{t("compose.from")}</span>
              <select value={fromId} onChange={(e) => setFromId(Number(e.target.value))} style={{ flex: 1 }} disabled={accounts.length === 1}>
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>{a.label && a.label !== a.email ? `${a.label} — ${a.email}` : a.email}</option>
                ))}
              </select>
            </div>
          )}

          <div className="row" style={{ gap: "0.5rem", alignItems: "center" }}>
            <RecipientField value={d.to} onChange={(v) => set("to", v)} placeholder={t("compose.to")} style={{ flex: 1 }} />
            {!showCc && <button className="ghost" style={{ padding: "0 0.4rem" }} onClick={() => setShowCc(true)}>Cc</button>}
            {!showBcc && <button className="ghost" style={{ padding: "0 0.4rem" }} onClick={() => setShowBcc(true)}>Bcc</button>}
          </div>
          {showCc && <RecipientField value={d.cc} onChange={(v) => set("cc", v)} placeholder={t("compose.cc")} />}
          {showBcc && <RecipientField value={d.bcc} onChange={(v) => set("bcc", v)} placeholder={t("compose.bcc")} />}
          <input placeholder={t("compose.subject")} value={d.subject} onChange={(e) => set("subject", e.target.value)} />

          <div className="compose-toolbar">
            {FORMATS.map((f) => (
              <button key={f.cmd} className="ghost" title={f.title} onMouseDown={(e) => { e.preventDefault(); exec(f.cmd, f.arg); }}>{f.label}</button>
            ))}
            <button className="ghost" title={t("compose.link")} onMouseDown={(e) => { e.preventDefault(); addLink(); }}>🔗</button>
          </div>
          <div
            ref={editorRef}
            className="compose-editor"
            contentEditable
            suppressContentEditableWarning
            data-placeholder={t("compose.body")}
          />
          {(() => {
            const sig = accounts.find((a) => a.id === fromId)?.signature;
            return sig ? (
              <div className="compose-sig">
                <span className="label">{t("accounts.signature")}</span>
                <div dangerouslySetInnerHTML={{ __html: "-- <br>" + sigHtml(sig).replace(/^<br><br>-- <br>/, "") }} />
              </div>
            ) : null;
          })()}

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
          <div className="row" style={{ position: "relative" }}>
            <label className="ghost" style={{ cursor: "pointer" }}>
              📎 {t("compose.attach")}
              <input type="file" multiple style={{ display: "none" }} onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
            </label>
            <button className="ghost" title={t("compose.options")} onClick={() => setMoreOpen((o) => !o)}>⋯</button>
            {moreOpen && (
              <div className="compose-more">
                <label><input type="checkbox" style={{ width: "auto" }} checked={readReceipt} onChange={(e) => setReadReceipt(e.target.checked)} /> {t("compose.readReceipt")}</label>
                <label><input type="checkbox" style={{ width: "auto" }} checked={deliveryReceipt} onChange={(e) => setDeliveryReceipt(e.target.checked)} /> {t("compose.deliveryReceipt")}</label>
              </div>
            )}
            <span className="grow" />
            <button className="ghost" onClick={onClose}>{t("common.cancel")}</button>
            <button className="primary" onClick={send} disabled={busy}>{busy ? t("compose.sending") : t("compose.send")}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { api, type Account, type Identity, type MailTemplate } from "../lib/api";
import { useLang, type TFunc } from "../lib/i18n";
import { promptDialog } from "../lib/dialog";
import { safeLinkUrl } from "../lib/url";
import { trimQuotedText } from "../lib/mailview";
import { RecipientField } from "./RecipientField";

export type Draft = {
  to: string; cc: string; bcc: string; subject: string; body: string; in_reply_to: string;
  // Zitierter Verlauf (Antwort/Weiterleitung) — bewusst GETRENNT vom Eingabefeld:
  // so bleibt das Schreibfeld sauber, und der Verlauf wird darunter als grauer
  // Balken angezeigt (wie Gmail). Beim Senden hinten angehängt.
  quotedHtml?: string; quotedText?: string;
};

export function emptyDraft(): Draft {
  return { to: "", cc: "", bcc: "", subject: "", body: "", in_reply_to: "" };
}

const escHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// Baut aus Einleitung + zitiertem Klartext beide Fassungen des Verlaufs:
// - quotedText: klassisch mit "> " je Zeile (für den Plaintext-Teil / einfache Clients)
// - quotedHtml: grauer Gmail-Balken (blockquote.gmail_quote, 1px-Linie), Text normal
export function buildQuoteBlock(introLine: string, rawSource: string): { quotedText: string; quotedHtml: string } {
  const quotedText = "\n\n" + introLine + "\n" + rawSource.split("\n").map((l) => "> " + l).join("\n");
  const quotedHtml = '<div class="gmail_quote"><div class="gmail_attr" style="color:#5f6368">'
    + escHtml(introLine) + "</div>"
    + '<blockquote class="gmail_quote" style="margin:0 0 0 0.8ex;border-left:1px solid #ccc;padding-left:1ex">'
    + escHtml(rawSource).replace(/\n/g, "<br>") + "</blockquote></div>";
  return { quotedText, quotedHtml };
}

// Antwort-Entwurf aus einer geöffneten Nachricht. Das Schreibfeld bleibt LEER
// (sauberes Tippen), der Verlauf steckt getrennt in quotedText/quotedHtml und
// wird darunter als Balken angezeigt bzw. beim Senden angehängt.
export function replyDraft(d: {
  from: string; subject: string; date: string; text: string; html: string; message_id: string;
}, t: TFunc): Draft {
  // Nur den NEUESTEN Teil zitieren (trimQuotedText kappt den verschachtelten Verlauf).
  const raw = trimQuotedText(d.text || d.html.replace(/<[^>]+>/g, "")).text;
  const { quotedText, quotedHtml } = buildQuoteBlock(t("compose.replyIntro", { date: d.date, from: d.from }), raw);
  return {
    to: d.from, cc: "", bcc: "",
    subject: d.subject.startsWith("Re:") ? d.subject : "Re: " + d.subject,
    body: "", in_reply_to: d.message_id, quotedText, quotedHtml,
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
// Plaintext-Fassung der Signatur (für den text/plain-Teil der Mail).
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
// HTML-Fassung (für den text/html-Teil). Plaintext wird escaped + nl2br.
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
  const [identities, setIdentities] = useState<Identity[]>([]);
  // Absenderauswahl als String kodiert: "a{Konto-ID}" = Konto-Adresse selbst,
  // "i{Identitäts-ID}" = konfigurierter Alias. Konto + Alias + Signatur werden daraus
  // abgeleitet (siehe unten), damit es nur EINE Quelle der Wahrheit gibt.
  const [fromKey, setFromKey] = useState<string>("a" + accountId);
  const defaultPicked = useRef(false);
  const [showCc, setShowCc] = useState<boolean>(!!draft.cc);
  const [showBcc, setShowBcc] = useState<boolean>(!!draft.bcc);
  const [moreOpen, setMoreOpen] = useState(false);
  // Zitierten Verlauf einer Antwort sichtbar zeigen (Balken). Standardmäßig an,
  // damit man beim Antworten sofort sieht, was zitiert wird.
  const [showQuote, setShowQuote] = useState(true);
  const [readReceipt, setReadReceipt] = useState(false);
  const [deliveryReceipt, setDeliveryReceipt] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const editorRef = useRef<HTMLDivElement>(null);
  // Vorlagen/Textbausteine.
  const [templates, setTemplates] = useState<MailTemplate[]>([]);
  const [tplEdit, setTplEdit] = useState<MailTemplate | null>(null);   // Vorlage bearbeiten
  const [tplOpen, setTplOpen] = useState(false);
  // „Senden rückgängig": nach Klick auf Senden läuft ein kurzer Countdown, in dem
  // sich der Versand noch abbrechen lässt (wie Gmail). null = kein Versand geplant.
  const UNDO_SECONDS = 5;
  const [countdown, setCountdown] = useState<number | null>(null);
  const sendTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  // Später senden (Terminversand).
  const [schedMenu, setSchedMenu] = useState(false);
  const [customTime, setCustomTime] = useState("");

  useEffect(() => { api.get<Account[]>("/accounts").then(setAccounts).catch(() => {}); }, []);
  useEffect(() => { api.get<MailTemplate[]>("/templates").then(setTemplates).catch(() => {}); }, []);
  useEffect(() => { api.get<Identity[]>("/identities").then(setIdentities).catch(() => {}); }, []);
  // Beim ersten Laden der Identitäten die Standard-Identität des Start-Kontos wählen
  // (einmalig, damit eine spätere manuelle Auswahl nicht überschrieben wird).
  useEffect(() => {
    if (defaultPicked.current || identities.length === 0) return;
    defaultPicked.current = true;
    const def = identities.find((i) => i.account_id === accountId && i.is_default);
    if (def) setFromKey("i" + def.id);
  }, [identities, accountId]);
  useEffect(() => () => { if (sendTimer.current) clearInterval(sendTimer.current); }, []);
  // Editor einmalig mit dem Entwurfstext füllen (Zeilenumbrüche bleiben erhalten).
  useEffect(() => { if (editorRef.current) editorRef.current.innerText = draft.body; }, [draft.body]);

  // Aus fromKey abgeleitet: aktive Identität (falls Alias gewählt), das Konto, über
  // das gesendet wird (fromId), und die passende Signatur.
  const activeIdentity =
    fromKey[0] === "i" ? identities.find((i) => i.id === Number(fromKey.slice(1))) : undefined;
  const fromId = activeIdentity ? activeIdentity.account_id : Number(fromKey.slice(1)) || accountId;
  const currentSig = activeIdentity
    ? activeIdentity.signature
    : (accounts.find((a) => a.id === fromId)?.signature ?? "");

  function set<K extends keyof Draft>(k: K, v: Draft[K]) { setD((p) => ({ ...p, [k]: v })); }
  function exec(cmd: string, arg?: string) {
    document.execCommand(cmd, false, arg);
    editorRef.current?.focus();
  }
  async function addLink() {
    const url = safeLinkUrl(await promptDialog(t("compose.linkPrompt")));
    if (url) exec("createLink", url);
  }
  // Eingefügtes HTML säubern, bevor es ins Live-DOM des Editors gelangt — sonst könnten
  // Inline-Handler (onerror/onmouseover) aus einer bösartigen Quelle sofort feuern und
  // landeten zudem im gesendeten HTML-Teil.
  function onPasteEditor(e: React.ClipboardEvent<HTMLDivElement>) {
    e.preventDefault();
    const html = e.clipboardData.getData("text/html");
    if (html) {
      document.execCommand("insertHTML", false, DOMPurify.sanitize(html));
    } else {
      document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
    }
  }

  function addFiles(list: FileList | null) {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
  }
  function removeFile(idx: number) {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  // Vorlage in den Entwurf einfügen: Betreff nur setzen, wenn noch leer (sonst
  // nicht überschreiben); Text ans Ende des Editors anhängen.
  function insertTemplate(tpl: MailTemplate) {
    setTplOpen(false);
    if (tpl.subject && !d.subject.trim()) set("subject", tpl.subject);
    if (editorRef.current) {
      const cur = editorRef.current.innerText;
      editorRef.current.innerText = cur ? cur + "\n" + tpl.body : tpl.body;
      editorRef.current.focus();
    }
  }
  async function saveAsTemplate() {
    setTplOpen(false);
    const name = (await promptDialog(t("tpl.namePrompt")))?.trim();
    if (!name) return;
    const body = editorRef.current?.innerText ?? "";
    try {
      const created = await api.post<MailTemplate>("/templates", { name, subject: d.subject, body });
      setTemplates((ts) => [...ts, created].sort((a, b) => a.name.localeCompare(b.name)));
    } catch (e) { setErr((e as Error).message); }
  }
  async function deleteTemplate(id: number) {
    try { await api.del(`/templates/${id}`); setTemplates((ts) => ts.filter((x) => x.id !== id)); }
    catch (e) { setErr((e as Error).message); }
  }
  async function saveTplEdit() {
    if (!tplEdit) return;
    const name = tplEdit.name.trim();
    if (!name) return;
    try {
      const upd = await api.patch<MailTemplate>(`/templates/${tplEdit.id}`, { name, subject: tplEdit.subject, body: tplEdit.body });
      setTemplates((ts) => ts.map((x) => (x.id === upd.id ? upd : x)).sort((a, b) => a.name.localeCompare(b.name)));
      setTplEdit(null);
    } catch (e) { setErr((e as Error).message); }
  }

  // Senden mit „Rückgängig"-Countdown: erst nach Ablauf wird wirklich gesendet.
  function send() {
    setErr("");
    if (split(d.to).length === 0) { setErr(t("compose.needRecipient")); return; }
    if (files.reduce((s, f) => s + f.size, 0) > MAX_ATTACH_BYTES) { setErr(t("compose.tooLarge")); return; }
    setCountdown(UNDO_SECONDS);
    if (sendTimer.current) clearInterval(sendTimer.current);
    sendTimer.current = setInterval(() => {
      setCountdown((c) => {
        if (c === null) return null;
        if (c <= 1) {
          if (sendTimer.current) { clearInterval(sendTimer.current); sendTimer.current = null; }
          doSend();
          return null;
        }
        return c - 1;
      });
    }, 1000);
  }
  function undoSend() {
    if (sendTimer.current) { clearInterval(sendTimer.current); sendTimer.current = null; }
    setCountdown(null);
    editorRef.current?.focus();
  }

  // Gemeinsamer Nachrichten-Payload (Anhänge + Signatur) für Sofort- UND
  // Terminversand.
  async function buildPayload() {
    const attachments = await Promise.all(
      files.map(async (f) => ({
        filename: f.name,
        content_type: f.type || "application/octet-stream",
        content_b64: await fileToB64(f),
      })),
    );
    const sig = currentSig;
    const editorText = editorRef.current?.innerText ?? d.body;
    // Reihenfolge: eigener Text → Signatur → zitierter Verlauf (getrennt gehalten,
    // deshalb sauber). HTML-Teil bekommt den grauen Gmail-Balken (d.quotedHtml),
    // der Text-Teil das klassische "> " (d.quotedText) für einfache Clients.
    const html = (editorRef.current?.innerHTML ?? "") + sigHtml(sig) + (d.quotedHtml ?? "");
    const body = editorText + sigText(sig) + (d.quotedText ?? "");
    return {
      to: split(d.to), cc: split(d.cc), bcc: split(d.bcc),
      subject: d.subject, body, html,
      in_reply_to: d.in_reply_to, attachments,
      read_receipt: readReceipt, delivery_receipt: deliveryReceipt,
      from_addr: activeIdentity?.email ?? "",
      from_name: activeIdentity?.name ?? "",
    };
  }

  async function doSend() {
    setBusy(true);
    try {
      await api.post(`/mail/${fromId}/send`, await buildPayload());
      onClose();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  // Termin-Versand: parkt die Mail zum gewählten Zeitpunkt (ISO). Der Server
  // sendet sie dann automatisch.
  async function scheduleSend(when: Date) {
    setSchedMenu(false);
    if (split(d.to).length === 0) { setErr(t("compose.needRecipient")); return; }
    if (files.reduce((s, f) => s + f.size, 0) > MAX_ATTACH_BYTES) { setErr(t("compose.tooLarge")); return; }
    setBusy(true);
    try {
      await api.post(`/mail/${fromId}/schedule`, { ...(await buildPayload()), send_at: when.toISOString() });
      onClose();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  // ✕ schließt und speichert ungesendete Eingaben als Entwurf (nicht verwerfen).
  async function closeAsDraft() {
    if (busy) return;
    const sig = currentSig;
    const html = editorRef.current?.innerHTML ?? "";
    const body = editorRef.current?.innerText ?? "";
    const hasContent = !!(d.to || d.cc || d.bcc || d.subject || body.trim());
    if (hasContent) {
      setBusy(true);
      try {
        await api.post(`/mail/${fromId}/draft`, {
          to: split(d.to), cc: split(d.cc), bcc: split(d.bcc),
          subject: d.subject,
          body: body + sigText(sig) + (d.quotedText ?? ""),
          html: html + sigHtml(sig) + (d.quotedHtml ?? ""),
        });
      } catch (e) {
        // Speichern fehlgeschlagen: Modal OFFEN lassen + Fehler zeigen, damit der
        // Entwurf nicht stillschweigend verloren geht.
        setErr((e as Error).message);
        setBusy(false);
        return;
      }
      setBusy(false);
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
              <select
                value={fromKey}
                onChange={(e) => setFromKey(e.target.value)}
                style={{ flex: 1 }}
                disabled={accounts.length === 1 && identities.length === 0}
              >
                {accounts.map((a) => {
                  const aliases = identities.filter((i) => i.account_id === a.id);
                  const accLabel = a.label && a.label !== a.email ? `${a.label} — ${a.email}` : a.email;
                  // Ohne Aliase: schlichte Option. Mit Aliassen: Gruppe (Konto + Aliase).
                  return aliases.length === 0 ? (
                    <option key={a.id} value={"a" + a.id}>{accLabel}</option>
                  ) : (
                    <optgroup key={a.id} label={accLabel}>
                      <option value={"a" + a.id}>{accLabel}</option>
                      {aliases.map((i) => (
                        <option key={i.id} value={"i" + i.id}>
                          {i.name ? `${i.name} — ${i.email}` : i.email}
                        </option>
                      ))}
                    </optgroup>
                  );
                })}
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
            onPaste={onPasteEditor}
          />
          {/* Zitierter Verlauf (Antwort): getrennt vom Schreibfeld, als grauer Balken
              wie bei Gmail — standardmäßig eingeklappt (Klick blendet ein/aus). */}
          {d.quotedHtml && (
            <div style={{ marginTop: "0.4rem" }}>
              <button className="ghost" style={{ padding: "0.15rem 0.4rem", fontSize: "0.8rem", color: "var(--self-muted, #8aa)" }}
                onClick={() => setShowQuote((v) => !v)} title={t("compose.quotedToggle")}>
                {showQuote ? "▾" : "▸"} {t("compose.quotedHistory")}
              </button>
              {showQuote && (
                <div className="compose-quote"
                  style={{ marginTop: "0.35rem", opacity: 0.85 }}
                  dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(d.quotedHtml) }} />
              )}
            </div>
          )}
          {(() => {
            const sig = currentSig;
            return sig ? (
              <div className="compose-sig">
                <span className="label">{t("accounts.signature")}</span>
                <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize("-- <br>" + sigHtml(sig).replace(/^<br><br>-- <br>/, "")) }} />
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
          {countdown !== null && (
            <div className="compose-undo">
              <span>✉ {t("tpl.sendingIn", { n: countdown })}</span>
              <button className="link-btn" onClick={undoSend}>↩ {t("tpl.undo")}</button>
            </div>
          )}
          <div className="row" style={{ position: "relative" }}>
            <label className="ghost" style={{ cursor: "pointer" }}>
              📎 {t("compose.attach")}
              <input type="file" multiple style={{ display: "none" }} onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
            </label>
            <button className="ghost" title={t("tpl.templates")} onClick={() => setTplOpen((o) => !o)}>📄</button>
            {tplOpen && (
              <div className="compose-more compose-tpl">
                {templates.length === 0 && <div className="muted" style={{ fontSize: "0.8rem", padding: "0.2rem 0.3rem" }}>{t("tpl.none")}</div>}
                {templates.map((tpl) => (
                  <div key={tpl.id} className="compose-tpl-row">
                    <button className="compose-tpl-ins" onClick={() => insertTemplate(tpl)} title={tpl.subject}>{tpl.name}</button>
                    <button className="ghost" style={{ padding: "0 0.3rem" }} onClick={() => { setTplOpen(false); setTplEdit(tpl); }} title={t("common.edit")}>✎</button>
                    <button className="ghost" style={{ padding: "0 0.3rem" }} onClick={() => deleteTemplate(tpl.id)} title={t("common.delete")}>🗑</button>
                  </div>
                ))}
                <button className="link-btn" style={{ marginTop: "0.3rem" }} onClick={saveAsTemplate}>＋ {t("tpl.saveCurrent")}</button>
              </div>
            )}
            {tplEdit && (
              <div className="modal-backdrop" onClick={() => setTplEdit(null)}>
                <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 460, display: "flex", flexDirection: "column", gap: "0.55rem" }}>
                  <h2 style={{ margin: 0 }}>{t("common.edit")}: {tplEdit.name}</h2>
                  <input value={tplEdit.name} placeholder={t("tpl.namePrompt")}
                    onChange={(e) => setTplEdit({ ...tplEdit, name: e.target.value })} />
                  <input value={tplEdit.subject} placeholder={t("compose.subject")}
                    onChange={(e) => setTplEdit({ ...tplEdit, subject: e.target.value })} />
                  <textarea value={tplEdit.body} rows={6} placeholder={t("compose.body")}
                    onChange={(e) => setTplEdit({ ...tplEdit, body: e.target.value })} />
                  <div className="row" style={{ gap: "0.5rem", justifyContent: "flex-end" }}>
                    <button className="ghost" onClick={() => setTplEdit(null)}>{t("common.cancel")}</button>
                    <button className="primary" onClick={saveTplEdit}>{t("common.save")}</button>
                  </div>
                </div>
              </div>
            )}
            <button className="ghost" title={t("sched.later")} onClick={() => setSchedMenu((o) => !o)}>🕒</button>
            {schedMenu && (
              <div className="compose-more compose-sched">
                <button className="compose-tpl-ins" onClick={() => scheduleSend(new Date(Date.now() + 3600e3))}>{t("sched.in1h")}</button>
                <button className="compose-tpl-ins" onClick={() => scheduleSend(new Date(Date.now() + 3 * 3600e3))}>{t("sched.in3h")}</button>
                <button className="compose-tpl-ins" onClick={() => { const dt = new Date(); dt.setDate(dt.getDate() + 1); dt.setHours(8, 0, 0, 0); scheduleSend(dt); }}>{t("sched.tomorrow8")}</button>
                <div className="row" style={{ gap: "0.3rem", marginTop: "0.3rem" }}>
                  <input type="datetime-local" value={customTime} onChange={(e) => setCustomTime(e.target.value)} style={{ flex: 1 }} />
                  <button className="link-btn" disabled={!customTime} onClick={() => { const dt = new Date(customTime); if (!isNaN(dt.getTime())) scheduleSend(dt); }}>{t("sched.schedule")}</button>
                </div>
              </div>
            )}
            <button className="ghost" title={t("compose.options")} onClick={() => setMoreOpen((o) => !o)}>⋯</button>
            {moreOpen && (
              <div className="compose-more">
                <label><input type="checkbox" style={{ width: "auto" }} checked={readReceipt} onChange={(e) => setReadReceipt(e.target.checked)} /> {t("compose.readReceipt")}</label>
                <label><input type="checkbox" style={{ width: "auto" }} checked={deliveryReceipt} onChange={(e) => setDeliveryReceipt(e.target.checked)} /> {t("compose.deliveryReceipt")}</label>
              </div>
            )}
            <span className="grow" />
            <button className="ghost" onClick={onClose}>{t("common.cancel")}</button>
            <button className="primary" onClick={send} disabled={busy || countdown !== null}>{busy ? t("compose.sending") : t("compose.send")}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

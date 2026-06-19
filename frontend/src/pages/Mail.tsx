import { useEffect, useState } from "react";
import { api, type Account, type MsgHeader, type MsgDetail } from "../lib/api";
import { useLang } from "../lib/i18n";
import { Compose, emptyDraft, replyDraft, forwardDraft, type Draft } from "../components/Compose";

export function Mail() {
  const { t } = useLang();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<MsgHeader[]>([]);
  const [open, setOpen] = useState<MsgDetail | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get<Account[]>("/accounts").then((a) => {
      setAccounts(a);
      if (a.length) setActiveId(a[0].id);
    });
  }, []);

  function reload() {
    if (activeId == null) return;
    setLoading(true); setErr(""); setOpen(null);
    api.get<MsgHeader[]>(`/mail/${activeId}/messages?folder=INBOX&limit=50`)
      .then(setMessages)
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false));
  }
  useEffect(reload, [activeId]);

  async function openMsg(uid: string) {
    if (activeId == null) return;
    setErr("");
    try { setOpen(await api.get<MsgDetail>(`/mail/${activeId}/messages/${uid}?folder=INBOX`)); }
    catch (e) { setErr((e as Error).message); }
  }

  if (accounts.length === 0) {
    return <p className="muted">{t("mail.noAccount")}</p>;
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <select value={activeId ?? ""} onChange={(e) => setActiveId(Number(e.target.value))} style={{ maxWidth: 320 }}>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.label || a.email}</option>)}
        </select>
        <button className="primary" onClick={() => setDraft(emptyDraft())}>{t("mail.newMail")}</button>
        <span className="grow" />
        <button className="ghost" onClick={reload}>↻</button>
        <span className="label">{t("mail.inbox")}</span>
      </div>

      {err && <div className="err">{err}</div>}
      {loading && <p className="muted">{t("mail.loadingMessages")}</p>}

      {open ? (
        <div className="card" style={{ padding: "1.2rem" }}>
          <div className="row" style={{ marginBottom: "0.6rem" }}>
            <button className="ghost" onClick={() => setOpen(null)}>{t("mail.back")}</button>
            <span className="grow" />
            <button onClick={() => setDraft(replyDraft(open, t))}>{t("mail.reply")}</button>
            <button onClick={() => setDraft(forwardDraft(open, t))}>{t("mail.forward")}</button>
          </div>
          <h2 style={{ marginBottom: "0.2rem" }}>{open.subject || t("mail.noSubject")}</h2>
          <div className="mail-from">{open.from} · {open.date}</div>
          <hr style={{ borderColor: "var(--self-line)", margin: "1rem 0" }} />
          <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>
            {open.text || open.html.replace(/<[^>]+>/g, "") || t("mail.emptyBody")}
          </div>
        </div>
      ) : (
        <div className="mail-list">
          {messages.map((m) => (
            <div className={`mail-row ${m.seen ? "" : "unseen"}`} key={m.uid} onClick={() => openMsg(m.uid)}>
              <div>
                <div className="mail-subj">{m.subject || t("mail.noSubject")}</div>
                <div className="mail-from">{m.from}</div>
              </div>
              <div className="muted" style={{ fontSize: "0.78rem" }}>{m.date?.slice(0, 16)}</div>
            </div>
          ))}
          {!loading && messages.length === 0 && <p className="muted">{t("mail.noMessages")}</p>}
        </div>
      )}

      {draft && activeId != null && (
        <Compose accountId={activeId} draft={draft} onClose={() => setDraft(null)} />
      )}
    </div>
  );
}

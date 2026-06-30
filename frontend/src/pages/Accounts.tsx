import { useEffect, useState } from "react";
import { api, type Account } from "../lib/api";
import { useLang } from "../lib/i18n";
import { confirmDialog } from "../lib/dialog";
import { RichEditor } from "../components/RichEditor";

type Form = {
  label: string; email: string; password: string;
  imap_host: string; imap_port: number; imap_ssl: boolean;
  smtp_host: string; smtp_port: number; smtp_starttls: boolean;
  auth_user: string; protocol: string; signature: string;
  spam_purge_days: number;
  trash_purge_days: number;
};

const EMPTY: Form = {
  label: "", email: "", password: "",
  imap_host: "", imap_port: 993, imap_ssl: true,
  smtp_host: "", smtp_port: 587, smtp_starttls: true,
  auth_user: "", protocol: "imap", signature: "", spam_purge_days: -1, trash_purge_days: -1,
};

function formFrom(a: Account): Form {
  return {
    label: a.label, email: a.email, password: "",
    imap_host: a.imap_host, imap_port: a.imap_port, imap_ssl: a.imap_ssl,
    smtp_host: a.smtp_host, smtp_port: a.smtp_port, smtp_starttls: a.smtp_starttls,
    auth_user: a.auth_user, protocol: a.protocol, signature: a.signature ?? "",
    spam_purge_days: a.spam_purge_days ?? -1,
    trash_purge_days: a.trash_purge_days ?? -1,
  };
}

export function Accounts() {
  const { t } = useLang();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<Form>({ ...EMPTY });
  const [editId, setEditId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<Form>({ ...EMPTY });
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  async function load() {
    try { setAccounts(await api.get<Account[]>("/accounts")); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault(); setErr(""); setMsg("");
    try {
      await api.post<Account>("/accounts", form);
      setForm({ ...EMPTY }); setShowAdd(false);
      setMsg(t("accounts.added")); load();
    } catch (e) { setErr((e as Error).message); }
  }
  function startEdit(a: Account) { setEditId(a.id); setEditForm(formFrom(a)); setErr(""); setMsg(""); }
  async function saveEdit(id: number) {
    setErr(""); setMsg("");
    try {
      const { password, ...rest } = editForm;
      const payload = password ? { ...rest, password } : rest;  // leeres PW = nicht ändern
      await api.patch<Account>(`/accounts/${id}`, payload);
      setEditId(null); setMsg(t("accounts.saved")); load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function test(a: Account) {
    setErr(""); setMsg(t("accounts.testing"));
    const r = await api.post<{ ok: boolean; error?: string; folders?: string[] }>(`/accounts/${a.id}/test`);
    setMsg(r.ok ? t("accounts.testOk", { n: r.folders?.length ?? 0 }) : t("accounts.testErr", { error: r.error ?? "" }));
  }
  async function remove(a: Account) {
    if (!(await confirmDialog(t("accounts.confirmDelete", { name: a.label || a.email })))) return;
    await api.del(`/accounts/${a.id}`); load();
  }
  async function purgeSpamNow(a: Account) {
    if (!(await confirmDialog(t("accounts.spamPurgeConfirm")))) return;
    setErr(""); setMsg(t("accounts.spamPurging"));
    try {
      const r = await api.post<{ deleted: number }>(`/mail/${a.id}/spam/purge`);
      setMsg(t("accounts.spamPurged", { n: r.deleted }));
    } catch (e) { setErr((e as Error).message); }
  }
  async function purgeTrashNow(a: Account) {
    if (!(await confirmDialog(t("accounts.trashPurgeConfirm")))) return;
    setErr(""); setMsg(t("accounts.trashPurging"));
    try {
      const r = await api.post<{ deleted: number }>(`/mail/${a.id}/trash/purge`);
      setMsg(t("accounts.trashPurged", { n: r.deleted }));
    } catch (e) { setErr((e as Error).message); }
  }

  // Gemeinsames Feld-Formular für Anlegen + Bearbeiten.
  function fields(f: Form, upd: (patch: Partial<Form>) => void, isEdit: boolean) {
    return (
      <div className="stack" style={{ gap: "0.7rem" }}>
        <div className="acc-grid">
          <label className="stack"><span className="label">{t("common.label")}</span>
            <input value={f.label} onChange={(e) => upd({ label: e.target.value })} /></label>
          <label className="stack"><span className="label">{t("accounts.emailAddress")}</span>
            <input value={f.email} onChange={(e) => upd({ email: e.target.value })} required /></label>
        </div>
        <label className="stack">
          <span className="label">{isEdit ? t("accounts.passwordChange") : t("accounts.appPassword")}</span>
          <input type="password" placeholder={isEdit ? t("accounts.passwordKeep") : ""} value={f.password}
            onChange={(e) => upd({ password: e.target.value })} required={!isEdit} />
        </label>
        <fieldset className="acc-fieldset">
          <legend>IMAP</legend>
          <div className="acc-grid">
            <label className="stack"><span className="label">{t("accounts.imapHost")}</span>
              <input value={f.imap_host} onChange={(e) => upd({ imap_host: e.target.value })} /></label>
            <label className="stack"><span className="label">{t("accounts.port")}</span>
              <input type="number" value={f.imap_port} onChange={(e) => upd({ imap_port: Number(e.target.value) })} /></label>
          </div>
          <label className="acc-check"><input type="checkbox" checked={f.imap_ssl} onChange={(e) => upd({ imap_ssl: e.target.checked })} /> SSL/TLS</label>
        </fieldset>
        <fieldset className="acc-fieldset">
          <legend>SMTP</legend>
          <div className="acc-grid">
            <label className="stack"><span className="label">{t("accounts.smtpHost")}</span>
              <input value={f.smtp_host} onChange={(e) => upd({ smtp_host: e.target.value })} /></label>
            <label className="stack"><span className="label">{t("accounts.port")}</span>
              <input type="number" value={f.smtp_port} onChange={(e) => upd({ smtp_port: Number(e.target.value) })} /></label>
          </div>
          <label className="acc-check"><input type="checkbox" checked={f.smtp_starttls} onChange={(e) => upd({ smtp_starttls: e.target.checked })} /> STARTTLS</label>
        </fieldset>
      </div>
    );
  }

  return (
    <div className="acc-page">
      <div className="row" style={{ marginBottom: "1rem", alignItems: "center" }}>
        <h2 style={{ margin: 0, fontSize: "1.15rem" }}>{t("nav.accounts")}</h2>
        <span className="grow" />
        <button className="primary" onClick={() => setShowAdd((s) => !s)}>
          {showAdd ? t("common.cancel") : "＋ " + t("accounts.addAccount")}
        </button>
      </div>

      {showAdd && (
        <form className="card stack" style={{ padding: "1rem", marginBottom: "1.2rem", gap: "0.7rem" }} onSubmit={add}>
          <div className="label">{t("accounts.new")}</div>
          {fields(form, (p) => setForm((f) => ({ ...f, ...p })), false)}
          <div className="row"><span className="grow" /><button className="primary">{t("accounts.save")}</button></div>
        </form>
      )}

      {err && <div className="err">{err}</div>}
      {msg && <div className="muted" style={{ marginBottom: "0.8rem" }}>{msg}</div>}

      <div className="stack" style={{ gap: "0.7rem" }}>
        {accounts.map((a) => (
          <div className="card acc-card" key={a.id}>
            <div className="row acc-card-head">
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="acc-card-title">{a.label || a.email}</div>
                <div className="mail-from">{a.email} · {a.imap_host || "—"}</div>
              </div>
              {editId !== a.id && <>
                <button className="ghost" onClick={() => test(a)}>{t("accounts.test")}</button>
                <button className="ghost" onClick={() => startEdit(a)}>✎ {t("accounts.edit")}</button>
                <button className="ghost" onClick={() => remove(a)} title={t("common.remove")}>🗑</button>
              </>}
            </div>

            {editId === a.id && (
              <div className="stack" style={{ gap: "0.9rem", marginTop: "0.8rem" }}>
                {fields(editForm, (p) => setEditForm((f) => ({ ...f, ...p })), true)}
                <div className="stack" style={{ gap: "0.35rem" }}>
                  <span className="label">✍ {t("accounts.signature")}</span>
                  <RichEditor value={editForm.signature} onChange={(html) => setEditForm((f) => ({ ...f, signature: html }))}
                    placeholder={t("accounts.signaturePlaceholder")} />
                </div>
                <fieldset className="acc-fieldset">
                  <legend>🗑 {t("accounts.spamSection")}</legend>
                  <label className="stack" style={{ gap: "0.35rem" }}>
                    <span className="label">{t("accounts.spamPurgeLabel")}</span>
                    <select value={editForm.spam_purge_days}
                      onChange={(e) => setEditForm((f) => ({ ...f, spam_purge_days: Number(e.target.value) }))}>
                      <option value={-1}>{t("accounts.spamOff")}</option>
                      <option value={0}>{t("accounts.spamNow")}</option>
                      <option value={7}>{t("accounts.spam7")}</option>
                      <option value={30}>{t("accounts.spam30")}</option>
                    </select>
                  </label>
                  <p className="mail-from" style={{ margin: "0.4rem 0 0.6rem" }}>{t("accounts.spamPurgeHint")}</p>
                  <button type="button" className="ghost" onClick={() => purgeSpamNow(a)}>{t("accounts.spamPurgeNow")}</button>
                </fieldset>
                <fieldset className="acc-fieldset">
                  <legend>♻ {t("accounts.trashSection")}</legend>
                  <label className="stack" style={{ gap: "0.35rem" }}>
                    <span className="label">{t("accounts.trashPurgeLabel")}</span>
                    <select value={editForm.trash_purge_days}
                      onChange={(e) => setEditForm((f) => ({ ...f, trash_purge_days: Number(e.target.value) }))}>
                      <option value={-1}>{t("accounts.spamOff")}</option>
                      <option value={0}>{t("accounts.spamNow")}</option>
                      <option value={7}>{t("accounts.spam7")}</option>
                      <option value={30}>{t("accounts.spam30")}</option>
                    </select>
                  </label>
                  <p className="mail-from" style={{ margin: "0.4rem 0 0.6rem" }}>{t("accounts.trashPurgeHint")}</p>
                  <button type="button" className="ghost" onClick={() => purgeTrashNow(a)}>{t("accounts.trashPurgeNow")}</button>
                </fieldset>
                <div className="row">
                  <button className="ghost" onClick={() => test(a)}>{t("accounts.test")}</button>
                  <span className="grow" />
                  <button className="ghost" onClick={() => setEditId(null)}>{t("common.cancel")}</button>
                  <button className="primary" onClick={() => saveEdit(a.id)}>{t("accounts.save")}</button>
                </div>
              </div>
            )}
          </div>
        ))}
        {accounts.length === 0 && !showAdd && <p className="muted">{t("accounts.empty")}</p>}
      </div>
    </div>
  );
}

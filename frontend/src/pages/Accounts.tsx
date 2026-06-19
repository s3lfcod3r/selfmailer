import { useEffect, useState } from "react";
import { api, type Account } from "../lib/api";
import { useLang } from "../lib/i18n";

const EMPTY = {
  label: "", email: "", password: "",
  imap_host: "", imap_port: 993, imap_ssl: true,
  smtp_host: "", smtp_port: 587, smtp_starttls: true,
  auth_user: "", protocol: "imap",
};

export function Accounts() {
  const { t } = useLang();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  async function load() {
    try { setAccounts(await api.get<Account[]>("/accounts")); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setMsg("");
    try {
      await api.post<Account>("/accounts", form);
      setForm({ ...EMPTY });
      setMsg(t("accounts.added"));
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function test(a: Account) {
    setErr(""); setMsg(t("accounts.testing"));
    const r = await api.post<{ ok: boolean; error?: string; folders?: string[] }>(`/accounts/${a.id}/test`);
    setMsg(r.ok
      ? t("accounts.testOk", { n: r.folders?.length ?? 0 })
      : t("accounts.testErr", { error: r.error ?? "" }));
  }
  async function remove(a: Account) {
    await api.del(`/accounts/${a.id}`);
    load();
  }

  return (
    <div>
      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={add}>
        <div className="label">{t("accounts.new")}</div>
        <div className="row">
          <input placeholder={t("common.label")} value={form.label} onChange={(e) => set("label", e.target.value)} />
          <input placeholder={t("accounts.emailAddress")} value={form.email} onChange={(e) => set("email", e.target.value)} required />
        </div>
        <input type="password" placeholder={t("accounts.appPassword")} value={form.password} onChange={(e) => set("password", e.target.value)} required />
        <div className="row">
          <input placeholder={t("accounts.imapHost")} value={form.imap_host} onChange={(e) => set("imap_host", e.target.value)} />
          <input type="number" placeholder="993" value={form.imap_port} onChange={(e) => set("imap_port", Number(e.target.value))} style={{ maxWidth: 110 }} />
        </div>
        <div className="row">
          <input placeholder={t("accounts.smtpHost")} value={form.smtp_host} onChange={(e) => set("smtp_host", e.target.value)} />
          <input type="number" placeholder="587" value={form.smtp_port} onChange={(e) => set("smtp_port", Number(e.target.value))} style={{ maxWidth: 110 }} />
        </div>
        <div className="row">
          <span className="grow" />
          <button className="primary">{t("accounts.save")}</button>
        </div>
      </form>

      {err && <div className="err">{err}</div>}
      {msg && <div className="muted" style={{ marginBottom: "0.8rem" }}>{msg}</div>}

      <div className="stack">
        {accounts.map((a) => (
          <div className="card row" style={{ padding: "0.8rem 1rem" }} key={a.id}>
            <div className="grow">
              <div style={{ fontWeight: 600 }}>{a.label || a.email}</div>
              <div className="mail-from">{a.email} · {a.imap_host || "—"}</div>
            </div>
            <button onClick={() => test(a)}>{t("accounts.test")}</button>
            <button className="ghost" onClick={() => remove(a)}>{t("common.remove")}</button>
          </div>
        ))}
        {accounts.length === 0 && <p className="muted">{t("accounts.empty")}</p>}
      </div>
    </div>
  );
}

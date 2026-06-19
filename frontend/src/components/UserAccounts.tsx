import { useEffect, useState } from "react";
import { api, type Account } from "../lib/api";

const EMPTY = {
  label: "", email: "", password: "",
  imap_host: "", imap_port: 993, imap_ssl: true,
  smtp_host: "", smtp_port: 587, smtp_starttls: true,
  auth_user: "", protocol: "imap",
};

// Admin-Ansicht: Mailkonten eines bestimmten Users anlegen/entfernen.
export function UserAccounts({ userId }: { userId: number }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [err, setErr] = useState("");

  async function load() {
    try { setAccounts(await api.get<Account[]>(`/admin/users/${userId}/accounts`)); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, [userId]);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      await api.post<Account>(`/admin/users/${userId}/accounts`, form);
      setForm({ ...EMPTY });
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function remove(a: Account) {
    setErr("");
    try { await api.del(`/admin/users/${userId}/accounts/${a.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <div style={{ padding: "0.8rem 1rem 0.2rem", borderTop: "1px solid var(--self-line)" }}>
      <div className="stack" style={{ marginBottom: "0.8rem" }}>
        {accounts.map((a) => (
          <div className="row" key={a.id}>
            <span className="grow">{a.label || a.email} <span className="mail-from">· {a.imap_host || "—"}</span></span>
            <button className="ghost" onClick={() => remove(a)}>Entfernen</button>
          </div>
        ))}
        {accounts.length === 0 && <span className="muted">Noch kein Konto für diesen User.</span>}
      </div>

      <form className="stack" onSubmit={add}>
        <div className="row">
          <input placeholder="Bezeichnung" value={form.label} onChange={(e) => set("label", e.target.value)} />
          <input placeholder="E-Mail" value={form.email} onChange={(e) => set("email", e.target.value)} required />
        </div>
        <input type="password" placeholder="Passwort / App-Passwort" value={form.password} onChange={(e) => set("password", e.target.value)} required />
        <div className="row">
          <input placeholder="IMAP-Host" value={form.imap_host} onChange={(e) => set("imap_host", e.target.value)} />
          <input type="number" value={form.imap_port} onChange={(e) => set("imap_port", Number(e.target.value))} style={{ maxWidth: 100 }} />
          <input placeholder="SMTP-Host" value={form.smtp_host} onChange={(e) => set("smtp_host", e.target.value)} />
          <input type="number" value={form.smtp_port} onChange={(e) => set("smtp_port", Number(e.target.value))} style={{ maxWidth: 100 }} />
        </div>
        {err && <div className="err">{err}</div>}
        <div className="row">
          <span className="grow" />
          <button className="primary">Konto für User anlegen</button>
        </div>
      </form>
    </div>
  );
}

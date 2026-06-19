import { useEffect, useState } from "react";
import { api, type Account } from "../lib/api";

const EMPTY = {
  label: "", email: "", password: "",
  imap_host: "", imap_port: 993, imap_ssl: true,
  smtp_host: "", smtp_port: 587, smtp_starttls: true,
  auth_user: "", protocol: "imap",
};

export function Accounts() {
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
      setMsg("Konto hinzugefügt.");
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function test(a: Account) {
    setErr(""); setMsg("Teste…");
    const r = await api.post<{ ok: boolean; error?: string; folders?: string[] }>(`/accounts/${a.id}/test`);
    setMsg(r.ok ? `OK – ${r.folders?.length ?? 0} Ordner gefunden.` : `Fehler: ${r.error}`);
  }
  async function remove(a: Account) {
    await api.del(`/accounts/${a.id}`);
    load();
  }

  return (
    <div>
      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={add}>
        <div className="label">Neues Mailkonto</div>
        <div className="row">
          <input placeholder="Bezeichnung" value={form.label} onChange={(e) => set("label", e.target.value)} />
          <input placeholder="E-Mail-Adresse" value={form.email} onChange={(e) => set("email", e.target.value)} required />
        </div>
        <input type="password" placeholder="Passwort / App-Passwort" value={form.password} onChange={(e) => set("password", e.target.value)} required />
        <div className="row">
          <input placeholder="IMAP-Host (z. B. imap.web.de)" value={form.imap_host} onChange={(e) => set("imap_host", e.target.value)} />
          <input type="number" placeholder="993" value={form.imap_port} onChange={(e) => set("imap_port", Number(e.target.value))} style={{ maxWidth: 110 }} />
        </div>
        <div className="row">
          <input placeholder="SMTP-Host (z. B. smtp.web.de)" value={form.smtp_host} onChange={(e) => set("smtp_host", e.target.value)} />
          <input type="number" placeholder="587" value={form.smtp_port} onChange={(e) => set("smtp_port", Number(e.target.value))} style={{ maxWidth: 110 }} />
        </div>
        <div className="row">
          <span className="grow" />
          <button className="primary">Konto speichern</button>
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
            <button onClick={() => test(a)}>Verbindung testen</button>
            <button className="ghost" onClick={() => remove(a)}>Entfernen</button>
          </div>
        ))}
        {accounts.length === 0 && <p className="muted">Noch kein Konto. Lege oben eines an.</p>}
      </div>
    </div>
  );
}

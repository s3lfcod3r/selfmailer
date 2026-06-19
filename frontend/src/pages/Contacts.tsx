import { useEffect, useState } from "react";
import { api, type Contact } from "../lib/api";

const EMPTY = { first_name: "", last_name: "", email: "", phone: "", organization: "", notes: "" };

export function Contacts() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [q, setQ] = useState("");
  const [form, setForm] = useState({ ...EMPTY });
  const [err, setErr] = useState("");

  async function load(query = q) {
    try { setContacts(await api.get<Contact[]>(`/contacts?q=${encodeURIComponent(query)}`)); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(""); }, []);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!form.first_name && !form.last_name && !form.email) { setErr("Name oder E-Mail angeben."); return; }
    try { await api.post<Contact>("/contacts", form); setForm({ ...EMPTY }); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function remove(ct: Contact) {
    try { await api.del(`/contacts/${ct.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <input placeholder="Suche (Name, E-Mail, Firma)…" value={q}
          onChange={(e) => { setQ(e.target.value); load(e.target.value); }} />
      </div>

      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={add}>
        <div className="label">Neuer Kontakt</div>
        <div className="row">
          <input placeholder="Vorname" value={form.first_name} onChange={(e) => set("first_name", e.target.value)} />
          <input placeholder="Nachname" value={form.last_name} onChange={(e) => set("last_name", e.target.value)} />
        </div>
        <div className="row">
          <input placeholder="E-Mail" value={form.email} onChange={(e) => set("email", e.target.value)} />
          <input placeholder="Telefon" value={form.phone} onChange={(e) => set("phone", e.target.value)} />
        </div>
        <input placeholder="Firma / Organisation" value={form.organization} onChange={(e) => set("organization", e.target.value)} />
        <div className="row">
          <span className="grow" />
          <button className="primary">Kontakt speichern</button>
        </div>
      </form>

      {err && <div className="err">{err}</div>}
      {contacts.length === 0 && <p className="muted">Keine Kontakte gefunden.</p>}

      <div className="notes-grid">
        {contacts.map((ct) => (
          <div className="card note" key={ct.id}>
            <div className="note-title">{[ct.first_name, ct.last_name].filter(Boolean).join(" ") || "(ohne Namen)"}</div>
            <div className="note-body">
              {ct.organization && <div className="muted">{ct.organization}</div>}
              {ct.email && <div>✉ {ct.email}</div>}
              {ct.phone && <div>☎ {ct.phone}</div>}
            </div>
            <div className="note-foot">
              <span />
              <button className="ghost" onClick={() => remove(ct)}>🗑</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

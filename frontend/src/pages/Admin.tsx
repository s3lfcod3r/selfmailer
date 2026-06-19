import { useEffect, useState } from "react";
import { api, type User } from "../lib/api";
import { UserAccounts } from "../components/UserAccounts";

const EMPTY = { username: "", password: "", display_name: "", role: "user" };

export function Admin({ meId }: { meId: number }) {
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [expanded, setExpanded] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  async function load() {
    try { setUsers(await api.get<User[]>("/admin/users")); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setMsg("");
    try {
      await api.post<User>("/admin/users", form);
      setForm({ ...EMPTY });
      setMsg(`User „${form.username}" angelegt.`);
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function toggleActive(u: User) {
    setErr("");
    try { await api.patch(`/admin/users/${u.id}/active?active=${!u.is_active}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function resetPw(u: User) {
    const pw = prompt(`Neues Passwort für ${u.username} (min. 8 Zeichen):`);
    if (!pw) return;
    setErr(""); setMsg("");
    try { await api.patch(`/admin/users/${u.id}/password?new_password=${encodeURIComponent(pw)}`); setMsg("Passwort gesetzt."); }
    catch (e) { setErr((e as Error).message); }
  }
  async function remove(u: User) {
    if (!confirm(`User „${u.username}" wirklich löschen?`)) return;
    setErr("");
    try { await api.del(`/admin/users/${u.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <div>
      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={create}>
        <div className="label">Neuen Benutzer anlegen</div>
        <div className="row">
          <input placeholder="Benutzername / E-Mail" value={form.username} onChange={(e) => set("username", e.target.value)} required />
          <input placeholder="Anzeigename" value={form.display_name} onChange={(e) => set("display_name", e.target.value)} />
        </div>
        <div className="row">
          <input type="password" placeholder="Passwort (min. 8)" value={form.password} onChange={(e) => set("password", e.target.value)} required />
          <select value={form.role} onChange={(e) => set("role", e.target.value)} style={{ maxWidth: 160 }}>
            <option value="user">Benutzer</option>
            <option value="admin">Admin</option>
          </select>
          <button className="primary">Anlegen</button>
        </div>
      </form>

      {err && <div className="err">{err}</div>}
      {msg && <div className="muted" style={{ marginBottom: "0.8rem" }}>{msg}</div>}

      <div className="stack">
        {users.map((u) => (
          <div className="card" style={{ opacity: u.is_active ? 1 : 0.6 }} key={u.id}>
            <div className="row" style={{ padding: "0.8rem 1rem" }}>
              <div className="grow">
                <div style={{ fontWeight: 600 }}>
                  {u.display_name || u.username}
                  {u.role === "admin" && <span className="label" style={{ marginLeft: 8 }}>Admin</span>}
                  {u.id === meId && <span className="label" style={{ marginLeft: 8 }}>Du</span>}
                </div>
                <div className="mail-from">{u.username} · {u.is_active ? "aktiv" : "gesperrt"}</div>
              </div>
              <button onClick={() => setExpanded(expanded === u.id ? null : u.id)}>
                {expanded === u.id ? "Konten ▲" : "Konten…"}
              </button>
              <button onClick={() => resetPw(u)}>Passwort…</button>
              {u.id !== meId && (
                <button onClick={() => toggleActive(u)}>{u.is_active ? "Sperren" : "Entsperren"}</button>
              )}
              {u.id !== meId && <button className="ghost" onClick={() => remove(u)}>Löschen</button>}
            </div>
            {expanded === u.id && <UserAccounts userId={u.id} />}
          </div>
        ))}
      </div>
    </div>
  );
}

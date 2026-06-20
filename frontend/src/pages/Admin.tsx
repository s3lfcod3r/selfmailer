import { useEffect, useState } from "react";
import { api, type User } from "../lib/api";
import { useLang } from "../lib/i18n";
import { confirmDialog, promptDialog } from "../lib/dialog";
import { UserAccounts } from "../components/UserAccounts";

const EMPTY = { username: "", password: "", display_name: "", role: "user" };

export function Admin({ meId }: { meId: number }) {
  const { t } = useLang();
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
      const name = form.username;
      setForm({ ...EMPTY });
      setMsg(t("admin.created", { name }));
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function toggleActive(u: User) {
    setErr("");
    try { await api.patch(`/admin/users/${u.id}/active?active=${!u.is_active}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function resetPw(u: User) {
    const pw = await promptDialog(t("admin.resetPrompt", { name: u.username }));
    if (!pw) return;
    setErr(""); setMsg("");
    try { await api.patch(`/admin/users/${u.id}/password`, { new_password: pw }); setMsg(t("admin.pwSet")); }
    catch (e) { setErr((e as Error).message); }
  }
  async function remove(u: User) {
    if (!(await confirmDialog(t("admin.deleteConfirm", { name: u.username })))) return;
    setErr("");
    try { await api.del(`/admin/users/${u.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <div>
      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={create}>
        <div className="label">{t("admin.newUser")}</div>
        <div className="row">
          <input placeholder={t("login.userLabel")} value={form.username} onChange={(e) => set("username", e.target.value)} required />
          <input placeholder={t("common.displayName")} value={form.display_name} onChange={(e) => set("display_name", e.target.value)} />
        </div>
        <div className="row">
          <input type="password" placeholder={t("admin.pwMin")} value={form.password} onChange={(e) => set("password", e.target.value)} required />
          <select value={form.role} onChange={(e) => set("role", e.target.value)} style={{ maxWidth: 160 }}>
            <option value="user">{t("admin.roleUser")}</option>
            <option value="admin">{t("admin.roleAdmin")}</option>
          </select>
          <button className="primary">{t("admin.create")}</button>
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
                  {u.role === "admin" && <span className="label" style={{ marginLeft: 8 }}>{t("admin.roleAdmin")}</span>}
                  {u.id === meId && <span className="label" style={{ marginLeft: 8 }}>{t("admin.you")}</span>}
                </div>
                <div className="mail-from">{u.username} · {u.is_active ? t("admin.active") : t("admin.blocked")}</div>
              </div>
              <button onClick={() => setExpanded(expanded === u.id ? null : u.id)}>
                {expanded === u.id ? t("admin.accountsOpen") : t("admin.accountsClosed")}
              </button>
              <button onClick={() => resetPw(u)}>{t("admin.pwButton")}</button>
              {u.id !== meId && (
                <button onClick={() => toggleActive(u)}>{u.is_active ? t("admin.block") : t("admin.unblock")}</button>
              )}
              {u.id !== meId && <button className="ghost" onClick={() => remove(u)}>{t("common.delete")}</button>}
            </div>
            {expanded === u.id && <UserAccounts userId={u.id} />}
          </div>
        ))}
      </div>
    </div>
  );
}

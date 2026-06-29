import { useEffect, useState } from "react";
import { api, type Account, type Rule } from "../lib/api";
import { useLang } from "../lib/i18n";

const EMPTY = { field: "from", value: "", target_folder: "", mark_read: false, star: false, delete_msg: false };

export function Rules() {
  const { t } = useLang();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [folders, setFolders] = useState<string[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [editId, setEditId] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<Account[]>("/accounts").then((a) => { setAccounts(a); if (a.length) setActiveId(a[0].id); });
  }, []);

  function loadRules(id: number) {
    api.get<Rule[]>(`/mail/${id}/rules`).then(setRules).catch(() => setRules([]));
  }
  useEffect(() => {
    if (activeId == null) return;
    loadRules(activeId);
    api.get<string[]>(`/mail/${activeId}/folders`).then(setFolders).catch(() => setFolders([]));
  }, [activeId]);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) { setForm((f) => ({ ...f, [k]: v })); }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setMsg("");
    if (activeId == null) return;
    if (!form.value.trim()) { setErr(t("rules.needValue")); return; }
    if (!form.target_folder && !form.mark_read && !form.star && !form.delete_msg) { setErr(t("rules.needAction")); return; }
    try {
      if (editId != null) await api.patch(`/mail/${activeId}/rules/${editId}`, form);
      else await api.post(`/mail/${activeId}/rules`, form);
      setForm({ ...EMPTY }); setEditId(null);
      loadRules(activeId);
    } catch (e) { setErr((e as Error).message); }
  }
  function startEdit(r: Rule) {
    setEditId(r.id);
    setForm({ field: r.field, value: r.value, target_folder: r.target_folder, mark_read: r.mark_read, star: r.star, delete_msg: r.delete_msg });
    setErr(""); setMsg("");
  }
  function cancelEdit() { setEditId(null); setForm({ ...EMPTY }); }
  async function remove(r: Rule) {
    if (activeId == null) return;
    try { await api.del(`/mail/${activeId}/rules/${r.id}`); setRules((rs) => rs.filter((x) => x.id !== r.id)); }
    catch (e) { setErr((e as Error).message); }
  }
  async function applyNow() {
    if (activeId == null) return;
    setBusy(true); setErr(""); setMsg("");
    try {
      const res = await api.post<{ affected: number }>(`/mail/${activeId}/rules/apply`);
      setMsg(t("rules.applied", { n: res.affected }));
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  function fieldLabel(f: string): string {
    if (f === "to") return t("rules.to");
    if (f === "subject") return t("filter.subject");
    if (f === "from_domain") return t("rules.fromDomain");
    return t("filter.from");
  }

  if (accounts.length === 0) return <p className="muted">{t("mail.noAccount")}</p>;

  return (
    <div style={{ maxWidth: 760 }}>
      {accounts.length > 1 && (
        <select value={activeId ?? ""} onChange={(e) => setActiveId(Number(e.target.value))} style={{ maxWidth: 260, marginBottom: "1rem" }}>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.label || a.email}</option>)}
        </select>
      )}

      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={submit}>
        <div className="label">{editId != null ? t("rules.edit") : t("rules.new")}</div>
        <div className="row">
          <select value={form.field} onChange={(e) => set("field", e.target.value)} style={{ maxWidth: 180 }}>
            <option value="from">{t("filter.from")}</option>
            <option value="from_domain">{t("rules.fromDomain")}</option>
            <option value="to">{t("rules.to")}</option>
            <option value="subject">{t("filter.subject")}</option>
          </select>
          <input placeholder={t("rules.valuePlaceholder")} value={form.value} onChange={(e) => set("value", e.target.value)} />
        </div>
        <div className="row">
          <label className="label" style={{ minWidth: 120 }}>{t("rules.moveTo")}</label>
          <select value={form.target_folder} disabled={form.delete_msg} onChange={(e) => set("target_folder", e.target.value)}>
            <option value="">— {t("rules.noMove")} —</option>
            {folders.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
        <div className="row">
          <label style={{ display: "flex", gap: "0.4rem", alignItems: "center", opacity: form.delete_msg ? 0.5 : 1 }}>
            <input type="checkbox" style={{ width: "auto" }} disabled={form.delete_msg} checked={form.star} onChange={(e) => set("star", e.target.checked)} /> {t("rules.star")}
          </label>
          <label style={{ display: "flex", gap: "0.4rem", alignItems: "center", opacity: form.delete_msg ? 0.5 : 1 }}>
            <input type="checkbox" style={{ width: "auto" }} disabled={form.delete_msg} checked={form.mark_read} onChange={(e) => set("mark_read", e.target.checked)} /> {t("rules.markRead")}
          </label>
          <label style={{ display: "flex", gap: "0.4rem", alignItems: "center", color: "var(--self-danger, #d44)" }}>
            <input type="checkbox" style={{ width: "auto" }} checked={form.delete_msg} onChange={(e) => set("delete_msg", e.target.checked)} /> {t("rules.delete")}
          </label>
          <span className="grow" />
          {editId != null && <button type="button" className="ghost" onClick={cancelEdit}>{t("common.cancel")}</button>}
          <button className="primary">{editId != null ? t("rules.save") : t("rules.add")}</button>
        </div>
      </form>

      {err && <div className="err">{err}</div>}
      {msg && <div className="muted" style={{ marginBottom: "0.8rem" }}>{msg}</div>}

      <div className="row" style={{ marginBottom: "0.8rem" }}>
        <span className="label">{t("rules.list")}</span>
        <span className="grow" />
        <button onClick={applyNow} disabled={busy}>{busy ? "…" : t("rules.applyNow")}</button>
      </div>

      {rules.length === 0 && <p className="muted">{t("rules.empty")}</p>}
      <div className="stack">
        {rules.map((r) => (
          <div className="card row" style={{ padding: "0.7rem 1rem" }} key={r.id}>
            <div className="grow">
              <div style={{ fontWeight: 600 }}>
                {fieldLabel(r.field)} {t("rules.contains")} „{r.value}“
              </div>
              <div className="mail-from">
                {r.delete_msg
                  ? <span style={{ color: "var(--self-danger, #d44)", fontWeight: 600 }}>→ {t("rules.delete")}</span>
                  : <>→ {r.target_folder ? `${t("rules.moveTo")}: ${r.target_folder}` : ""}{r.star ? " ★" : ""}{r.mark_read ? ` · ${t("rules.markRead")}` : ""}</>}
              </div>
            </div>
            <button className="ghost" onClick={() => startEdit(r)}>{t("rules.editBtn")}</button>
            <button className="ghost" onClick={() => remove(r)}>{t("common.delete")}</button>
          </div>
        ))}
      </div>
    </div>
  );
}

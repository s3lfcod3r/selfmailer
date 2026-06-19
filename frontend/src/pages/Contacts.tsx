import { useEffect, useState } from "react";
import { api, type Contact } from "../lib/api";
import { useLang, dateLocale } from "../lib/i18n";

const EMPTY = { first_name: "", last_name: "", email: "", phone: "", organization: "", birthday: "", notes: "" };
type Form = typeof EMPTY;

// Geburtstag schön anzeigen (Tag + Monat + Jahr in Sprach-Locale).
function fmtBirthday(iso: string, lang: string): string {
  const d = new Date(iso + "T00:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(dateLocale(lang as "de" | "en"), { day: "2-digit", month: "long", year: "numeric" });
}

export function Contacts() {
  const { t, lang } = useLang();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [q, setQ] = useState("");
  const [form, setForm] = useState<Form>({ ...EMPTY });
  const [edit, setEdit] = useState<Contact | null>(null);
  const [editForm, setEditForm] = useState<Form>({ ...EMPTY });
  const [err, setErr] = useState("");

  async function load(query = q) {
    try { setContacts(await api.get<Contact[]>(`/contacts?q=${encodeURIComponent(query)}`)); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(""); }, []);

  function set<K extends keyof Form>(k: K, v: Form[K]) { setForm((f) => ({ ...f, [k]: v })); }
  function setE<K extends keyof Form>(k: K, v: Form[K]) { setEditForm((f) => ({ ...f, [k]: v })); }

  // Leeren Geburtstag als null senden, damit das Backend (date | None) sauber bleibt.
  function payload(f: Form) {
    return { ...f, birthday: f.birthday || null };
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!form.first_name && !form.last_name && !form.email) { setErr(t("contacts.needNameOrEmail")); return; }
    try { await api.post<Contact>("/contacts", payload(form)); setForm({ ...EMPTY }); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function remove(ct: Contact) {
    try { await api.del(`/contacts/${ct.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  function openEdit(ct: Contact) {
    setEdit(ct);
    setEditForm({
      first_name: ct.first_name, last_name: ct.last_name, email: ct.email,
      phone: ct.phone, organization: ct.organization, birthday: ct.birthday ?? "", notes: ct.notes,
    });
  }
  async function saveEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!edit) return;
    setErr("");
    try { await api.patch<Contact>(`/contacts/${edit.id}`, payload(editForm)); setEdit(null); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <input placeholder={t("contacts.search")} value={q}
          onChange={(e) => { setQ(e.target.value); load(e.target.value); }} />
      </div>

      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={add}>
        <div className="label">{t("contacts.new")}</div>
        <div className="row">
          <input placeholder={t("contacts.firstName")} value={form.first_name} onChange={(e) => set("first_name", e.target.value)} />
          <input placeholder={t("contacts.lastName")} value={form.last_name} onChange={(e) => set("last_name", e.target.value)} />
        </div>
        <div className="row">
          <input placeholder={t("common.email")} value={form.email} onChange={(e) => set("email", e.target.value)} />
          <input placeholder={t("contacts.phone")} value={form.phone} onChange={(e) => set("phone", e.target.value)} />
        </div>
        <input placeholder={t("contacts.org")} value={form.organization} onChange={(e) => set("organization", e.target.value)} />
        <div className="row" style={{ alignItems: "center" }}>
          <label className="label" style={{ minWidth: 90 }}>🎂 {t("contacts.birthday")}</label>
          <input type="date" value={form.birthday} onChange={(e) => set("birthday", e.target.value)} />
          <span className="grow" />
          <button className="primary">{t("contacts.save")}</button>
        </div>
      </form>

      {err && <div className="err">{err}</div>}
      {contacts.length === 0 && <p className="muted">{t("contacts.empty")}</p>}

      <div className="notes-grid">
        {contacts.map((ct) => (
          <div className="card note" key={ct.id}>
            <button className="note-title contact-open" onClick={() => openEdit(ct)} title={t("contacts.edit")}>
              {[ct.first_name, ct.last_name].filter(Boolean).join(" ") || t("contacts.noName")}
            </button>
            <div className="note-body">
              {ct.organization && <div className="muted">{ct.organization}</div>}
              {ct.email && <div>✉ {ct.email}</div>}
              {ct.phone && <div>☎ {ct.phone}</div>}
              {ct.birthday && <div>🎂 {fmtBirthday(ct.birthday, lang)}</div>}
            </div>
            <div className="note-foot">
              <button className="ghost" onClick={() => openEdit(ct)}>✎</button>
              <button className="ghost" onClick={() => remove(ct)}>🗑</button>
            </div>
          </div>
        ))}
      </div>

      {edit && (
        <div className="modal-backdrop" onClick={() => setEdit(null)}>
          <form className="modal card stack" onClick={(e) => e.stopPropagation()} onSubmit={saveEdit}>
            <div className="topbar">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{t("contacts.editTitle")}</h2>
              <button type="button" className="ghost" onClick={() => setEdit(null)}>✕</button>
            </div>
            <div className="row">
              <input placeholder={t("contacts.firstName")} value={editForm.first_name} onChange={(e) => setE("first_name", e.target.value)} />
              <input placeholder={t("contacts.lastName")} value={editForm.last_name} onChange={(e) => setE("last_name", e.target.value)} />
            </div>
            <div className="row">
              <input placeholder={t("common.email")} value={editForm.email} onChange={(e) => setE("email", e.target.value)} />
              <input placeholder={t("contacts.phone")} value={editForm.phone} onChange={(e) => setE("phone", e.target.value)} />
            </div>
            <input placeholder={t("contacts.org")} value={editForm.organization} onChange={(e) => setE("organization", e.target.value)} />
            <div className="row" style={{ alignItems: "center" }}>
              <label className="label" style={{ minWidth: 90 }}>🎂 {t("contacts.birthday")}</label>
              <input type="date" value={editForm.birthday} onChange={(e) => setE("birthday", e.target.value)} />
            </div>
            <textarea placeholder={t("contacts.notesPlaceholder")} value={editForm.notes} onChange={(e) => setE("notes", e.target.value)} rows={3} />
            <div className="row">
              <span className="grow" />
              <button type="button" className="ghost" onClick={() => setEdit(null)}>{t("common.cancel")}</button>
              <button className="primary">{t("contacts.save")}</button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

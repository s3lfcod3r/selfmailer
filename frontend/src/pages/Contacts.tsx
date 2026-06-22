import { useEffect, useState } from "react";
import { api, type Contact, type DavAccount, type GcalCalendar } from "../lib/api";
import { useLang, dateLocale, type TFunc } from "../lib/i18n";
import { confirmDialog } from "../lib/dialog";

const EMPTY = {
  first_name: "", last_name: "", email: "", phone: "", mobile: "", work_phone: "",
  organization: "", title: "", website: "", street: "", postal_code: "", city: "",
  country: "", birthday: "", notes: "",
};
type Form = typeof EMPTY;

function fmtBirthday(iso: string, lang: string): string {
  const d = new Date(iso + "T00:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(dateLocale(lang as "de" | "en"), { day: "2-digit", month: "long", year: "numeric" });
}

function displayName(ct: Contact, fallback: string): string {
  return [ct.first_name, ct.last_name].filter(Boolean).join(" ") || ct.email || fallback;
}
function initials(ct: Contact): string {
  const a = ct.first_name?.[0] ?? "";
  const b = ct.last_name?.[0] ?? "";
  return (a + b || ct.email?.[0] || "?").toUpperCase();
}
function formFrom(ct: Contact): Form {
  return {
    first_name: ct.first_name, last_name: ct.last_name, email: ct.email, phone: ct.phone,
    mobile: ct.mobile, work_phone: ct.work_phone, organization: ct.organization, title: ct.title,
    website: ct.website, street: ct.street, postal_code: ct.postal_code, city: ct.city,
    country: ct.country, birthday: ct.birthday ?? "", notes: ct.notes,
  };
}

// Gemeinsamer Feldblock für Anlegen + Bearbeiten (DRY).
function ContactFields({ form, set, t }: { form: Form; set: <K extends keyof Form>(k: K, v: Form[K]) => void; t: TFunc }) {
  return (
    <>
      <div className="row">
        <input placeholder={t("contacts.firstName")} value={form.first_name} onChange={(e) => set("first_name", e.target.value)} />
        <input placeholder={t("contacts.lastName")} value={form.last_name} onChange={(e) => set("last_name", e.target.value)} />
      </div>
      <div className="row">
        <input placeholder={t("contacts.title")} value={form.title} onChange={(e) => set("title", e.target.value)} />
        <input placeholder={t("contacts.org")} value={form.organization} onChange={(e) => set("organization", e.target.value)} />
      </div>
      <div className="row">
        <input placeholder={t("common.email")} value={form.email} onChange={(e) => set("email", e.target.value)} />
        <input placeholder={t("contacts.website")} value={form.website} onChange={(e) => set("website", e.target.value)} />
      </div>
      <div className="row">
        <input placeholder={t("contacts.phone")} value={form.phone} onChange={(e) => set("phone", e.target.value)} />
        <input placeholder={t("contacts.mobile")} value={form.mobile} onChange={(e) => set("mobile", e.target.value)} />
        <input placeholder={t("contacts.workPhone")} value={form.work_phone} onChange={(e) => set("work_phone", e.target.value)} />
      </div>
      <input placeholder={t("contacts.street")} value={form.street} onChange={(e) => set("street", e.target.value)} />
      <div className="row">
        <input style={{ maxWidth: 120 }} placeholder={t("contacts.postalCode")} value={form.postal_code} onChange={(e) => set("postal_code", e.target.value)} />
        <input placeholder={t("contacts.city")} value={form.city} onChange={(e) => set("city", e.target.value)} />
        <input style={{ maxWidth: 140 }} placeholder={t("contacts.country")} value={form.country} onChange={(e) => set("country", e.target.value)} />
      </div>
      <div className="row" style={{ alignItems: "center" }}>
        <label className="label" style={{ minWidth: 90 }}>🎂 {t("contacts.birthday")}</label>
        <input type="date" value={form.birthday} onChange={(e) => set("birthday", e.target.value)} />
      </div>
      <textarea placeholder={t("contacts.notesPlaceholder")} value={form.notes} onChange={(e) => set("notes", e.target.value)} rows={2} />
    </>
  );
}

export function Contacts() {
  const { t, lang } = useLang();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<number | "new" | null>(null);
  const [form, setForm] = useState<Form>({ ...EMPTY });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // Geburtstage-Kalender (welcher Google-Kalender bekommt die Geburtstage).
  const [gcalAccts, setGcalAccts] = useState<DavAccount[]>([]);
  const [calsByAcc, setCalsByAcc] = useState<Record<number, GcalCalendar[]>>({});
  const [bdayCal, setBdayCal] = useState("");   // "accId::calId" oder "" (aus)
  const [bdayNote, setBdayNote] = useState("");

  async function load(query = q) {
    try { setContacts(await api.get<Contact[]>(`/contacts?q=${encodeURIComponent(query)}`)); }
    catch (e) { setErr((e as Error).message); }
  }
  async function loadBdaySettings() {
    try {
      const accs = (await api.get<DavAccount[]>("/dav/accounts")).filter((a) => a.kind === "gcal");
      setGcalAccts(accs);
      const map: Record<number, GcalCalendar[]> = {};
      for (const a of accs) { try { map[a.id] = await api.get<GcalCalendar[]>(`/dav/accounts/${a.id}/calendars`); } catch { /* egal */ } }
      setCalsByAcc(map);
      const cur = await api.get<{ dav_account_id: number | null; gcal_calendar_id: string }>("/contacts/birthday-calendar");
      setBdayCal(cur.dav_account_id && cur.gcal_calendar_id ? `${cur.dav_account_id}::${cur.gcal_calendar_id}` : "");
    } catch { /* optional */ }
  }
  async function chooseBdayCal(val: string) {
    setBdayCal(val); setBdayNote("");
    const body = val.includes("::")
      ? { dav_account_id: Number(val.split("::")[0]), gcal_calendar_id: val.split("::")[1] }
      : { dav_account_id: null, gcal_calendar_id: "" };
    try {
      const r = await api.put<{ ok: boolean; created?: number; updated?: number; removed?: number }>("/contacts/birthday-calendar", body);
      setBdayNote(val ? `Übertragen: ${r.created ?? 0} neu, ${r.updated ?? 0} aktualisiert` : "Geburtstage-Kalender aus");
    } catch (e) { setBdayNote((e as Error).message); }
  }
  async function syncBdays() {
    setBdayNote("Übertrage …");
    try {
      const r = await api.post<{ created?: number; updated?: number; removed?: number }>("/contacts/birthdays/sync", {});
      setBdayNote(`Übertragen: ${r.created ?? 0} neu, ${r.updated ?? 0} aktualisiert, ${r.removed ?? 0} entfernt`);
    } catch (e) { setBdayNote((e as Error).message); }
  }
  useEffect(() => { load(""); loadBdaySettings(); }, []);

  function set<K extends keyof Form>(k: K, v: Form[K]) { setForm((f) => ({ ...f, [k]: v })); }
  function payload(f: Form) { return { ...f, birthday: f.birthday || null }; }

  function openContact(ct: Contact) { setSel(ct.id); setForm(formFrom(ct)); setErr(""); }
  function newContact() { setSel("new"); setForm({ ...EMPTY }); setErr(""); }

  async function save() {
    setErr("");
    if (!form.first_name && !form.last_name && !form.email) { setErr(t("contacts.needNameOrEmail")); return; }
    setBusy(true);
    try {
      if (sel === "new") {
        const ct = await api.post<Contact>("/contacts", payload(form));
        await load(); setSel(ct.id);
      } else if (typeof sel === "number") {
        await api.patch<Contact>(`/contacts/${sel}`, payload(form));
        await load();
      }
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (typeof sel !== "number") { setSel(null); return; }
    if (!(await confirmDialog(t("contacts.confirmDelete")))) return;
    try { await api.del(`/contacts/${sel}`); setSel(null); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  function cityLine(ct: Contact): string { return [ct.postal_code, ct.city].filter(Boolean).join(" "); }
  const current = typeof sel === "number" ? contacts.find((c) => c.id === sel) : null;

  return (
    <div className="md-page">
      {/* Linke Spalte: Kontaktliste */}
      <aside className="md-list">
        <div className="md-list-head">
          <button className="primary" style={{ flex: 1 }} onClick={newContact}>＋ {t("contacts.new")}</button>
        </div>
        {gcalAccts.length > 0 && (
          <div className="md-search" style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
            <label className="label">🎂 Geburtstage-Kalender</label>
            <select value={bdayCal} onChange={(e) => chooseBdayCal(e.target.value)} style={{ width: "100%" }}>
              <option value="">Aus (nur in SelfMailer anzeigen)</option>
              {gcalAccts.map((a) => {
                const cals = (calsByAcc[a.id] || []).filter((c) => c.writable);
                if (cals.length === 0) return null;
                return (
                  <optgroup key={a.id} label={a.label || a.username}>
                    {cals.map((c) => <option key={c.id} value={`${a.id}::${c.id}`}>{c.name}{c.primary ? " ★" : ""}</option>)}
                  </optgroup>
                );
              })}
            </select>
            <div className="row" style={{ gap: 6 }}>
              {bdayCal && <button className="ghost" style={{ flex: 1 }} onClick={syncBdays}>Jetzt übertragen</button>}
            </div>
            {bdayNote && <div className="muted" style={{ fontSize: "0.78rem" }}>{bdayNote}</div>}
          </div>
        )}
        <div className="md-search">
          <span aria-hidden>🔍</span>
          <input value={q} onChange={(e) => { setQ(e.target.value); load(e.target.value); }} placeholder={t("contacts.search")} />
        </div>
        <div className="md-scroll">
          {contacts.map((ct) => (
            <button key={ct.id} className={`md-item ct-item ${sel === ct.id ? "active" : ""}`} onClick={() => openContact(ct)}>
              <span className="ct-avatar">{initials(ct)}</span>
              <div className="md-item-main">
                <div className="md-item-title">{displayName(ct, t("contacts.noName"))}</div>
                <div className="md-item-snippet">{[ct.organization, ct.email].filter(Boolean).join(" · ")}</div>
              </div>
            </button>
          ))}
          {contacts.length === 0 && <p className="muted" style={{ padding: "0.6rem" }}>{t("contacts.empty")}</p>}
        </div>
      </aside>

      {/* Rechte Spalte: Detail / Editor */}
      <div className="md-detail">
        {sel === null ? (
          <div className="md-placeholder">{t("contacts.selectHint")}</div>
        ) : (
          <div className="stack" style={{ gap: "0.7rem" }}>
            <div className="row" style={{ alignItems: "center", gap: "0.6rem" }}>
              {current && <span className="ct-avatar ct-avatar-lg">{initials(current)}</span>}
              <h2 style={{ margin: 0, fontSize: "1.15rem" }}>
                {sel === "new" ? t("contacts.new") : (current ? displayName(current, t("contacts.noName")) : "")}
              </h2>
              <span className="grow" />
              {typeof sel === "number" && <button className="ghost" onClick={remove} title={t("common.delete")}>🗑</button>}
            </div>

            {/* Kompakte Detailanzeige (nur bestehende Kontakte) */}
            {current && (
              <div className="ct-detail-info">
                {current.email && <div>✉ {current.email}</div>}
                {current.mobile && <div>📱 {current.mobile}</div>}
                {current.phone && <div>☎ {current.phone}</div>}
                {current.work_phone && <div>🏢 {current.work_phone}</div>}
                {current.website && <div>🌐 {current.website}</div>}
                {(current.street || cityLine(current) || current.country) && (
                  <div>📍 {[current.street, cityLine(current), current.country].filter(Boolean).join(", ")}</div>
                )}
                {current.birthday && <div>🎂 {fmtBirthday(current.birthday, lang)}</div>}
              </div>
            )}

            <details className="ct-edit" open={sel === "new"}>
              <summary>{sel === "new" ? t("contacts.new") : t("contacts.edit")}</summary>
              <div className="stack" style={{ gap: "0.6rem", marginTop: "0.7rem" }}>
                <ContactFields form={form} set={set} t={t} />
              </div>
            </details>

            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button className="ghost" onClick={() => setSel(null)}>{t("common.cancel")}</button>
              <button className="primary" onClick={save} disabled={busy}>{busy ? "…" : t("contacts.save")}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

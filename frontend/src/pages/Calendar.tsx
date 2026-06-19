import { useEffect, useMemo, useState } from "react";
import { api, type CalEvent, type Contact } from "../lib/api";
import { useLang, dateLocale, type Lang, type TFunc } from "../lib/i18n";

const EMPTY = { title: "", location: "", description: "", start: "", end: "", all_day: false };
type Form = typeof EMPTY;

function fmt(iso: string, lang: Lang): string {
  return new Date(iso).toLocaleString(dateLocale(lang), { dateStyle: "medium", timeStyle: "short" });
}
function dayKey(iso: string, lang: Lang): string {
  return new Date(iso).toLocaleDateString(dateLocale(lang), { weekday: "long", day: "2-digit", month: "long", year: "numeric" });
}
// Lokaler YYYY-MM-DD-Schlüssel (ohne UTC-Verschiebung).
function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
// Wert für <input type="datetime-local"> aus einem Date.
function localInput(d: Date): string {
  return `${ymd(d)}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// Eine Geburtstags-„Pseudo-Termin"-Zeile (nicht in der DB, jährlich abgeleitet).
type Birthday = { day: string; name: string; age: number | null };

function birthdaysForYear(contacts: Contact[], year: number): Birthday[] {
  const out: Birthday[] = [];
  for (const c of contacts) {
    if (!c.birthday) continue;
    const parts = c.birthday.split("-");
    if (parts.length < 3) continue;
    const by = Number(parts[0]), bm = Number(parts[1]), bd = Number(parts[2]);
    if (!bm || !bd) continue;
    const name = [c.first_name, c.last_name].filter(Boolean).join(" ") || c.organization || c.email;
    out.push({
      day: `${year}-${String(bm).padStart(2, "0")}-${String(bd).padStart(2, "0")}`,
      name,
      age: by > 1900 ? year - by : null,
    });
  }
  return out;
}

export function Calendar() {
  const { t, lang } = useLang();
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [mode, setMode] = useState<"month" | "agenda">("month");
  const now = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState({ year: now.getFullYear(), month: now.getMonth() });
  const [form, setForm] = useState<Form>({ ...EMPTY });
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState<CalEvent | null>(null);
  const [err, setErr] = useState("");

  async function load() {
    try {
      const [evs, cts] = await Promise.all([
        api.get<CalEvent[]>("/calendar/events"),
        api.get<Contact[]>("/contacts"),
      ]);
      setEvents(evs);
      setContacts(cts);
    } catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  function set<K extends keyof Form>(k: K, v: Form[K]) { setForm((f) => ({ ...f, [k]: v })); }

  function openCreate(day?: Date) {
    const base = day ?? new Date();
    const start = new Date(base); start.setHours(9, 0, 0, 0);
    const end = new Date(base); end.setHours(10, 0, 0, 0);
    setForm({ ...EMPTY, start: localInput(start), end: localInput(end) });
    setErr("");
    setCreating(true);
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!form.title || !form.start || !form.end) { setErr(t("cal.needFields")); return; }
    try {
      await api.post<CalEvent>("/calendar/events", form);
      setCreating(false);
      setForm({ ...EMPTY });
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function remove(ev: CalEvent) {
    try { await api.del(`/calendar/events/${ev.id}`); setDetail(null); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  function shift(delta: number) {
    setCursor((c) => {
      const m = c.month + delta;
      return { year: c.year + Math.floor(m / 12), month: ((m % 12) + 12) % 12 };
    });
  }
  function goToday() { setCursor({ year: now.getFullYear(), month: now.getMonth() }); }

  // Termine + Geburtstage nach lokalem Tag indexieren.
  const eventsByDay = useMemo(() => {
    const m: Record<string, CalEvent[]> = {};
    for (const ev of events) (m[ymd(new Date(ev.start))] ??= []).push(ev);
    return m;
  }, [events]);
  const birthdaysByDay = useMemo(() => {
    const m: Record<string, Birthday[]> = {};
    for (const b of birthdaysForYear(contacts, cursor.year)) (m[b.day] ??= []).push(b);
    return m;
  }, [contacts, cursor.year]);

  const monthTitle = new Date(cursor.year, cursor.month, 1)
    .toLocaleDateString(dateLocale(lang), { month: "long", year: "numeric" });

  // Wochentags-Kürzel ab Montag in der jeweiligen Sprache (1. Jan 2024 = Montag).
  const weekdays = useMemo(() => {
    return Array.from({ length: 7 }, (_, i) =>
      new Date(2024, 0, 1 + i).toLocaleDateString(dateLocale(lang), { weekday: "short" }),
    );
  }, [lang]);

  // 6 Wochen × 7 Tage, beginnend am Montag der Woche des Monatsersten.
  const cells = useMemo(() => {
    const first = new Date(cursor.year, cursor.month, 1);
    const offset = (first.getDay() + 6) % 7; // Mo=0
    const start = new Date(first); start.setDate(1 - offset);
    return Array.from({ length: 42 }, (_, i) => {
      const d = new Date(start); d.setDate(start.getDate() + i);
      return d;
    });
  }, [cursor]);

  const todayKey = ymd(now);

  return (
    <div className="cal-wrap">
      <div className="cal-toolbar">
        <div className="cal-nav">
          <button className="ghost" onClick={() => shift(-1)} title="←">‹</button>
          <div className="cal-month-title">{monthTitle}</div>
          <button className="ghost" onClick={() => shift(1)} title="→">›</button>
          <button className="ghost" onClick={goToday}>{t("cal.today")}</button>
        </div>
        <div className="row" style={{ gap: "0.4rem" }}>
          <div className="cal-switch">
            <button className={mode === "month" ? "on" : ""} onClick={() => setMode("month")}>{t("cal.month")}</button>
            <button className={mode === "agenda" ? "on" : ""} onClick={() => setMode("agenda")}>{t("cal.agenda")}</button>
          </div>
          <button className="primary" onClick={() => openCreate()}>＋ {t("cal.newEvent")}</button>
        </div>
      </div>

      {err && !creating && <div className="err">{err}</div>}

      {mode === "month" && (
        <div className="cal-grid">
          {weekdays.map((w) => <div key={w} className="cal-wd">{w}</div>)}
          {cells.map((d) => {
            const key = ymd(d);
            const evs = eventsByDay[key] ?? [];
            const bds = birthdaysByDay[key] ?? [];
            const outside = d.getMonth() !== cursor.month;
            return (
              <div
                key={key}
                className={`cal-cell ${outside ? "outside" : ""} ${key === todayKey ? "today" : ""}`}
                onClick={() => openCreate(d)}
              >
                <div className="cal-daynum">{d.getDate()}</div>
                <div className="cal-chips">
                  {bds.map((b, i) => (
                    <div key={`b${i}`} className="cal-chip birthday" title={`${t("cal.birthday")}: ${b.name}`}>
                      🎂 {b.name}{b.age != null ? ` (${b.age})` : ""}
                    </div>
                  ))}
                  {evs.map((ev) => (
                    <div
                      key={ev.id}
                      className="cal-chip"
                      title={ev.title}
                      onClick={(e) => { e.stopPropagation(); setDetail(ev); }}
                    >
                      {ev.title}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {mode === "agenda" && <AgendaList events={events} lang={lang} t={t} onOpen={setDetail} />}

      {creating && (
        <div className="modal-backdrop" onClick={() => setCreating(false)}>
          <form className="modal card stack" onClick={(e) => e.stopPropagation()} onSubmit={add}>
            <div className="topbar">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{t("cal.new")}</h2>
              <button type="button" className="ghost" onClick={() => setCreating(false)}>✕</button>
            </div>
            <input placeholder={t("cal.title")} value={form.title} onChange={(e) => set("title", e.target.value)} autoFocus required />
            <input placeholder={t("cal.location")} value={form.location} onChange={(e) => set("location", e.target.value)} />
            <div className="row">
              <label className="label" style={{ minWidth: 56 }}>{t("cal.start")}</label>
              <input type="datetime-local" value={form.start} onChange={(e) => set("start", e.target.value)} required />
            </div>
            <div className="row">
              <label className="label" style={{ minWidth: 56 }}>{t("cal.end")}</label>
              <input type="datetime-local" value={form.end} onChange={(e) => set("end", e.target.value)} required />
            </div>
            <textarea placeholder={t("cal.description")} value={form.description} onChange={(e) => set("description", e.target.value)} rows={2} />
            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button type="button" className="ghost" onClick={() => setCreating(false)}>{t("common.cancel")}</button>
              <button className="primary">{t("common.add")}</button>
            </div>
          </form>
        </div>
      )}

      {detail && (
        <div className="modal-backdrop" onClick={() => setDetail(null)}>
          <div className="modal card stack" onClick={(e) => e.stopPropagation()}>
            <div className="topbar">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{detail.title}</h2>
              <button className="ghost" onClick={() => setDetail(null)}>✕</button>
            </div>
            <div className="muted">{fmt(detail.start, lang)} – {fmt(detail.end, lang)}</div>
            {detail.location && <div>📍 {detail.location}</div>}
            {detail.description && <div style={{ whiteSpace: "pre-wrap" }}>{detail.description}</div>}
            <div className="row">
              <span className="grow" />
              <button className="ghost" onClick={() => remove(detail)}>{t("common.delete")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AgendaList({ events, lang, t, onOpen }: {
  events: CalEvent[]; lang: Lang; t: TFunc; onOpen: (e: CalEvent) => void;
}) {
  if (events.length === 0) return <p className="muted">{t("cal.empty")}</p>;
  const groups: Record<string, CalEvent[]> = {};
  for (const ev of events) (groups[dayKey(ev.start, lang)] ??= []).push(ev);
  return (
    <>
      {Object.entries(groups).map(([day, evs]) => (
        <div key={day} style={{ marginBottom: "1.2rem" }}>
          <div className="label" style={{ marginBottom: "0.5rem" }}>{day}</div>
          <div className="stack">
            {evs.map((ev) => (
              <div className="card row" style={{ padding: "0.7rem 1rem", cursor: "pointer" }} key={ev.id} onClick={() => onOpen(ev)}>
                <div className="grow">
                  <div style={{ fontWeight: 600 }}>{ev.title}</div>
                  <div className="mail-from">
                    {fmt(ev.start, lang)} – {fmt(ev.end, lang)}{ev.location ? ` · ${ev.location}` : ""}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

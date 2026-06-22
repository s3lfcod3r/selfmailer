import { useEffect, useMemo, useState } from "react";
import { api, type CalEvent, type Contact, type DavAccount, type GcalCalendar, type Task } from "../lib/api";
import { useLang, dateLocale, type Lang, type TFunc } from "../lib/i18n";

const EMPTY = { title: "", location: "", description: "", start: "", end: "", all_day: false, target: "local", calendarId: "" };
type Form = typeof EMPTY;

function fmt(iso: string, lang: Lang): string {
  return new Date(iso).toLocaleString(dateLocale(lang), { dateStyle: "medium", timeStyle: "short" });
}
function fmtTime(iso: string, lang: Lang): string {
  return new Date(iso).toLocaleTimeString(dateLocale(lang), { hour: "2-digit", minute: "2-digit" });
}
function dayKey(iso: string, lang: Lang): string {
  return new Date(iso).toLocaleDateString(dateLocale(lang), { weekday: "long", day: "2-digit", month: "long", year: "numeric" });
}
function ymd(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function localInput(d: Date): string {
  return `${ymd(d)}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
// ISO-8601-Kalenderwoche (Mo-basiert, KW1 enthaelt den 4. Januar).
function isoWeek(d: Date): number {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7) + 3);
  const firstThu = new Date(Date.UTC(t.getUTCFullYear(), 0, 4));
  firstThu.setUTCDate(firstThu.getUTCDate() - ((firstThu.getUTCDay() + 6) % 7) + 3);
  return 1 + Math.round((t.getTime() - firstThu.getTime()) / 6048e5);
}

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
    out.push({ day: `${year}-${String(bm).padStart(2, "0")}-${String(bd).padStart(2, "0")}`, name, age: by > 1900 ? year - by : null });
  }
  return out;
}

export function Calendar() {
  const { t, lang } = useLang();
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [gcalAccounts, setGcalAccounts] = useState<DavAccount[]>([]);
  const [calsByAcc, setCalsByAcc] = useState<Record<number, GcalCalendar[]>>({});
  // Geburtstage als echte Termine im Kalender? Dann virtuelle Anzeige weglassen.
  const [bdayActive, setBdayActive] = useState(false);
  const [mode, setMode] = useState<"month" | "agenda" | "tasks">("month");
  const now = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState({ year: now.getFullYear(), month: now.getMonth() });
  const [form, setForm] = useState<Form>({ ...EMPTY });
  const [editId, setEditId] = useState<number | null>(null);   // null = Anlegen, sonst Bearbeiten
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<CalEvent | null>(null);
  const [newTask, setNewTask] = useState("");
  const [newTaskDue, setNewTaskDue] = useState("");
  const [err, setErr] = useState("");
  // Ausgeblendete Kalender (Verwaltung in „Sync & Export", geteilter localStorage).
  // Nur lesend — beim Seitenwechsel neu eingelesen.
  const [hiddenCals] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("selfmailer.hiddenCals") || "[]")); } catch { return new Set(); }
  });
  // Quell-Kalender eines Termins (Schluessel/Name/Farbe) — mit Fallbacks.
  const keyOf = (ev: CalEvent) => ev.source_key || (ev.dav_account_id ? `dav:${ev.dav_account_id}` : "local");
  const nameOf = (ev: CalEvent) => ev.source_name || (keyOf(ev) === "local" ? "Lokal" : keyOf(ev));
  const sources = useMemo(() => {
    const m = new Map<string, { key: string; name: string; color: string }>();
    // 1) ALLE Google-Kalender (auch ohne Termine) aus der Kalenderliste
    for (const cals of Object.values(calsByAcc)) {
      for (const c of cals) if (!m.has(c.id)) m.set(c.id, { key: c.id, name: c.name, color: c.color || "" });
    }
    // 2) lokale/CalDAV-Quellen aus den Events ergänzen
    for (const ev of events) {
      const k = keyOf(ev);
      if (!m.has(k)) m.set(k, { key: k, name: nameOf(ev), color: ev.source_color || "" });
    }
    return [...m.values()].sort((a, z) => a.name.localeCompare(z.name));
  }, [calsByAcc, events]);
  const shownEvents = useMemo(() => events.filter((e) => !hiddenCals.has(keyOf(e))), [events, hiddenCals]);

  async function load() {
    try {
      const [evs, cts, tks, dav] = await Promise.all([
        api.get<CalEvent[]>("/calendar/events"),
        api.get<Contact[]>("/contacts"),
        api.get<Task[]>("/tasks"),
        api.get<DavAccount[]>("/dav/accounts"),
      ]);
      setEvents(evs); setContacts(cts); setTasks(tks);
      const gcals = dav.filter((d) => d.kind === "gcal");
      setGcalAccounts(gcals);
      // ALLE Kalender je Google-Konto laden (für Filter-Panel + Ziel-Auswahl),
      // auch leere — sonst tauchen Kalender ohne Termine nirgends auf.
      const entries = await Promise.all(gcals.map(async (a) => {
        try { return [a.id, await api.get<GcalCalendar[]>(`/dav/accounts/${a.id}/calendars`)] as const; }
        catch { return [a.id, [] as GcalCalendar[]] as const; }
      }));
      setCalsByAcc(Object.fromEntries(entries));
      // Ist ein Geburtstage-Kalender gesetzt? Dann sind die Geburtstage ECHTE
      // Termine → die virtuelle Kontakt-Anzeige NICHT zusätzlich zeichnen (sonst doppelt).
      try {
        const bc = await api.get<{ dav_account_id: number | null; gcal_calendar_id: string }>("/contacts/birthday-calendar");
        setBdayActive(!!(bc.dav_account_id && bc.gcal_calendar_id));
      } catch { /* egal */ }
    } catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  function set<K extends keyof Form>(k: K, v: Form[K]) { setForm((f) => ({ ...f, [k]: v })); }

  // Standard-Ziel: zuerst der selbst gewählte Standardkalender (Sync & Export),
  // sonst der Google-Hauptkalender, sonst lokal.
  function defaultTarget(): { target: string; calendarId: string } {
    const saved = localStorage.getItem("selfmailer.defaultCal") || "";
    if (saved === "local") return { target: "local", calendarId: "" };
    if (saved.includes("::")) {
      const [accId, calId] = saved.split("::");
      const cals = calsByAcc[Number(accId)] || [];
      if (cals.some((c) => c.id === calId && c.writable)) return { target: accId, calendarId: calId };
    }
    for (const a of gcalAccounts) {
      const cals = (calsByAcc[a.id] || []).filter((c) => c.writable);
      if (cals.length) {
        const primary = cals.find((c) => c.primary) ?? cals[0];
        return { target: String(a.id), calendarId: primary.id };
      }
    }
    return { target: "local", calendarId: "" };
  }

  function openCreate(day?: Date) {
    const base = day ?? new Date();
    const start = new Date(base); start.setHours(9, 0, 0, 0);
    const end = new Date(base); end.setHours(10, 0, 0, 0);
    setForm({ ...EMPTY, ...defaultTarget(), start: localInput(start), end: localInput(end) });
    setEditId(null); setErr(""); setCreating(true);
  }

  function openEdit(ev: CalEvent) {
    setForm({
      title: ev.title, location: ev.location, description: ev.description,
      start: localInput(new Date(ev.start)), end: localInput(new Date(ev.end)),
      all_day: ev.all_day,
      target: ev.dav_account_id ? String(ev.dav_account_id) : "local",
      calendarId: "",
    });
    setEditId(ev.id); setDetail(null); setErr(""); setCreating(true);
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!form.title || !form.start || !form.end) { setErr(t("cal.needFields")); return; }
    // datetime-local ist Lokalzeit → als UTC-ISO (Z) senden; Store haelt UTC.
    const payload: Record<string, unknown> = {
      title: form.title, location: form.location, description: form.description,
      start: new Date(form.start).toISOString(), end: new Date(form.end).toISOString(),
      all_day: form.all_day,
    };
    setBusy(true);
    try {
      if (editId != null) {
        await api.patch<CalEvent>(`/calendar/events/${editId}`, payload);
      } else {
        if (form.target !== "local") {
          payload.dav_account_id = Number(form.target);
          payload.gcal_calendar_id = form.calendarId;
        }
        await api.post<CalEvent>("/calendar/events", payload);
      }
      setCreating(false); setEditId(null); setForm({ ...EMPTY }); load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function remove(ev: CalEvent) {
    setBusy(true);
    try { await api.del(`/calendar/events/${ev.id}`); setDetail(null); load(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function addTask(e: React.FormEvent) {
    e.preventDefault();
    if (!newTask.trim()) return;
    try { await api.post<Task>("/tasks", { title: newTask.trim(), due: newTaskDue || null }); setNewTask(""); setNewTaskDue(""); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function toggleTask(tk: Task) {
    try { await api.patch<Task>(`/tasks/${tk.id}`, { done: !tk.done }); load(); }
    catch (e) { setErr((e as Error).message); }
  }
  async function removeTask(tk: Task) {
    try { await api.del(`/tasks/${tk.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  function shift(delta: number) {
    setCursor((c) => { const m = c.month + delta; return { year: c.year + Math.floor(m / 12), month: ((m % 12) + 12) % 12 }; });
  }
  function goToday() { setCursor({ year: now.getFullYear(), month: now.getMonth() }); }

  const eventsByDay = useMemo(() => {
    const m: Record<string, CalEvent[]> = {};
    for (const ev of shownEvents) (m[ymd(new Date(ev.start))] ??= []).push(ev);
    return m;
  }, [shownEvents]);
  const birthdaysByDay = useMemo(() => {
    const m: Record<string, Birthday[]> = {};
    if (bdayActive) return m;   // Geburtstage sind echte Termine → nicht doppelt anzeigen
    for (const b of birthdaysForYear(contacts, cursor.year)) (m[b.day] ??= []).push(b);
    return m;
  }, [contacts, cursor.year, bdayActive]);

  // „Diesen Monat": Termine + Geburtstage des sichtbaren Monats, nach Tag sortiert.
  const monthAgenda = useMemo(() => {
    const items: { day: number; label: string; time?: string; ev?: CalEvent; birthday?: boolean }[] = [];
    for (const ev of shownEvents) {
      const d = new Date(ev.start);
      if (d.getFullYear() === cursor.year && d.getMonth() === cursor.month)
        items.push({ day: d.getDate(), label: ev.title, time: fmtTime(ev.start, lang), ev });
    }
    if (!bdayActive) for (const b of birthdaysForYear(contacts, cursor.year)) {
      const parts = b.day.split("-");
      if (Number(parts[1]) - 1 === cursor.month)
        items.push({ day: Number(parts[2]), label: `🎂 ${b.name}${b.age != null ? ` (${b.age})` : ""}`, birthday: true });
    }
    // Im aktuellen Monat nur ab HEUTE zeigen (Vergangenes ausblenden → kurze
    // Liste, heutiger Tag oben, kein Scrollbalken nötig).
    const isCurrentMonth = cursor.year === now.getFullYear() && cursor.month === now.getMonth();
    const fromDay = isCurrentMonth ? now.getDate() : 0;
    return items.filter((it) => it.day >= fromDay).sort((a, z) => a.day - z.day);
  }, [shownEvents, contacts, cursor, lang, now, bdayActive]);

  const openTasks = tasks.filter((tk) => !tk.done);
  const doneTasks = tasks.filter((tk) => tk.done);

  const monthTitle = new Date(cursor.year, cursor.month, 1).toLocaleDateString(dateLocale(lang), { month: "long", year: "numeric" });
  const weekdays = useMemo(() =>
    Array.from({ length: 7 }, (_, i) => new Date(2024, 0, 1 + i).toLocaleDateString(dateLocale(lang), { weekday: "short" })), [lang]);
  const cells = useMemo(() => {
    const first = new Date(cursor.year, cursor.month, 1);
    const offset = (first.getDay() + 6) % 7;
    const start = new Date(first); start.setDate(1 - offset);
    return Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
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
            <button className={mode === "tasks" ? "on" : ""} onClick={() => setMode("tasks")}>✓ {t("cal.tasks")}</button>
          </div>
          {mode !== "tasks" && <button className="primary" onClick={() => openCreate()}>＋ {t("cal.newEvent")}</button>}
        </div>
      </div>

      {err && !creating && <div className="err">{err}</div>}

      {mode === "tasks" ? (
        <div className="cal-body" style={{ gridTemplateColumns: "1fr" }}>
          <section className="cal-panel cal-main" style={{ overflow: "auto" }}>
            <div className="cal-panel-head">✓ {t("cal.tasks")}</div>
            <form className="cal-task-add" onSubmit={addTask}>
              <input placeholder={t("cal.taskPlaceholder")} value={newTask} onChange={(e) => setNewTask(e.target.value)} />
              <div className="row" style={{ gap: "0.3rem" }}>
                <input type="date" value={newTaskDue} onChange={(e) => setNewTaskDue(e.target.value)} style={{ flex: 1 }} />
                <button className="primary" style={{ padding: "0 0.7rem" }}>＋</button>
              </div>
            </form>
            {openTasks.length === 0 && doneTasks.length === 0 && <div className="muted" style={{ fontSize: "0.82rem" }}>{t("cal.noTasks")}</div>}
            {openTasks.map((tk) => <TaskRow key={tk.id} tk={tk} lang={lang} onToggle={toggleTask} onRemove={removeTask} />)}
            {doneTasks.length > 0 && <div className="cal-done-head">{t("cal.doneTasks")} ({doneTasks.length})</div>}
            {doneTasks.map((tk) => <TaskRow key={tk.id} tk={tk} lang={lang} onToggle={toggleTask} onRemove={removeTask} />)}
          </section>
        </div>
      ) : (
        <div className="cal-body">
          <div className="cal-main">
            {mode === "month" ? (
              <div className="cal-grid">
                {weekdays.map((w) => <div key={w} className="cal-wd">{w}</div>)}
                {cells.map((d) => {
                  const key = ymd(d);
                  const evs = eventsByDay[key] ?? [];
                  const bds = birthdaysByDay[key] ?? [];
                  const outside = d.getMonth() !== cursor.month;
                  return (
                    <div key={key} className={`cal-cell ${outside ? "outside" : ""} ${key === todayKey ? "today" : ""}`} onClick={() => openCreate(d)}>
                      <div className="cal-daynum">
                        {d.getDay() === 1 && <span className="cal-kw" title="Kalenderwoche">KW {isoWeek(d)}</span>}
                        {d.getDate()}
                      </div>
                      <div className="cal-chips">
                        {bds.map((b, i) => (
                          <div key={`b${i}`} className="cal-chip birthday" title={`${t("cal.birthday")}: ${b.name}`}>🎂 {b.name}{b.age != null ? ` (${b.age})` : ""}</div>
                        ))}
                        {evs.map((ev) => (
                          <div key={ev.id} className="cal-chip" title={`${nameOf(ev)}: ${ev.title}`}
                            style={ev.source_color ? { borderLeft: `3px solid ${ev.source_color}`, paddingLeft: "5px" } : undefined}
                            onClick={(e) => { e.stopPropagation(); setDetail(ev); }}>{ev.title}</div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <AgendaList events={shownEvents} lang={lang} t={t} onOpen={setDetail} />
            )}
          </div>

          <aside className="cal-aside">
            {(() => {
              // Kompakte LEGENDE der sichtbaren Kalender — Ein-/Ausblenden läuft
              // über „Sync & Export" (Klick hier blendet bewusst NICHT aus).
              const visible = sources.filter((s) => !hiddenCals.has(s.key));
              if (visible.length === 0) return null;
              return (
                <section className="cal-panel">
                  <div className="cal-panel-head">Kalender</div>
                  <div className="cal-legend">
                    {visible.map((s) => (
                      <span key={s.key} className="cal-legend-item">
                        <span className="cal-filter-dot" style={{ background: s.color || "var(--self-teal)" }} />
                        {s.name}
                      </span>
                    ))}
                  </div>
                  <div className="muted" style={{ fontSize: "0.72rem", marginTop: "0.45rem" }}>Ein-/ausblenden in „Sync & Export".</div>
                </section>
              );
            })()}
            <section className="cal-panel cal-aside-scroll">
              <div className="cal-panel-head">{t("cal.thisMonth")}</div>
              {monthAgenda.length === 0 && <div className="muted" style={{ fontSize: "0.82rem" }}>{t("cal.noneThisMonth")}</div>}
              {monthAgenda.map((it, i) => (
                <button key={i} className={`cal-agenda-item ${it.birthday ? "is-birthday" : ""}`} onClick={() => it.ev && setDetail(it.ev)} disabled={!it.ev}>
                  <span className="cal-agenda-day">{String(it.day).padStart(2, "0")}</span>
                  <span className="cal-agenda-label">{it.label}</span>
                  {it.time && <span className="cal-agenda-time">{it.time}</span>}
                </button>
              ))}
            </section>
          </aside>
        </div>
      )}

      {creating && (
        <div className="modal-backdrop" onClick={() => setCreating(false)}>
          <form className="modal card stack" onClick={(e) => e.stopPropagation()} onSubmit={add}>
            <div className="topbar">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{editId != null ? t("cal.edit") : t("cal.new")}</h2>
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
            <textarea placeholder={t("cal.description")} value={form.description} onChange={(e) => set("description", e.target.value)} rows={8} style={{ minHeight: "11rem" }} />

            {/* Ziel-Kalender direkt waehlbar (Lokal + alle beschreibbaren Google-Kalender). */}
            {editId == null && gcalAccounts.length > 0 && (
              <div className="row">
                <label className="label" style={{ minWidth: 56 }}>{t("cal.saveIn")}</label>
                <select
                  value={form.target === "local" ? "local" : `${form.target}::${form.calendarId}`}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === "local") { setForm((f) => ({ ...f, target: "local", calendarId: "" })); return; }
                    const [accId, calId] = v.split("::");
                    setForm((f) => ({ ...f, target: accId, calendarId: calId }));
                  }}
                  style={{ flex: 1 }}
                >
                  <option value="local">{t("cal.localOnly")}</option>
                  {gcalAccounts.map((a) => {
                    const cals = (calsByAcc[a.id] || []).filter((c) => c.writable);
                    if (cals.length === 0) return null;
                    return (
                      <optgroup key={a.id} label={a.label || a.username}>
                        {cals.map((c) => <option key={c.id} value={`${a.id}::${c.id}`}>{c.name}{c.primary ? " ★" : ""}</option>)}
                      </optgroup>
                    );
                  })}
                </select>
              </div>
            )}

            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button type="button" className="ghost" onClick={() => setCreating(false)}>{t("common.cancel")}</button>
              <button className="primary" disabled={busy}>{editId != null ? t("common.save") : t("common.add")}</button>
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
            {detail.dav_account_id && <div className="muted" style={{ fontSize: "0.82rem" }}>🔄 {t("cal.syncedHint")}</div>}
            <div className="row">
              <span className="grow" />
              <button className="ghost" onClick={() => openEdit(detail)}>{t("common.edit")}</button>
              <button className="ghost" disabled={busy} onClick={() => remove(detail)}>{t("common.delete")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TaskRow({ tk, lang, onToggle, onRemove }: { tk: Task; lang: Lang; onToggle: (t: Task) => void; onRemove: (t: Task) => void }) {
  const overdue = tk.due && !tk.done && tk.due < new Date().toISOString().slice(0, 10);
  return (
    <div className={`cal-task ${tk.done ? "done" : ""}`}>
      <button className="cal-task-check" onClick={() => onToggle(tk)} title={tk.done ? "↺" : "✓"}>{tk.done ? "☑" : "☐"}</button>
      <div className="cal-task-body">
        <div className="cal-task-title">{tk.title}</div>
        {tk.due && <div className={`cal-task-due ${overdue ? "overdue" : ""}`}>{new Date(tk.due + "T00:00:00").toLocaleDateString(dateLocale(lang), { day: "2-digit", month: "short" })}</div>}
      </div>
      <button className="ghost cal-task-del" onClick={() => onRemove(tk)}>✕</button>
    </div>
  );
}

function AgendaList({ events, lang, t, onOpen }: { events: CalEvent[]; lang: Lang; t: TFunc; onOpen: (e: CalEvent) => void }) {
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
                  <div style={{ fontWeight: 600 }}>{ev.dav_account_id ? "🔄 " : ""}{ev.title}</div>
                  <div className="mail-from">{fmt(ev.start, lang)} – {fmt(ev.end, lang)}{ev.location ? ` · ${ev.location}` : ""}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

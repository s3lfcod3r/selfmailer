import { useEffect, useState } from "react";
import { api, type CalEvent } from "../lib/api";

const EMPTY = { title: "", location: "", start: "", end: "" };

function fmt(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}
function dayKey(iso: string): string {
  return new Date(iso).toLocaleDateString("de-DE", { weekday: "long", day: "2-digit", month: "long", year: "numeric" });
}

export function Calendar() {
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [err, setErr] = useState("");

  async function load() {
    try { setEvents(await api.get<CalEvent[]>("/calendar/events")); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    if (!form.title || !form.start || !form.end) { setErr("Titel, Beginn und Ende sind nötig."); return; }
    try {
      await api.post<CalEvent>("/calendar/events", form);
      setForm({ ...EMPTY });
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function remove(ev: CalEvent) {
    try { await api.del(`/calendar/events/${ev.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  // nach Tag gruppieren
  const groups: Record<string, CalEvent[]> = {};
  for (const ev of events) (groups[dayKey(ev.start)] ??= []).push(ev);

  return (
    <div>
      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={add}>
        <div className="label">Neuer Termin</div>
        <div className="row">
          <input placeholder="Titel" value={form.title} onChange={(e) => set("title", e.target.value)} required />
          <input placeholder="Ort" value={form.location} onChange={(e) => set("location", e.target.value)} />
        </div>
        <div className="row">
          <label className="label" style={{ minWidth: 56 }}>Beginn</label>
          <input type="datetime-local" value={form.start} onChange={(e) => set("start", e.target.value)} required />
          <label className="label" style={{ minWidth: 40 }}>Ende</label>
          <input type="datetime-local" value={form.end} onChange={(e) => set("end", e.target.value)} required />
          <button className="primary">Hinzufügen</button>
        </div>
      </form>

      {err && <div className="err">{err}</div>}
      {events.length === 0 && <p className="muted">Noch keine Termine.</p>}

      {Object.entries(groups).map(([day, evs]) => (
        <div key={day} style={{ marginBottom: "1.2rem" }}>
          <div className="label" style={{ marginBottom: "0.5rem" }}>{day}</div>
          <div className="stack">
            {evs.map((ev) => (
              <div className="card row" style={{ padding: "0.7rem 1rem" }} key={ev.id}>
                <div className="grow">
                  <div style={{ fontWeight: 600 }}>{ev.title}</div>
                  <div className="mail-from">
                    {fmt(ev.start)} – {fmt(ev.end)}{ev.location ? ` · ${ev.location}` : ""}
                  </div>
                </div>
                <button className="ghost" onClick={() => remove(ev)}>Löschen</button>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

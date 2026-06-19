import { useEffect, useMemo, useState } from "react";
import { api, type Note } from "../lib/api";
import { useLang, dateLocale } from "../lib/i18n";

type Draft = { title: string; body: string };

export function Notes() {
  const { t, lang } = useLang();
  const [notes, setNotes] = useState<Note[]>([]);
  const [sel, setSel] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>({ title: "", body: "" });
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try { setNotes(await api.get<Note[]>("/notes")); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  // Angeheftete zuerst, dann nach letzter Aenderung.
  const sorted = useMemo(() => {
    const f = q.trim().toLowerCase();
    return [...notes]
      .filter((n) => !f || `${n.title} ${n.body}`.toLowerCase().includes(f))
      .sort((a, b) => (Number(b.pinned) - Number(a.pinned)) || (b.updated_at > a.updated_at ? 1 : -1));
  }, [notes, q]);

  function openNote(n: Note) { setSel(n.id); setDraft({ title: n.title, body: n.body }); setErr(""); }
  function newNote() { setSel("new"); setDraft({ title: "", body: "" }); setErr(""); }

  async function save() {
    if (!draft.title.trim() && !draft.body.trim()) return;
    setBusy(true); setErr("");
    try {
      if (sel === "new") {
        const n = await api.post<Note>("/notes", draft);
        await load(); setSel(n.id);
      } else if (typeof sel === "number") {
        await api.patch<Note>(`/notes/${sel}`, draft);
        await load();
      }
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }
  async function togglePin(n: Note, e: React.MouseEvent) {
    e.stopPropagation();
    await api.patch<Note>(`/notes/${n.id}`, { pinned: !n.pinned }); load();
  }
  async function remove() {
    if (typeof sel !== "number") { setSel(null); return; }
    if (!confirm(t("notes.confirmDelete"))) return;
    await api.del(`/notes/${sel}`); setSel(null); load();
  }

  function dateParts(iso: string) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return { day: "–", mon: "", time: "" };
    const loc = dateLocale(lang);
    return {
      day: d.toLocaleDateString(loc, { day: "2-digit" }),
      mon: d.toLocaleDateString(loc, { month: "short" }),
      time: d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" }),
    };
  }

  return (
    <div className="md-page">
      {/* Linke Spalte: Liste */}
      <aside className="md-list">
        <div className="md-list-head">
          <button className="primary" style={{ flex: 1 }} onClick={newNote}>＋ {t("notes.new")}</button>
        </div>
        <div className="md-search">
          <span aria-hidden>🔍</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("contacts.search")} />
        </div>
        <div className="md-scroll">
          {sorted.map((n) => {
            const dp = dateParts(n.updated_at);
            return (
              <button key={n.id} className={`md-item ${sel === n.id ? "active" : ""}`} onClick={() => openNote(n)}>
                <div className="md-date">
                  <span className="md-date-day">{dp.day}</span>
                  <span className="md-date-sub">{dp.mon}</span>
                  <span className="md-date-sub">{dp.time}</span>
                </div>
                <div className="md-item-main">
                  <div className="md-item-title">
                    {n.pinned && <span className="md-pin">★</span>}
                    {n.title || t("notes.untitled")}
                  </div>
                  <div className="md-item-snippet">{n.body || t("notes.empty")}</div>
                </div>
              </button>
            );
          })}
          {sorted.length === 0 && <p className="muted" style={{ padding: "0.6rem" }}>{t("notes.empty")}</p>}
        </div>
      </aside>

      {/* Rechte Spalte: Detail / Editor */}
      <div className="md-detail">
        {sel === null ? (
          <div className="md-placeholder">{t("notes.selectHint")}</div>
        ) : (
          <div className="stack" style={{ gap: "0.7rem", height: "100%" }}>
            <div className="row" style={{ alignItems: "center" }}>
              <input className="md-title-input" value={draft.title}
                onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
                placeholder={t("notes.title")} autoFocus />
              <span className="grow" />
              {typeof sel === "number" && (() => {
                const cur = notes.find((n) => n.id === sel);
                return cur ? <button className="ghost" onClick={(e) => togglePin(cur, e)} title={t("notes.pin")}>{cur.pinned ? "★" : "☆"}</button> : null;
              })()}
              {typeof sel === "number" && <button className="ghost" onClick={remove} title={t("common.delete")}>🗑</button>}
            </div>
            <textarea className="md-body-input" value={draft.body}
              onChange={(e) => setDraft((d) => ({ ...d, body: e.target.value }))}
              placeholder={t("notes.bodyPlaceholder")} />
            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button className="ghost" onClick={() => setSel(null)}>{t("common.cancel")}</button>
              <button className="primary" onClick={save} disabled={busy}>{busy ? "…" : t("notes.save")}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

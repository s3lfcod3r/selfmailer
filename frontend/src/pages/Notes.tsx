import { useEffect, useLayoutEffect, useRef, useState, type MutableRefObject } from "react";
import { api, type Note } from "../lib/api";
import { useLang, dateLocale } from "../lib/i18n";
import { confirmDialog } from "../lib/dialog";
import { useLeaveGuard, type LeaveGuard } from "../lib/leaveGuard";

type Draft = { title: string; body: string };
type Summary = Omit<Note, "body">;
const EMPTY: Draft = { title: "", body: "" };

export function Notes({ leaveGuard }: { leaveGuard?: MutableRefObject<LeaveGuard | null> }) {
  const { t, lang } = useLang();
  const [notes, setNotes] = useState<Summary[]>([]);
  const [sel, setSel] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>({ title: "", body: "" });
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState<Draft>(EMPTY);
  const listRequest = useRef(0);
  const detailRequest = useRef(0);
  const writing = useRef(false);
  const mounted = useRef(false);
  const query = useRef(q);
  query.current = q;
  const dirty = sel !== null && (draft.title !== saved.title || draft.body !== saved.body);
  const canLeave = useLeaveGuard(dirty, busy, t("notes.unsaved"));
  useLayoutEffect(() => {
    if (leaveGuard) leaveGuard.current = canLeave;
    return () => { if (leaveGuard?.current === canLeave) leaveGuard.current = null; };
  }, [leaveGuard, canLeave]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; listRequest.current++; detailRequest.current++; };
  }, []);

  async function load() {
    const request = ++listRequest.current;
    const search = query.current;
    try {
      const result = await api.get<Summary[]>(`/notes/summaries?q=${encodeURIComponent(search)}`);
      if (mounted.current && request === listRequest.current && search === query.current) setNotes(result);
    } catch (e) {
      if (mounted.current && request === listRequest.current && search === query.current) setErr((e as Error).message);
    }
  }
  useEffect(() => {
    setNotes([]);
    const timer = window.setTimeout(() => { void load(); }, q ? 250 : 0);
    return () => { window.clearTimeout(timer); listRequest.current++; };
  }, [q]);

  async function openNote(n: Summary) {
    if (sel === n.id || writing.current || !(await canLeave()) || !mounted.current) return;
    const request = ++detailRequest.current;
    // Keep the current draft until the replacement was successfully fetched.
    setLoading(true); setErr("");
    try {
      const note = await api.get<Note>(`/notes/${n.id}`);
      if (!mounted.current || request !== detailRequest.current) return;
      setSel(note.id); setDraft({ title: note.title, body: note.body });
      setSaved({ title: note.title, body: note.body });
    } catch (e) {
      if (mounted.current && request === detailRequest.current) setErr((e as Error).message);
    } finally {
      if (mounted.current && request === detailRequest.current) setLoading(false);
    }
  }
  async function selectEmpty(next: "new" | null) {
    if (writing.current || !(await canLeave()) || !mounted.current) return;
    detailRequest.current++; setLoading(false);
    setSel(next); setDraft(EMPTY); setSaved(EMPTY); setErr("");
  }

  async function save() {
    if (writing.current || loading || sel === null || (!draft.title.trim() && !draft.body.trim())) return;
    writing.current = true;
    setBusy(true); setErr("");
    try {
      const n = sel === "new" ? await api.post<Note>("/notes", draft) : await api.patch<Note>(`/notes/${sel}`, draft);
      if (!mounted.current) return;
      setSel(n.id); setDraft({ title: n.title, body: n.body }); setSaved({ title: n.title, body: n.body });
      void load();
    } catch (e) { setErr((e as Error).message); }
    finally { writing.current = false; if (mounted.current) setBusy(false); }
  }
  async function togglePin(n: Summary) {
    if (writing.current || loading) return;
    writing.current = true; setBusy(true); setErr("");
    try { await api.patch<Note>(`/notes/${n.id}`, { pinned: !n.pinned }); void load(); }
    catch (e) { setErr((e as Error).message); }
    finally { writing.current = false; if (mounted.current) setBusy(false); }
  }
  async function remove() {
    if (typeof sel !== "number" || writing.current || loading) return;
    writing.current = true; setBusy(true); setErr("");
    try {
      if (!(await confirmDialog(t("notes.confirmDelete"))) || !mounted.current) return;
      await api.del(`/notes/${sel}`);
      if (!mounted.current) return;
      setSel(null); setDraft(EMPTY); setSaved(EMPTY); void load();
    } catch (e) { setErr((e as Error).message); }
    finally { writing.current = false; if (mounted.current) setBusy(false); }
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
          <button className="primary" style={{ flex: 1 }} disabled={busy} onClick={() => selectEmpty("new")}>＋ {t("notes.new")}</button>
        </div>
        <div className="md-search">
          <span aria-hidden>🔍</span>
          <input value={q} maxLength={200} aria-label={t("notes.search")} onChange={(e) => setQ(e.target.value)} placeholder={t("notes.search")} />
        </div>
        <div className="md-scroll">
          {notes.map((n) => {
            const dp = dateParts(n.updated_at);
            return (
              <button key={n.id} disabled={busy} className={`md-item ${sel === n.id ? "active" : ""}`} onClick={() => openNote(n)}>
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
                </div>
              </button>
            );
          })}
          {notes.length === 0 && <p className="muted" style={{ padding: "0.6rem" }}>{t(q ? "notes.noResults" : "notes.empty")}</p>}
        </div>
      </aside>

      {/* Rechte Spalte: Detail / Editor */}
      <div className="md-detail">
        {err && <div className="err" role="alert">{err}</div>}
        {loading && <p role="status">{t("common.loading")}</p>}
        {sel === null ? (
          <div className="md-placeholder empty-state">
            <span className="empty-ico" aria-hidden>📝</span>
            {t("notes.selectHint")}
          </div>
        ) : (
          <div className="stack" style={{ gap: "0.7rem", height: "100%" }}>
            <div className="row" style={{ alignItems: "center" }}>
              <input className="md-title-input" value={draft.title} disabled={busy || loading} aria-label={t("notes.title")}
                onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
                placeholder={t("notes.title")} autoFocus />
              <span className="grow" />
              {typeof sel === "number" && (() => {
                const cur = notes.find((n) => n.id === sel);
                return cur ? <button className="ghost" disabled={busy || loading} onClick={() => togglePin(cur)} title={t("notes.pin")} aria-label={t("notes.pin")}>{cur.pinned ? "★" : "☆"}</button> : null;
              })()}
              {typeof sel === "number" && <button className="ghost" disabled={busy || loading} onClick={remove} title={t("common.delete")} aria-label={t("common.delete")}>🗑</button>}
            </div>
            <textarea className="md-body-input" value={draft.body} disabled={busy || loading} aria-label={t("notes.bodyPlaceholder")}
              onChange={(e) => setDraft((d) => ({ ...d, body: e.target.value }))}
              placeholder={t("notes.bodyPlaceholder")} />
            <div className="row">
              <span className="grow" />
              <button className="ghost" disabled={busy} onClick={() => selectEmpty(null)}>{t("common.cancel")}</button>
              <button className="primary" onClick={save} disabled={busy || loading || !dirty}>{busy ? "…" : t("notes.save")}</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

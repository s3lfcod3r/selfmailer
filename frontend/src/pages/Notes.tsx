import { useEffect, useState } from "react";
import { api, type Note } from "../lib/api";

export function Notes() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    try { setNotes(await api.get<Note[]>("/notes")); }
    catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() && !body.trim()) return;
    await api.post<Note>("/notes", { title, body });
    setTitle(""); setBody("");
    load();
  }
  async function togglePin(n: Note) {
    await api.patch<Note>(`/notes/${n.id}`, { pinned: !n.pinned });
    load();
  }
  async function remove(n: Note) {
    await api.del(`/notes/${n.id}`);
    load();
  }

  return (
    <div>
      <form className="card stack" style={{ padding: "1rem", marginBottom: "1.4rem" }} onSubmit={add}>
        <input placeholder="Titel" value={title} onChange={(e) => setTitle(e.target.value)} />
        <textarea placeholder="Notiz schreiben…" rows={3} value={body} onChange={(e) => setBody(e.target.value)} />
        <div className="row">
          <span className="grow" />
          <button className="primary">Notiz speichern</button>
        </div>
      </form>

      {err && <div className="err">{err}</div>}
      {notes.length === 0 && <p className="muted">Noch keine Notizen.</p>}

      <div className="notes-grid">
        {notes.map((n) => (
          <div className="note card" key={n.id}>
            {n.title && <div className="note-title">{n.title}</div>}
            <div className="note-body">{n.body}</div>
            <div className="note-foot">
              <button className="ghost" onClick={() => togglePin(n)} title="Anheften">
                {n.pinned ? "★" : "☆"}
              </button>
              <button className="ghost" onClick={() => remove(n)} title="Löschen">🗑</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { api, type FolderCount, type MailLabel, type MsgHeader, type SearchResult } from "../lib/api";
import { Modal } from "./Modal";
import { parseAddr, listDate } from "../lib/mailview";
import { normalizeSubject } from "../lib/threads";

type Field = "from" | "domain" | "subject" | "text" | "label";
type SavedView = { name: string; field: Field; value: string; before: string; read: boolean; protect: boolean };
function readViews(key: string): SavedView[] {
  try {
    const rows: unknown = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(rows) ? rows.filter(v => v && typeof v.name === "string" && v.name.length <= 80 &&
      ["from", "domain", "subject", "text", "label"].includes(v.field) && typeof v.value === "string" && v.value.length <= 200 &&
      typeof v.before === "string" && v.before.length <= 10 && typeof v.read === "boolean" && typeof v.protect === "boolean").slice(0, 10) : [];
  } catch { return []; }
}
export function MailCleanup({ account, folder, folders, labels, source, de, onClose, onChanged, onRead }: {
  account: number; folder: string; folders: FolderCount[]; labels: MailLabel[]; source?: MsgHeader;
  de: boolean; onClose: () => void; onChanged: () => void; onRead: (m: MsgHeader) => void;
}) {
  const L = (a: string, b: string) => de ? a : b;
  const [field, setField] = useState<Field>("from");
  const [value, setValue] = useState(source ? parseAddr(source.from).email : "");
  const [before, setBefore] = useState("");
  const [read, setRead] = useState(false);
  const [protect, setProtect] = useState(true);
  const [dest, setDest] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [rememberRule, setRememberRule] = useState(false);
  const viewKey = `selfmailer.savedCleanup:${account}:${folder}`;
  const [views, setViews] = useState(() => readViews(viewKey));
  const [viewName, setViewName] = useState("");
  function saveView() {
    const name = viewName.trim(); if (!name || !value.trim()) return;
    const next = [{ name, field, value, before, read, protect }, ...views.filter(v => v.name !== name)].slice(0, 10);
    try { localStorage.setItem(viewKey, JSON.stringify(next)); setViews(next); setViewName(""); }
    catch { setError(L("Ansicht konnte nicht gespeichert werden.", "Could not save view.")); }
  }
  const seq = useRef(0);
  useEffect(() => () => { seq.current++; }, []);
  function invalidate() { seq.current++; setResult(null); setConfirm(false); setError(""); setNotice(""); setBusy(false); }
  function choose(next: Field) {
    invalidate(); setField(next);
    if (!source) { setValue(""); return; }
    const address = parseAddr(source.from).email;
    setValue(next === "from" ? address : next === "domain" ? address.split("@")[1] || "" :
      next === "subject" ? normalizeSubject(source.subject) : next === "label" ? source.labels?.[0] || "" : "");
  }
  async function preview(next = 0) {
    const id = ++seq.current; setBusy(true); setError(""); setResult(null); setConfirm(false); setOffset(next);
    const p = new URLSearchParams({ folder, limit: "100", offset: String(next) });
    p.set(field === "domain" ? "from" : field === "text" ? "q" : field === "label" ? "keyword" : field,
      field === "domain" ? `@${value.trim().replace(/^@/, "")}` : value.trim());
    if (before) p.set("before", before);
    if (read) p.set("seen", "true");
    if (protect) p.set("flagged", "false");
    try { const r = await api.get<SearchResult>(`/mail/${account}/search?${p}`); if (id === seq.current) setResult(r); }
    catch (e) { if (id === seq.current) setError((e as Error).message); }
    finally { if (id === seq.current) setBusy(false); }
  }
  async function move() {
    if (!result?.items.length || !dest || busy) return;
    const id = ++seq.current; setBusy(true); setError("");
    const items = result.items;
    try {
      await api.post(`/mail/${account}/messages/batch-move`, { folder, dest,
        uids: items.map(m => m.uid), uidvalidity: items[0].uidvalidity });
      if (id !== seq.current) return;
      setResult(null); setConfirm(false); setOffset(0); onChanged();
      setNotice(L(`${items.length} Mails verschoben. Vorschau neu laden, um weitere Treffer aufzuräumen.`,
        `${items.length} messages moved. Refresh the preview to clean up more matches.`));
      if (rememberRule && !value.includes(",") && ["from", "domain", "subject"].includes(field)) {
        try {
          await api.post(`/mail/${account}/rules`, { field: field === "domain" ? "from_domain" : field,
            value: value.trim().replace(field === "domain" ? /^@/ : /$^/, ""), target_folder: dest });
          if (id === seq.current) setNotice(L("Mails verschoben und Regel gespeichert.", "Messages moved and rule saved."));
        } catch (e) { if (id === seq.current) setError(L("Mails wurden verschoben; Regel konnte nicht gespeichert werden: ",
          "Messages were moved; could not save the rule: ") + (e as Error).message); }
      }
    } catch (e) { if (id === seq.current) { setResult(null); setConfirm(false); setError((e as Error).message + L(" — Vorschau vor erneutem Versuch neu laden.", " — refresh the preview before retrying.")); onChanged(); } }
    finally { if (id === seq.current) setBusy(false); }
  }
  const targets = folders.filter(f => f.name !== folder && f.special !== "noselect");
  const editable = !busy;
  return <Modal label={L("Ähnliche Mails / Postfach aufräumen", "Similar messages / mailbox cleanup")} onClose={() => { if (!busy) onClose(); }}>
    <div className="mail-cleanup">
      <h2>{L("Ähnliche Mails / Postfach aufräumen", "Similar messages / mailbox cleanup")}</h2>
      <p>{L("Suche im gesamten Ordner: ", "Search the entire folder: ")}{folder}</p>
      {views.length > 0 && <label>{L("Gespeicherte Ansicht", "Saved view")} <select disabled={busy} value="" onChange={e => {
        const v = views[Number(e.target.value)]; if (!v) return; invalidate(); setField(v.field); setValue(v.value); setBefore(v.before); setRead(v.read); setProtect(v.protect);
      }}><option value="">{L("Ansicht laden", "Load view")}</option>{views.map((v, i) => <option key={v.name} value={i}>{v.name}</option>)}</select>
        <button disabled={busy} onClick={() => { try { localStorage.removeItem(viewKey); setViews([]); } catch { setError(L("Ansichten konnten nicht entfernt werden.", "Could not remove views.")); } }}>{L("Ansichten entfernen", "Remove views")}</button></label>}
      <label>{L("Gruppieren nach", "Group by")} <select disabled={!editable} value={field} onChange={e => choose(e.target.value as Field)}>
        <option value="from">{L("Absender enthält", "Sender contains")}</option>
        <option value="domain">{L("Absender-Domain enthält", "Sender domain contains")}</option>
        <option value="subject">{L("Betreff enthält", "Subject contains")}</option>
        <option value="text">{L("Thema / Stichwort im Mailtext", "Topic / keyword in message text")}</option>
        <option value="label">{L("Label", "Label")}</option>
      </select></label>
      {field === "label" ? <select disabled={!editable} value={value} onChange={e => { invalidate(); setValue(e.target.value); }}>
        <option value="">{L("Label wählen", "Choose label")}</option>{labels.map(l => <option key={l.keyword} value={l.keyword}>{l.name}</option>)}
      </select> : <input data-modal-focus disabled={!editable} maxLength={200} value={value} aria-label={L("Suchwert", "Search value")}
        onChange={e => { invalidate(); setValue(e.target.value); }} placeholder={L("Adresse, Betreff oder Stichwort", "Address, subject or keyword")} />}
      <label>{L("Älter als (optional)", "Before date (optional)")} <input type="date" disabled={!editable} value={before} onChange={e => { invalidate(); setBefore(e.target.value); }} /></label>
      <label><input type="checkbox" disabled={!editable} checked={read} onChange={e => { invalidate(); setRead(e.target.checked); }} /> {L("Nur gelesene Mails", "Only read messages")}</label>
      <label><input type="checkbox" disabled={!editable} checked={protect} onChange={e => { invalidate(); setProtect(e.target.checked); }} /> {L("Markierte Mails behalten", "Keep starred messages")}</label>
      <div className="row"><input disabled={busy} value={viewName} maxLength={80} onChange={e => setViewName(e.target.value)} placeholder={L("Name für gespeicherte Ansicht", "Saved view name")} aria-label={L("Name für gespeicherte Ansicht", "Saved view name")} />
        <button disabled={busy || !viewName.trim() || !value.trim()} onClick={saveView}>{L("Ansicht merken", "Save view")}</button></div>
      <button disabled={busy || !value.trim() || (field === "domain" && !value.trim().replace(/^@/, "")) || (field === "text" && value.trim().length < 2)} onClick={() => preview()}>{busy ? L("Läuft …", "Working …") : L("Alle Treffer suchen / Vorschau", "Find all matches / preview")}</button>
      {error && <p role="alert" className="err">{error}</p>}{notice && <p role="status">{notice}</p>}
      {result && <>
        <p role="status">{result.items.length} {L("angezeigt", "shown")} / {result.total_count ?? result.items.length} {L("Treffer", "matches")}
          {!result.complete && ` — ${L("unvollständig; Suche erneut versuchen", "incomplete; retry search")}`}</p>
        <div className="mail-cleanup-results">{result.items.map(m => <button key={m.uid} disabled={busy} onClick={() => onRead(m)}>
          <b>{m.subject || L("(kein Betreff)", "(no subject)")}</b><span>{m.from} · {listDate(m.date)}</span>
        </button>)}</div>
        <div className="row">
          <button disabled={busy || offset === 0} onClick={() => preview(Math.max(0, offset - 100))}>{L("Zurück", "Previous")}</button>
          <button disabled={busy || !result.truncated} onClick={() => preview(offset + 100)}>{L("Weitere Treffer", "More matches")}</button>
        </div>
        <label>{L("Verschieben nach", "Move to")} <select disabled={busy} value={dest} onChange={e => { setDest(e.target.value); setConfirm(false); }}>
          <option value="">{L("Archiv, Papierkorb oder Ordner wählen", "Choose archive, trash or folder")}</option>
          {targets.map(f => <option key={f.name} value={f.name}>{f.name}</option>)}
        </select></label>
        {!value.includes(",") && ["from", "domain", "subject"].includes(field) && <label><input type="checkbox" disabled={busy} checked={rememberRule} onChange={e => setRememberRule(e.target.checked)} />
          {L("Als Regel für künftige Mails speichern (Posteingang; enthält den Suchwert, ohne Alters-/Gelesen-/Sternfilter)", "Save a rule for future mail (inbox; contains the search value, without date/read/star filters)")}</label>}
        {!confirm ? <button disabled={busy || !dest || !result.complete || !result.items.length} onClick={() => setConfirm(true)}>{L("Diese Vorschau aufräumen", "Clean up this preview")}</button> :
          <button className="primary" disabled={busy} onClick={move}>{L(`Diese ${result.items.length} Mails nach „${dest}“ verschieben`, `Move these ${result.items.length} messages to “${dest}”`)}</button>}
      </>}
      <button disabled={busy} onClick={onClose}>{L("Schließen", "Close")}</button>
    </div>
  </Modal>;
}

import { useEffect, useState } from "react";
import { api, type DavAccount, type DavKind, type FeedToken, type SyncResult } from "../lib/api";

const EMPTY = { kind: "caldav" as DavKind, label: "", url: "", username: "", password: "" };

// Macht eine ggf. relative Feed-URL fuer Kopieren/Abo absolut.
function absolute(url: string): string {
  return url.startsWith("http") ? url : window.location.origin + url;
}
function fmt(iso: string | null): string {
  if (!iso) return "noch nie";
  return new Date(iso).toLocaleString("de-DE", { dateStyle: "medium", timeStyle: "short" });
}

export function Sync() {
  const [feed, setFeed] = useState<FeedToken | null>(null);
  const [accounts, setAccounts] = useState<DavAccount[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");

  async function load() {
    try {
      setFeed(await api.get<FeedToken>("/feeds/token"));
      setAccounts(await api.get<DavAccount[]>("/dav/accounts"));
    } catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); }, []);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function copy(url: string) {
    try { await navigator.clipboard.writeText(absolute(url)); setNote("In die Zwischenablage kopiert."); }
    catch { setNote(absolute(url)); }
  }
  async function rotate() {
    if (!confirm("Neuen Token erzeugen? Bestehende Abos werden ungültig.")) return;
    try { setFeed(await api.post<FeedToken>("/feeds/token/rotate")); setNote("Token rotiert."); }
    catch (e) { setErr((e as Error).message); }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setNote("");
    if (!form.url || !form.password) { setErr("URL und Passwort sind nötig."); return; }
    try {
      await api.post<DavAccount>("/dav/accounts", form);
      setForm({ ...EMPTY });
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function sync(acc: DavAccount) {
    setBusy(acc.id); setErr(""); setNote("");
    try {
      const r = await api.post<SyncResult>(`/dav/accounts/${acc.id}/sync`);
      if (r.ok) setNote(`Sync „${acc.label}“: ${r.imported} neu, ${r.updated} aktualisiert, ${r.removed} entfernt.`);
      else setErr(`Sync fehlgeschlagen: ${r.error}`);
      load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(null); }
  }
  async function remove(acc: DavAccount) {
    if (!confirm(`Konto „${acc.label}“ und alle importierten Einträge löschen?`)) return;
    try { await api.del(`/dav/accounts/${acc.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <div className="stack" style={{ gap: "1.6rem" }}>
      {err && <div className="err">{err}</div>}
      {note && <div className="card" style={{ padding: "0.6rem 1rem" }}>{note}</div>}

      {/* Abonnierbare Export-Feeds */}
      <section className="stack">
        <div className="label">Abonnier-Links (Handy-Kalender / Adressbuch)</div>
        <p className="muted" style={{ margin: 0 }}>
          Diese Links enthalten einen geheimen Token. Im Handy-Kalender als
          abonnierten Kalender bzw. im Adressbuch als CardDAV/Datei-Quelle eintragen.
        </p>
        {feed && (
          <div className="stack">
            {[
              { label: "Kalender (.ics)", url: feed.calendar_url },
              { label: "Kontakte (.vcf)", url: feed.contacts_url },
            ].map((f) => (
              <div className="card row" style={{ padding: "0.7rem 1rem" }} key={f.label}>
                <div className="grow" style={{ overflow: "hidden" }}>
                  <div style={{ fontWeight: 600 }}>{f.label}</div>
                  <div className="mail-from" style={{ wordBreak: "break-all" }}>{absolute(f.url)}</div>
                </div>
                <button className="ghost" onClick={() => copy(f.url)}>Kopieren</button>
                <a className="ghost" href={absolute(f.url)} target="_blank" rel="noreferrer"
                   style={{ textDecoration: "none" }}>Öffnen</a>
              </div>
            ))}
            <div className="row">
              <span className="grow" />
              <button className="ghost" onClick={rotate}>Token neu erzeugen</button>
            </div>
          </div>
        )}
      </section>

      {/* Externe CalDAV/CardDAV-Konten */}
      <section className="stack">
        <div className="label">Externe CalDAV/CardDAV-Konten</div>
        <form className="card stack" style={{ padding: "1rem" }} onSubmit={add}>
          <div className="row">
            <select value={form.kind} onChange={(e) => set("kind", e.target.value as DavKind)}>
              <option value="caldav">CalDAV (Kalender)</option>
              <option value="carddav">CardDAV (Kontakte)</option>
            </select>
            <input placeholder="Bezeichnung" value={form.label} onChange={(e) => set("label", e.target.value)} />
          </div>
          <input placeholder="Collection-URL (z. B. https://nextcloud/remote.php/dav/calendars/user/personal/)"
                 value={form.url} onChange={(e) => set("url", e.target.value)} required />
          <div className="row">
            <input placeholder="Benutzername" value={form.username} onChange={(e) => set("username", e.target.value)} />
            <input type="password" placeholder="Passwort / App-Token" value={form.password}
                   onChange={(e) => set("password", e.target.value)} required />
            <button className="primary">Hinzufügen</button>
          </div>
        </form>

        {accounts.length === 0 && <p className="muted">Noch keine externen Konten verbunden.</p>}
        <div className="stack">
          {accounts.map((acc) => (
            <div className="card row" style={{ padding: "0.7rem 1rem" }} key={acc.id}>
              <div className="grow">
                <div style={{ fontWeight: 600 }}>
                  {acc.label} <span className="label">{acc.kind === "caldav" ? "Kalender" : "Kontakte"}</span>
                </div>
                <div className="mail-from">
                  Letzter Sync: {fmt(acc.last_sync)}{acc.last_status && acc.last_status !== "ok" ? ` · ${acc.last_status}` : ""}
                </div>
              </div>
              <button className="ghost" disabled={busy === acc.id} onClick={() => sync(acc)}>
                {busy === acc.id ? "Synchronisiere…" : "Jetzt synchronisieren"}
              </button>
              <button className="ghost" onClick={() => remove(acc)}>Löschen</button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

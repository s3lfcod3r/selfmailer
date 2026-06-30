import { useEffect, useState } from "react";
import { api, copyText, type Account, type DavAccount, type DavKind, type FeedToken, type GcalCalendar, type MigrateResult, type SyncResult } from "../lib/api";
import { useLang, dateLocale, type Lang, type TFunc } from "../lib/i18n";
import { confirmDialog } from "../lib/dialog";
import { safeLinkUrl } from "../lib/url";

const EMPTY = { kind: "caldav" as DavKind, label: "", url: "", username: "", password: "" };

// Macht eine ggf. relative Feed-URL für Kopieren/Abo absolut.
function absolute(url: string): string {
  return url.startsWith("http") ? url : window.location.origin + url;
}
function fmt(iso: string | null, lang: Lang, t: TFunc): string {
  if (!iso) return t("sync.never");
  return new Date(iso).toLocaleString(dateLocale(lang), { dateStyle: "medium", timeStyle: "short" });
}

export function Sync() {
  const { t, lang } = useLang();
  const [feed, setFeed] = useState<FeedToken | null>(null);
  const [accounts, setAccounts] = useState<DavAccount[]>([]);
  const [form, setForm] = useState({ ...EMPTY });
  const [busy, setBusy] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
  // Automatische Kalender-Erkennung (Discovery)
  const [disc, setDisc] = useState({ kind: "caldav" as DavKind, url: "", username: "", password: "" });
  const [found, setFound] = useState<{ url: string; name: string }[] | null>(null);
  const [discBusy, setDiscBusy] = useState(false);
  // iCal-Feed-Abo (read-only, z. B. Google secret .ics)
  const [ics, setIcs] = useState({ label: "", url: "" });
  // Google-Kalender via OAuth (refresh_token-Verfahren)
  const [goog, setGoog] = useState({ email: "", client_id: "", client_secret: "", refresh_token: "", label: "" });
  const [googBusy, setGoogBusy] = useState(false);
  // Kalender ein-/ausblenden (pro Google-Konto dessen Kalender; teilt sich den
  // localStorage-Schlüssel mit dem Kalender-Filter → Ausblenden wirkt überall).
  const [calsByAcc, setCalsByAcc] = useState<Record<number, GcalCalendar[]>>({});
  const [hiddenCals, setHiddenCals] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("selfmailer.hiddenCals") || "[]")); } catch { return new Set(); }
  });
  // Schreibt die Auswahl in localStorage (Sofort-Cache) UND auf den Server,
  // damit die App denselben Stand sieht.
  function persistHidden(n: Set<string>) {
    const arr = [...n];
    localStorage.setItem("selfmailer.hiddenCals", JSON.stringify(arr));
    api.put("/calendar/hidden", { keys: arr }).catch(() => {});
  }
  function toggleCal(id: string) {
    setHiddenCals((s) => {
      const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id);
      persistHidden(n);
      return n;
    });
  }
  // Beim Öffnen Server-Stand laden; bestehende Browser-Auswahl einmalig übernehmen.
  useEffect(() => {
    (async () => {
      try {
        const h = await api.get<{ keys: string[] }>("/calendar/hidden");
        const server = new Set(h.keys);
        const local = new Set<string>(
          JSON.parse(localStorage.getItem("selfmailer.hiddenCals") || "[]"),
        );
        const union = new Set([...server, ...local]);
        setHiddenCals(union);
        localStorage.setItem("selfmailer.hiddenCals", JSON.stringify([...union]));
        if (union.size !== server.size) api.put("/calendar/hidden", { keys: [...union] }).catch(() => {});
      } catch { /* Server optional */ }
    })();
  }, []);
  // Standardkalender für neue Termine (Wert "accId::calId" oder "local").
  const [defaultCal, setDefaultCalState] = useState<string>(() => localStorage.getItem("selfmailer.defaultCal") || "");
  function setDefaultCal(val: string) {
    setDefaultCalState(val);
    localStorage.setItem("selfmailer.defaultCal", val);
    setNote("Standardkalender gesetzt.");
  }
  // Postfach-Migration (Synology → passende Zielkonten)
  const [mailAccounts, setMailAccounts] = useState<Account[]>([]);
  const [mig, setMig] = useState({ sourceId: 0, destId: 0, prefix: "", limit: 5000 });
  const [migResult, setMigResult] = useState<MigrateResult | null>(null);
  const [migBusy, setMigBusy] = useState(false);

  async function load() {
    try {
      setFeed(await api.get<FeedToken>("/feeds/token"));
      const accs = await api.get<DavAccount[]>("/dav/accounts");
      setAccounts(accs);
      setMailAccounts(await api.get<Account[]>("/accounts"));
      // Kalender je Google-Konto laden (für die Ein-/Ausblenden-Verwaltung).
      const map: Record<number, GcalCalendar[]> = {};
      for (const a of accs.filter((x) => x.kind === "gcal")) {
        try { map[a.id] = await api.get<GcalCalendar[]>(`/dav/accounts/${a.id}/calendars`); } catch { /* egal */ }
      }
      setCalsByAcc(map);
    } catch (e) { setErr((e as Error).message); }
  }

  async function runMigrate(dry: boolean) {
    if (!mig.sourceId || !mig.destId) { setErr(t("mig.needAccounts")); return; }
    if (mig.sourceId === mig.destId) { setErr(t("mig.sameAccount")); return; }
    if (!dry && !(await confirmDialog(t("mig.confirm")))) return;
    setMigBusy(true); setErr(""); setNote(""); setMigResult(null);
    try {
      setMigResult(await api.post<MigrateResult>(`/mail/${mig.sourceId}/migrate`, {
        dest_account_id: mig.destId, target_prefix: mig.prefix.trim(),
        dry_run: dry, limit: mig.limit,
      }));
    } catch (e) { setErr((e as Error).message); }
    finally { setMigBusy(false); }
  }
  useEffect(() => { load(); }, []);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function copy(url: string) {
    const ok = await copyText(absolute(url));
    setNote(ok ? t("sync.copied") : absolute(url));
  }
  async function rotate() {
    if (!(await confirmDialog(t("sync.rotateConfirm")))) return;
    try { setFeed(await api.post<FeedToken>("/feeds/token/rotate")); setNote(t("sync.rotated")); }
    catch (e) { setErr((e as Error).message); }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setNote("");
    if (!form.url || !form.password) { setErr(t("sync.needUrlPw")); return; }
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
      if (r.ok) setNote(t("sync.result", { label: acc.label, imported: r.imported, updated: r.updated, removed: r.removed }));
      else setErr(t("sync.failed", { error: r.error }));
      load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(null); }
  }
  async function remove(acc: DavAccount) {
    if (!(await confirmDialog(t("sync.removeConfirm", { label: acc.label })))) return;
    try { await api.del(`/dav/accounts/${acc.id}`); load(); }
    catch (e) { setErr((e as Error).message); }
  }

  async function runDiscover(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setNote(""); setFound(null);
    if (!disc.url || !disc.password) { setErr("Server-Adresse und Passwort werden benötigt."); return; }
    setDiscBusy(true);
    try {
      const cols = await api.post<{ url: string; name: string }[]>("/dav/discover", {
        kind: disc.kind, url: disc.url, username: disc.username, password: disc.password,
      });
      setFound(cols);
      if (cols.length === 0) setNote("Keine Kalender gefunden — Server-Adresse/Zugang prüfen oder die Collection-URL unten manuell eintragen.");
    } catch (e) { setErr((e as Error).message); }
    finally { setDiscBusy(false); }
  }
  async function connect(col: { url: string; name: string }) {
    setErr(""); setNote("");
    try {
      await api.post<DavAccount>("/dav/accounts", {
        kind: disc.kind, label: col.name, url: col.url, username: disc.username, password: disc.password,
      });
      setNote(`„${col.name}" verbunden — jetzt unten „Abgleichen" antippen.`);
      setFound((f) => (f ? f.filter((c) => c.url !== col.url) : f));
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function addIcs(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setNote("");
    if (!ics.url) { setErr("iCal-URL fehlt."); return; }
    try {
      await api.post<DavAccount>("/dav/accounts", {
        kind: "ics", label: ics.label || "iCal-Abo", url: ics.url, username: "", password: "",
      });
      setIcs({ label: "", url: "" });
      setNote("iCal-Abo hinzugefügt — unten Abgleichen antippen.");
      load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function addGoogle(e: React.FormEvent) {
    e.preventDefault();
    setErr(""); setNote("");
    if (!goog.email || !goog.client_id || !goog.client_secret || !goog.refresh_token) {
      setErr("Bitte alle Google-Felder ausfüllen."); return;
    }
    setGoogBusy(true);
    try {
      await api.post<DavAccount>("/dav/google", goog);
      setGoog({ email: "", client_id: "", client_secret: "", refresh_token: "", label: "" });
      setNote("Google-Kalender verbunden — unten Abgleichen antippen.");
      load();
    } catch (e) { setErr((e as Error).message); }
    finally { setGoogBusy(false); }
  }
  async function syncAll() {
    setErr(""); setNote("");
    for (const acc of accounts) {
      try { await api.post<SyncResult>(`/dav/accounts/${acc.id}/sync`); } catch { /* einzeln ignorieren */ }
    }
    setNote("Alle Kalender/Adressbücher abgeglichen."); load();
  }

  return (
    <div className="stack" style={{ gap: "1.6rem" }}>
      {err && <div className="err">{err}</div>}
      {note && <div className="card" style={{ padding: "0.6rem 1rem" }}>{note}</div>}

      {/* Postfach-Migration: aus einem Quellkonto (z. B. Synology IMAP) in die
          passenden Zielkonten anhand des Empfängers. */}
      <section className="stack">
        <div className="label">{t("mig.heading")}</div>
        <p className="muted" style={{ margin: 0 }}>{t("mig.hint")}</p>
        <div className="card stack" style={{ padding: "1rem" }}>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <select value={mig.sourceId} onChange={(e) => setMig((m) => ({ ...m, sourceId: Number(e.target.value) }))}>
              <option value={0}>{t("mig.pickSource")}</option>
              {mailAccounts.map((a) => <option key={a.id} value={a.id}>{a.label || a.email}</option>)}
            </select>
            <span aria-hidden>→</span>
            <select value={mig.destId} onChange={(e) => setMig((m) => ({ ...m, destId: Number(e.target.value) }))}>
              <option value={0}>{t("mig.pickDest")}</option>
              {mailAccounts.map((a) => <option key={a.id} value={a.id}>{a.label || a.email}</option>)}
            </select>
            <input style={{ maxWidth: 200 }} value={mig.prefix} placeholder={t("mig.prefixPlaceholder")}
                   onChange={(e) => setMig((m) => ({ ...m, prefix: e.target.value }))} title={t("mig.prefix")} />
          </div>
          <div className="row">
            <span className="muted" style={{ fontSize: "0.8rem" }}>{t("mig.limitHint", { n: mig.limit })}</span>
            <span className="grow" />
            <button className="ghost" disabled={migBusy} onClick={() => runMigrate(true)}>
              {migBusy ? t("mig.running") : t("mig.dryRun")}
            </button>
            <button className="primary" disabled={migBusy} onClick={() => runMigrate(false)}>{t("mig.run")}</button>
          </div>
          {migResult && (
            <div className="stack" style={{ gap: "0.2rem", fontSize: "0.86rem", borderTop: "1px solid var(--self-line)", paddingTop: "0.7rem" }}>
              <div className="label">{migResult.dry_run ? t("mig.previewResult") : t("mig.doneResult")}</div>
              {migResult.folders.filter((f) => f.count > 0).map((f) => (
                <div key={f.source} className="row" style={{ gap: "0.5rem" }}>
                  <span className="grow" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.source} → {f.dest}</span>
                  <span className="muted" style={{ flex: "0 0 auto" }}>
                    {migResult.dry_run ? t("mig.willCopy", { n: f.count }) : t("mig.copiedSkipped", { c: f.copied, s: f.skipped })}
                  </span>
                </div>
              ))}
              {migResult.errors.length > 0 && <div className="err">{migResult.errors.join("; ")}</div>}
            </div>
          )}
        </div>
      </section>

      {/* Abonnierbare Export-Feeds */}
      <section className="stack">
        <div className="label">{t("sync.feedHeading")}</div>
        <p className="muted" style={{ margin: 0 }}>{t("sync.feedHint")}</p>
        {feed && (
          <div className="stack">
            {[
              { label: t("sync.feedCalendar"), url: feed.calendar_url },
              { label: t("sync.feedContacts"), url: feed.contacts_url },
            ].map((f) => (
              <div className="card row" style={{ padding: "0.7rem 1rem" }} key={f.label}>
                <div className="grow" style={{ overflow: "hidden" }}>
                  <div style={{ fontWeight: 600 }}>{f.label}</div>
                  <div className="mail-from" style={{ wordBreak: "break-all" }}>{absolute(f.url)}</div>
                </div>
                <button className="ghost" onClick={() => copy(f.url)}>{t("sync.copy")}</button>
                <a className="ghost" href={safeLinkUrl(absolute(f.url)) ?? "#"} target="_blank" rel="noreferrer"
                   style={{ textDecoration: "none" }}>{t("sync.open")}</a>
              </div>
            ))}
            <div className="row">
              <span className="grow" />
              <button className="ghost" onClick={rotate}>{t("sync.regenToken")}</button>
            </div>
          </div>
        )}
      </section>

      {/* Automatische Kalender-Erkennung (Discovery) */}
      <section className="stack">
        <div className="label">Kalender automatisch finden</div>
        <p className="muted" style={{ margin: 0 }}>
          Server-Adresse + E-Mail + Passwort eingeben — SelfMailer sucht die Kalender selbst.
          Beispiele: web.de <code>https://caldav.web.de</code>, GMX <code>https://caldav.gmx.net</code>,
          iCloud <code>https://caldav.icloud.com</code> (App-Passwort), Nextcloud die eigene Server-Adresse.
          <strong>Google geht so NICHT</strong> (verlangt OAuth) — dafür unten die iCal-Abo-URL nutzen.
        </p>
        <form className="card stack" style={{ padding: "1rem" }} onSubmit={runDiscover}>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <select value={disc.kind} onChange={(e) => setDisc((d) => ({ ...d, kind: e.target.value as DavKind }))}>
              <option value="caldav">Kalender</option>
              <option value="carddav">Kontakte</option>
            </select>
            <input className="grow" placeholder="Server-Adresse (z. B. https://caldav.web.de)"
                   value={disc.url} onChange={(e) => setDisc((d) => ({ ...d, url: e.target.value }))} required />
          </div>
          <div className="row">
            <input placeholder="E-Mail / Benutzer" value={disc.username}
                   onChange={(e) => setDisc((d) => ({ ...d, username: e.target.value }))} />
            <input type="password" placeholder="Passwort / App-Passwort" value={disc.password}
                   onChange={(e) => setDisc((d) => ({ ...d, password: e.target.value }))} required />
            <button className="primary" disabled={discBusy}>{discBusy ? "Suche…" : "Suchen"}</button>
          </div>
        </form>
        {found && found.length > 0 && (
          <div className="stack">
            {found.map((c) => (
              <div className="card row" style={{ padding: "0.7rem 1rem" }} key={c.url}>
                <div className="grow">
                  <div style={{ fontWeight: 600 }}>{c.name}</div>
                  <div className="mail-from" style={{ wordBreak: "break-all" }}>{c.url}</div>
                </div>
                <button className="primary" onClick={() => connect(c)}>Verbinden</button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* iCal-Feed abonnieren (read-only) — der einfache Google-Weg ohne OAuth */}
      <section className="stack">
        <div className="label">Kalender per iCal-URL abonnieren (read-only)</div>
        <p className="muted" style={{ margin: 0 }}>
          Für <strong>Google</strong> (das kein App-Passwort erlaubt): in Google Kalender →
          Einstellungen → den Kalender wählen → „Geheime Adresse im iCal-Format" kopieren und hier einfügen.
          Funktioniert auch für jeden anderen .ics-Feed. Nur Anzeigen (kein Zurückschreiben).
        </p>
        <form className="card stack" style={{ padding: "1rem" }} onSubmit={addIcs}>
          <div className="row">
            <input placeholder="Name (z. B. Google privat)" value={ics.label}
                   onChange={(e) => setIcs((s) => ({ ...s, label: e.target.value }))} />
          </div>
          <div className="row">
            <input className="grow" placeholder="iCal-URL (…/basic.ics)" value={ics.url}
                   onChange={(e) => setIcs((s) => ({ ...s, url: e.target.value }))} required />
            <button className="primary">Abonnieren</button>
          </div>
        </form>
      </section>

      {/* Google-Kalender via OAuth (zwei Wege, sobald Schreiben aktiv ist) */}
      <section className="stack">
        <div className="label">Google-Kalender verbinden (OAuth)</div>
        <p className="muted" style={{ margin: 0 }}>
          Google verlangt OAuth (App-Passwort geht nicht). Einmalige Einrichtung in der{" "}
          <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Google Cloud Console</a>:
          OAuth-Client (Webanwendung) anlegen, als Redirect <code>https://developers.google.com/oauthplayground</code> eintragen,
          Google-Calendar-API aktivieren. Dann im{" "}
          <a href="https://developers.google.com/oauthplayground" target="_blank" rel="noreferrer">OAuth Playground</a>{" "}
          (Zahnrad → „Use your own OAuth credentials") mit Scope{" "}
          <code>https://www.googleapis.com/auth/calendar</code> ein <strong>refresh_token</strong> holen und hier eintragen.
        </p>
        <form className="card stack" style={{ padding: "1rem" }} onSubmit={addGoogle}>
          <input placeholder="Google-E-Mail" value={goog.email}
                 onChange={(e) => setGoog((g) => ({ ...g, email: e.target.value }))} required />
          <input placeholder="Name (optional)" value={goog.label}
                 onChange={(e) => setGoog((g) => ({ ...g, label: e.target.value }))} />
          <input placeholder="client_id" value={goog.client_id}
                 onChange={(e) => setGoog((g) => ({ ...g, client_id: e.target.value }))} required />
          <input placeholder="client_secret" value={goog.client_secret}
                 onChange={(e) => setGoog((g) => ({ ...g, client_secret: e.target.value }))} required />
          <div className="row">
            <input className="grow" placeholder="refresh_token" value={goog.refresh_token}
                   onChange={(e) => setGoog((g) => ({ ...g, refresh_token: e.target.value }))} required />
            <button className="primary" disabled={googBusy}>{googBusy ? "Prüfe…" : "Verbinden"}</button>
          </div>
        </form>
      </section>

      {/* Kalender ein-/ausblenden (z. B. „Kalenderwochen" weg) */}
      {Object.values(calsByAcc).some((c) => c.length > 0) && (
        <section className="stack">
          <div className="label">Kalender anzeigen / ausblenden</div>
          <p className="muted" style={{ margin: 0 }}>
            Häkchen entfernt = Kalender ausgeblendet (z. B. „Kalenderwochen"/„Feiertage").
            Der ★ markiert den <strong>Standardkalender</strong> — dort landen neue Termine vorausgewählt.
          </p>
          {accounts.filter((a) => a.kind === "gcal").map((a) => (
            <div className="card stack" style={{ padding: "0.8rem 1rem" }} key={a.id}>
              <div style={{ fontWeight: 600 }}>{a.label || a.username}</div>
              {(calsByAcc[a.id] || []).map((c) => {
                const on = !hiddenCals.has(c.id);
                const val = `${a.id}::${c.id}`;
                const isDefault = defaultCal === val;
                return (
                  <div key={c.id} style={{ display: "flex", alignItems: "center", gap: "0.6rem", padding: "0.35rem 0.1rem" }}>
                    <span style={{ width: 11, height: 11, borderRadius: "50%", background: c.color || "var(--self-teal)", flex: "0 0 auto" }} />
                    <span style={{ flex: 1, opacity: on ? 1 : 0.45, textDecoration: on ? "none" : "line-through" }}>{c.name}</span>
                    {c.writable && (
                      <button type="button" className="ghost" title={isDefault ? "Standardkalender" : "Als Standard für neue Termine"}
                              onClick={() => setDefaultCal(val)} style={{ padding: "0 0.4rem", color: isDefault ? "var(--self-teal-bright)" : "var(--self-text-3)" }}>
                        {isDefault ? "★" : "☆"}
                      </button>
                    )}
                    <input type="checkbox" checked={on} onChange={() => toggleCal(c.id)} style={{ flex: "0 0 auto", width: 18, height: 18 }} title="Anzeigen/Ausblenden" />
                  </div>
                );
              })}
            </div>
          ))}
        </section>
      )}

      {/* Externe CalDAV/CardDAV-Konten */}
      <section className="stack">
        <div className="row">
          <div className="label grow">{t("sync.externalHeading")}</div>
          {accounts.length > 0 && <button className="ghost" onClick={syncAll}>Alle abgleichen</button>}
        </div>
        <form className="card stack" style={{ padding: "1rem" }} onSubmit={add}>
          <div className="row">
            <select value={form.kind} onChange={(e) => set("kind", e.target.value as DavKind)}>
              <option value="caldav">{t("sync.caldavOption")}</option>
              <option value="carddav">{t("sync.carddavOption")}</option>
            </select>
            <input placeholder={t("common.label")} value={form.label} onChange={(e) => set("label", e.target.value)} />
          </div>
          <input placeholder={t("sync.collectionUrl")}
                 value={form.url} onChange={(e) => set("url", e.target.value)} required />
          <div className="row">
            <input placeholder={t("common.username")} value={form.username} onChange={(e) => set("username", e.target.value)} />
            <input type="password" placeholder={t("sync.appToken")} value={form.password}
                   onChange={(e) => set("password", e.target.value)} required />
            <button className="primary">{t("common.add")}</button>
          </div>
        </form>

        {accounts.length === 0 && <p className="muted">{t("sync.externalEmpty")}</p>}
        <div className="stack">
          {accounts.map((acc) => (
            <div className="card row" style={{ padding: "0.7rem 1rem" }} key={acc.id}>
              <div className="grow">
                <div style={{ fontWeight: 600 }}>
                  {acc.label} <span className="label">{acc.kind === "carddav" ? t("sync.kindContacts") : t("sync.kindCalendar")}</span>
                </div>
                <div className="mail-from">
                  {t("sync.lastSync", { when: fmt(acc.last_sync, lang, t) })}{acc.last_status && acc.last_status !== "ok" ? ` · ${acc.last_status}` : ""}
                </div>
              </div>
              <button className="ghost" disabled={busy === acc.id} onClick={() => sync(acc)}>
                {busy === acc.id ? t("sync.syncing") : t("sync.syncNow")}
              </button>
              <button className="ghost" onClick={() => remove(acc)}>{t("common.delete")}</button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

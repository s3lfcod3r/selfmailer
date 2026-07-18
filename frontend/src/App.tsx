import { lazy, Suspense, useEffect, useState, type ComponentType } from "react";
import { api, type User } from "./lib/api";
import { useLang } from "./lib/i18n";
import { DialogHost } from "./lib/dialog";
import { Login } from "./pages/Login";
import { Mail } from "./pages/Mail";
import { Wordmark } from "./components/Wordmark";
import { LangPicker } from "./components/LangPicker";
import { ErrorBoundary } from "./components/ErrorBoundary";

// Lazy-Import mit Selbstheilung: schlägt das Laden eines Chunks fehl — typisch
// direkt NACH einem Deploy, wenn die alten Chunk-Hashes nicht mehr existieren und
// ein noch offener Tab sie anfordert — wird die Seite EINMAL hart neu geladen, um
// frisches index.html + die neuen Chunks zu holen. Der Zeit-Guard verhindert eine
// Reload-Schleife, falls ein Chunk wirklich kaputt ist.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function lazyWithReload<T extends ComponentType<any>>(
  factory: () => Promise<{ default: T }>,
) {
  return lazy(() =>
    factory().catch((err: unknown) => {
      const last = Number(sessionStorage.getItem("selfmailer.chunkReload") || 0);
      if (Date.now() - last > 10000) {
        sessionStorage.setItem("selfmailer.chunkReload", String(Date.now()));
        window.location.reload();
        return new Promise<{ default: T }>(() => {}); // hängt bis zum Reload
      }
      throw err;
    }),
  );
}

// Sekundäre Views per Code-Splitting: sie (und ihre Deps, z. B. qrcode in
// TotpSettings) liegen in eigenen Chunks und belasten das Initial-Bundle nicht.
// Mail bleibt eager (Default-View, bleibt gemountet).
const Notes = lazyWithReload(() => import("./pages/Notes").then((m) => ({ default: m.Notes })));
const Accounts = lazyWithReload(() => import("./pages/Accounts").then((m) => ({ default: m.Accounts })));
const Calendar = lazyWithReload(() => import("./pages/Calendar").then((m) => ({ default: m.Calendar })));
const Contacts = lazyWithReload(() => import("./pages/Contacts").then((m) => ({ default: m.Contacts })));
const Sync = lazyWithReload(() => import("./pages/Sync").then((m) => ({ default: m.Sync })));
const Admin = lazyWithReload(() => import("./pages/Admin").then((m) => ({ default: m.Admin })));
const Rules = lazyWithReload(() => import("./pages/Rules").then((m) => ({ default: m.Rules })));
const Notify = lazyWithReload(() => import("./pages/Notify").then((m) => ({ default: m.Notify })));
const TotpSettings = lazyWithReload(() => import("./components/TotpSettings").then((m) => ({ default: m.TotpSettings })));

type View = "mail" | "calendar" | "contacts" | "notes" | "sync" | "accounts" | "admin" | "rules" | "notify";

// --- Theme-Anpassung: eigene Farben als CSS-Variablen-Overrides ---------------
type ThemeCustom = { bg: string; surface: string; text: string; accent: string; unread: string };
const EMPTY_CUSTOM: ThemeCustom = { bg: "", surface: "", text: "", accent: "", unread: "" };
// Akzent-Vorschläge (erster = Self-Teal-Standard).
const ACCENTS = ["#33a78c", "#1db8d4", "#7c6cf0", "#3fb950", "#e0883a", "#e0588f"];
// Standard-Farben je Modus (Vorschau-Default der Farbwähler, wenn nichts eigenes gesetzt ist).
const THEME_DEFAULTS: Record<string, ThemeCustom> = {
  dark: { bg: "#080c11", surface: "#161b22", text: "#d4e4de", accent: "#33a78c", unread: "#33a78c" },
  light: { bg: "#eef1f6", surface: "#ffffff", text: "#1a2230", accent: "#0a9d8c", unread: "#0a9d8c" },
};

function _adj(hex: string, amt: number): string {
  const h = hex.replace("#", "");
  if (h.length !== 6) return hex;
  const ch = (i: number) => parseInt(h.slice(i, i + 2), 16);
  const f = (c: number) => Math.max(0, Math.min(255, Math.round(c + amt * 255))).toString(16).padStart(2, "0");
  return `#${f(ch(0))}${f(ch(2))}${f(ch(4))}`;
}

// Setzt/entfernt die Override-CSS-Variablen auf <html> anhand der eigenen Farben.
function applyCustom(c: ThemeCustom): void {
  const el = document.documentElement;
  const set = (k: string, v: string) => (v ? el.style.setProperty(k, v) : el.style.removeProperty(k));
  set("--self-bg-0", c.bg);
  set("--self-bg-1", c.bg ? _adj(c.bg, 0.03) : "");
  set("--self-bg-2", c.surface);
  set("--self-bg-3", c.surface ? _adj(c.surface, 0.05) : "");
  set("--self-text", c.text);
  set("--self-teal", c.accent);
  set("--self-teal-bright", c.accent ? _adj(c.accent, 0.12) : "");
  set("--self-teal-deep", c.accent ? _adj(c.accent, -0.18) : "");
  set("--self-unread", c.unread);
}

type AppItem = { key: View; labelKey: string; icon: string; adminOnly?: boolean };

// Haupt-Apps — als Icons direkt in der Topbar (neben dem Benutzer).
const APPS: AppItem[] = [
  { key: "mail", labelKey: "nav.mail", icon: "✉" },
  { key: "calendar", labelKey: "nav.calendar", icon: "📅" },
  { key: "contacts", labelKey: "nav.contacts", icon: "📇" },
  { key: "notes", labelKey: "nav.notes", icon: "🗒" },
];
// Im Benutzer-Menü: Sync & Export + Einstellungen.
const SETTINGS: AppItem[] = [
  { key: "notify", labelKey: "nav.notify", icon: "🔔" },
  { key: "sync", labelKey: "nav.sync", icon: "🔄" },
  { key: "rules", labelKey: "nav.rules", icon: "🔀" },
  { key: "accounts", labelKey: "nav.accounts", icon: "⚙" },
  { key: "admin", labelKey: "nav.admin", icon: "👥", adminOnly: true },
];

export function App() {
  const { t } = useLang();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [view, setView] = useState<View>("mail");
  // Gesamt-Ungelesen (von Mail gemeldet) — Badge am Mail-Icon, auch außerhalb des Mailbereichs.
  const [mailUnseen, setMailUnseen] = useState(0);
  const [search, setSearch] = useState("");
  const [menu, setMenu] = useState<"apps" | "user" | "filter" | null>(null);
  const [filter, setFilter] = useState({ from: "", subject: "", dateFrom: "", dateTo: "", unread: false, starred: false, attachments: false });
  const [pwOpen, setPwOpen] = useState(false);
  const [totpOpen, setTotpOpen] = useState(false);
  const [pwCur, setPwCur] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwMsg, setPwMsg] = useState("");
  const [pwErr, setPwErr] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [theme, setTheme] = useState<string>(() => localStorage.getItem("selfmailer.theme") || "dark");
  const [pollMin, setPollMin] = useState<number>(() => {
    const v = Number(localStorage.getItem("selfmailer.pollMin"));
    return [0, 1, 5, 15, 30].includes(v) ? v : 5;
  });
  // Externe Bilder (Tracking-Pixel) standardmäßig blockieren — Datenschutz/Sicherheit.
  const [blockImages, setBlockImages] = useState<boolean>(() => localStorage.getItem("selfmailer.blockImages") !== "0");
  // Helle Mails automatisch in den dunklen Look umfaerben (global; pro Mail übersteuerbar).
  // Default AN: der früher unlesbare Fall (hell-auf-hell) lag an einem Bug im Dunkel-Stil
  // (Hintergrund wurde mitinvertiert) — behoben. Pro Mail per 🌙/☀️ umschaltbar.
  const [darkMail, setDarkMail] = useState<boolean>(() => localStorage.getItem("selfmailer.darkMail") !== "0");
  // Markierte (Stern-)Mails oben anheften. Default AUS — die gewohnte rein
  // chronologische Liste bleibt damit die Voreinstellung.
  const [pinFlagged, setPinFlagged] = useState<boolean>(() => localStorage.getItem("selfmailer.pinFlagged") === "1");
  // App-eigene Textgröße (skaliert alle rem-Einheiten über die Wurzel-Schrift),
  // damit Lesbarkeit OHNE Browser-Zoom (der die Layout-Breite schrumpft) möglich ist.
  const [uiScale, setUiScale] = useState<number>(() => {
    const v = Number(localStorage.getItem("selfmailer.uiScale"));
    return [100, 110, 125, 150].includes(v) ? v : 100;
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("selfmailer.theme", theme);
  }, [theme]);
  useEffect(() => { localStorage.setItem("selfmailer.pollMin", String(pollMin)); }, [pollMin]);
  useEffect(() => { localStorage.setItem("selfmailer.blockImages", blockImages ? "1" : "0"); }, [blockImages]);
  useEffect(() => { localStorage.setItem("selfmailer.darkMail", darkMail ? "1" : "0"); }, [darkMail]);
  useEffect(() => { localStorage.setItem("selfmailer.pinFlagged", pinFlagged ? "1" : "0"); }, [pinFlagged]);
  useEffect(() => {
    document.documentElement.style.fontSize = uiScale === 100 ? "" : `${uiScale}%`;
    localStorage.setItem("selfmailer.uiScale", String(uiScale));
  }, [uiScale]);
  // Eigene Theme-Farben (Overrides). Leerer Wert = Standard des gewählten Modus.
  // Eigene Farben GETRENNT pro Modus (dunkel/hell haben jeweils eigene Werte).
  const [themeCustomAll, setThemeCustomAll] = useState<{ dark: ThemeCustom; light: ThemeCustom }>(() => {
    try {
      const raw = JSON.parse(localStorage.getItem("selfmailer.themeCustom") || "{}");
      // Migration: alte (modus-übergreifende) Werte dem Dunkel-Modus zuordnen.
      if (raw && ("bg" in raw || "accent" in raw || "text" in raw || "surface" in raw)) {
        return { dark: { ...EMPTY_CUSTOM, ...raw }, light: { ...EMPTY_CUSTOM } };
      }
      return { dark: { ...EMPTY_CUSTOM, ...(raw.dark || {}) }, light: { ...EMPTY_CUSTOM, ...(raw.light || {}) } };
    } catch { return { dark: { ...EMPTY_CUSTOM }, light: { ...EMPTY_CUSTOM } }; }
  });
  const [designOpen, setDesignOpen] = useState(false);
  const tmode: "dark" | "light" = theme === "light" ? "light" : "dark";
  const themeCustom = themeCustomAll[tmode];
  const setThemeCustom = (updater: (c: ThemeCustom) => ThemeCustom) =>
    setThemeCustomAll((all) => ({ ...all, [tmode]: updater(all[tmode]) }));
  useEffect(() => {
    applyCustom(themeCustomAll[tmode]);
    localStorage.setItem("selfmailer.themeCustom", JSON.stringify(themeCustomAll));
  }, [themeCustomAll, tmode]);

  function loadMe() {
    // Eingeloggt? Das Cookie ist httpOnly (für JS unsichtbar) — daher fragen wir
    // einfach /auth/me. 200 = angemeldet, 401 = Login anzeigen.
    api.get<User>("/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }
  useEffect(() => { loadMe(); }, []);

  if (!ready) return <div className="auth-wrap"><span className="muted">{t("common.loading")}</span></div>;
  if (!user) return <Login onAuthed={() => { setReady(false); loadMe(); }} />;

  const isAdmin = user.role === "admin";
  const apps = APPS;
  const settings = SETTINGS.filter((s) => !s.adminOnly || isAdmin);

  function go(v: View) { setView(v); setMenu(null); }
  function logout() {
    // Cookie serverseitig löschen; UI sofort ausloggen (Fehler ignorieren).
    api.post("/auth/logout").catch(() => {});
    localStorage.removeItem("selfmailer.token"); // Alt-Token aus früherer Version aufräumen
    setUser(null);
  }
  function openPw() { setMenu(null); setPwErr(""); setPwMsg(""); setPwCur(""); setPwNew(""); setPwOpen(true); }
  async function changePw(e: React.FormEvent) {
    e.preventDefault();
    setPwErr(""); setPwMsg(""); setPwBusy(true);
    try {
      await api.post("/auth/password", { current_password: pwCur, new_password: pwNew });
      setPwMsg(t("pw.changed")); setPwCur(""); setPwNew("");
    } catch (err) { setPwErr((err as Error).message); }
    finally { setPwBusy(false); }
  }

  return (
    <div className="app-shell">
      <header className="topbar-main">
        <div className="topbar-brand"><Wordmark size={1.05} /></div>

        <div className="topbar-search">
          <span aria-hidden>🔍</span>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("search.placeholder")}
          />
          {search && <button className="search-filter" onClick={() => setSearch("")}>✕</button>}
          <button
            className={`search-filter ${(filter.from || filter.subject || filter.dateFrom || filter.dateTo || filter.unread || filter.starred || filter.attachments) ? "on" : ""}`}
            title={t("filter.title")}
            onClick={() => setMenu(menu === "filter" ? null : "filter")}
          >⚙</button>
        </div>

        <div className="topbar-actions">
          {apps.map((a) => (
            <button
              key={a.key}
              className={`icon-btn ${view === a.key ? "on" : ""}`}
              style={a.key === "mail" ? { position: "relative" } : undefined}
              title={t(a.labelKey)}
              onClick={() => go(a.key)}
            >
              {a.icon}
              {a.key === "mail" && mailUnseen > 0 && (
                <span className="nav-badge" aria-label={`${mailUnseen} ungelesen`}>{mailUnseen > 99 ? "99+" : mailUnseen}</span>
              )}
            </button>
          ))}
          <button className="user-chip" onClick={() => setMenu(menu === "user" ? null : "user")}>
            <span>👤</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 120, fontSize: "0.84rem" }}>
              {user.display_name || user.username}
            </span>
            <span aria-hidden>▾</span>
          </button>
        </div>
      </header>

      {menu && <div className="menu-backdrop" onClick={() => setMenu(null)} />}

      {menu === "filter" && (
        <div className="filter-menu">
          <div className="filter-head">
            <span>{t("filter.title")}</span>
            <button className="filter-reset" onClick={() => setFilter({ from: "", subject: "", dateFrom: "", dateTo: "", unread: false, starred: false, attachments: false })}>
              ↺ {t("filter.reset")}
            </button>
          </div>
          <div className="stack" style={{ gap: "0.6rem" }}>
            <div className="stack">
              <label className="label">{t("filter.from")}</label>
              <input value={filter.from} onChange={(e) => setFilter((f) => ({ ...f, from: e.target.value }))} />
            </div>
            <div className="stack">
              <label className="label">{t("filter.subject")}</label>
              <input value={filter.subject} onChange={(e) => setFilter((f) => ({ ...f, subject: e.target.value }))} />
            </div>
            <div className="row">
              <div className="stack" style={{ flex: 1, minWidth: 0 }}>
                <label className="label">{t("filter.dateFrom")}</label>
                <input type="date" value={filter.dateFrom} onChange={(e) => setFilter((f) => ({ ...f, dateFrom: e.target.value }))} />
              </div>
              <div className="stack" style={{ flex: 1, minWidth: 0 }}>
                <label className="label">{t("filter.dateTo")}</label>
                <input type="date" value={filter.dateTo} onChange={(e) => setFilter((f) => ({ ...f, dateTo: e.target.value }))} />
              </div>
            </div>
            <div className="filter-chips">
              <button className={filter.starred ? "on" : ""} onClick={() => setFilter((f) => ({ ...f, starred: !f.starred }))}>
                ★ {t("filter.starred")}
              </button>
              <button className={filter.unread ? "on" : ""} onClick={() => setFilter((f) => ({ ...f, unread: !f.unread }))}>
                ● {t("filter.unread")}
              </button>
              <button className={filter.attachments ? "on" : ""} onClick={() => setFilter((f) => ({ ...f, attachments: !f.attachments }))}>
                📎 {t("filter.attachments")}
              </button>
            </div>
          </div>
        </div>
      )}

      {menu === "user" && (
        <div className="user-menu">
          <div className="user-menu-header">
            <div className="user-menu-name">{user.display_name || user.username}</div>
            <div className="user-menu-mail">{user.username}{isAdmin && <span className="user-menu-role">{t("shell.adminBadge")}</span>}</div>
          </div>

          <div className="user-menu-section">{t("menu.manage")}</div>
          {settings.map((s) => (
            <button key={s.key} onClick={() => go(s.key)}><span>{s.icon}</span> {t(s.labelKey)}</button>
          ))}

          <div className="user-menu-section">{t("menu.security")}</div>
          <button onClick={openPw}><span>🔑</span> {t("user.changePassword")}</button>
          <button onClick={() => { setMenu(null); setTotpOpen(true); }}><span>🛡</span> {t("totp.menu")}</button>
          <button onClick={() => setBlockImages((b) => !b)}>
            <span>🖼</span> {t("shell.blockImages")}
            <span style={{ marginLeft: "auto", color: blockImages ? "var(--self-teal-bright)" : "var(--self-text-3)" }}>{blockImages ? "✓" : "—"}</span>
          </button>
          <button onClick={() => setPinFlagged((b) => !b)}>
            <span>⭐</span> {t("shell.pinFlagged")}
            <span style={{ marginLeft: "auto", color: pinFlagged ? "var(--self-teal-bright)" : "var(--self-text-3)" }}>{pinFlagged ? "✓" : "—"}</span>
          </button>

          <div className="user-menu-section">{t("menu.appearance")}</div>
          <div className="user-menu-row" onClick={(e) => e.stopPropagation()}>
            <span>🌐 {t("shell.langSwitch")}</span>
            <LangPicker />
          </div>
          <button onClick={() => setTheme((tm) => (tm === "dark" ? "light" : "dark"))}>
            <span>{theme === "dark" ? "☀" : "🌙"}</span> {theme === "dark" ? t("shell.themeLight") : t("shell.themeDark")}
          </button>
          <button onClick={() => setDarkMail((b) => !b)}>
            <span>🌗</span> {t("shell.darkMail")}
            <span style={{ marginLeft: "auto", color: darkMail ? "var(--self-teal-bright)" : "var(--self-text-3)" }}>{darkMail ? "✓" : "—"}</span>
          </button>
          <button onClick={() => { setMenu(null); setDesignOpen(true); }}><span>🎨</span> {t("shell.design")}</button>
          <div className="user-menu-row" onClick={(e) => e.stopPropagation()}>
            <span>🔠 {t("shell.textSize")}</span>
            <select value={uiScale} onChange={(e) => setUiScale(Number(e.target.value))}>
              <option value={100}>100%</option>
              <option value={110}>110%</option>
              <option value={125}>125%</option>
              <option value={150}>150%</option>
            </select>
          </div>
          <div className="user-menu-row" onClick={(e) => e.stopPropagation()}>
            <span>🔄 {t("shell.autoRefresh")}</span>
            <select value={pollMin} onChange={(e) => setPollMin(Number(e.target.value))}>
              <option value={0}>{t("shell.autoOff")}</option>
              <option value={1}>1 min</option>
              <option value={5}>5 min</option>
              <option value={15}>15 min</option>
              <option value={30}>30 min</option>
            </select>
          </div>

          <hr />
          <button className="user-menu-logout" onClick={logout}><span>⎋</span> {t("shell.logout")}</button>
        </div>
      )}

      <main className="app-main">
        {/* Fehlergrenze: ein Render-Fehler einer View blendet nur den Inhaltsbereich
            auf einen Notfall-Bildschirm um — Kopfzeile/Navigation bleiben nutzbar,
            statt dass die gesamte App weißscreent. */}
        <ErrorBoundary>
          {/* Mail bleibt gemountet (nur versteckt), damit beim Zurückwechseln
              nicht neu geladen wird – kein sichtbares Nachladen. */}
          <div style={{ display: view === "mail" ? "contents" : "none" }}>
            <Mail search={search} filter={filter} pollMin={pollMin} blockImages={blockImages} darkMail={darkMail} pinFlagged={pinFlagged} onUnseenChange={setMailUnseen} />
          </div>
          <Suspense fallback={<div className="muted">{t("common.loading")}</div>}>
            {view === "calendar" && <Calendar />}
            {view === "contacts" && <Contacts />}
            {view === "notes" && <Notes />}
            {view === "sync" && <Sync />}
            {view === "notify" && <Notify />}
            {view === "accounts" && <Accounts />}
            {view === "rules" && <Rules />}
            {view === "admin" && isAdmin && <Admin meId={user.id} />}
          </Suspense>
        </ErrorBoundary>
      </main>

      {designOpen && (
        <div className="modal-backdrop" onClick={() => setDesignOpen(false)}>
          <div className="modal card stack" onClick={(e) => e.stopPropagation()}>
            <div className="topbar">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>🎨 {t("design.title")}</h2>
              <button type="button" className="ghost" onClick={() => setDesignOpen(false)}>✕</button>
            </div>

            <div className="stack">
              <label className="label">{t("design.mode")}</label>
              <div className="row">
                <button className={theme === "dark" ? "primary" : "ghost"} onClick={() => setTheme("dark")}>🌙 {t("shell.themeDark")}</button>
                <button className={theme === "light" ? "primary" : "ghost"} onClick={() => setTheme("light")}>☀ {t("shell.themeLight")}</button>
              </div>
            </div>

            <div className="stack">
              <label className="label">{t("design.accent")}</label>
              <div className="design-swatches">
                {ACCENTS.map((c) => (
                  <button key={c} className={`design-swatch ${themeCustom.accent === c ? "on" : ""}`} style={{ background: c }}
                    onClick={() => setThemeCustom((t0) => ({ ...t0, accent: c }))} title={c} />
                ))}
                <label className="design-pick" title={t("design.custom")}>
                  🎨<input type="color" value={themeCustom.accent || THEME_DEFAULTS[tmode].accent} onChange={(e) => setThemeCustom((t0) => ({ ...t0, accent: e.target.value }))} />
                </label>
              </div>
            </div>

            <div className="stack">
              <label className="label">{t("design.ownColors")}</label>
              {([
                ["bg", t("design.bg")],
                ["surface", t("design.surface")],
                ["text", t("design.text")],
                ["unread", t("design.unread")],
              ] as const).map(([key, label]) => (
                <div className="design-color-row" key={key}>
                  <span>{label}</span>
                  <span className="grow" />
                  {themeCustom[key] && <button className="ghost design-clear" title={t("design.resetOne")} onClick={() => setThemeCustom((t0) => ({ ...t0, [key]: "" }))}>↺</button>}
                  <input type="color" value={themeCustom[key] || THEME_DEFAULTS[tmode][key]} onChange={(e) => setThemeCustom((t0) => ({ ...t0, [key]: e.target.value }))} />
                </div>
              ))}
            </div>

            <div className="row">
              <button className="ghost" onClick={() => setThemeCustom(() => ({ ...EMPTY_CUSTOM }))}>↺ {t("design.reset")}</button>
              <span className="grow" />
              <button className="primary" onClick={() => setDesignOpen(false)}>{t("design.done")}</button>
            </div>
          </div>
        </div>
      )}

      {totpOpen && (
        <Suspense fallback={null}>
          <TotpSettings onClose={() => setTotpOpen(false)} />
        </Suspense>
      )}

      {pwOpen && (
        <div className="modal-backdrop" onClick={() => setPwOpen(false)}>
          <form className="modal card stack" onClick={(e) => e.stopPropagation()} onSubmit={changePw}>
            <div className="topbar">
              <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{t("user.changePassword")}</h2>
              <button type="button" className="ghost" onClick={() => setPwOpen(false)}>✕</button>
            </div>
            <div className="stack">
              <label className="label">{t("pw.current")}</label>
              <input type="password" value={pwCur} onChange={(e) => setPwCur(e.target.value)} autoFocus required />
            </div>
            <div className="stack">
              <label className="label">{t("pw.new")}</label>
              <input type="password" value={pwNew} onChange={(e) => setPwNew(e.target.value)} required />
            </div>
            {pwErr && <div className="err">{pwErr}</div>}
            {pwMsg && <div className="muted">{pwMsg}</div>}
            <div className="row">
              <span className="grow" />
              <button type="button" className="ghost" onClick={() => setPwOpen(false)}>{t("common.cancel")}</button>
              <button className="primary" disabled={pwBusy}>{pwBusy ? "…" : t("pw.save")}</button>
            </div>
          </form>
        </div>
      )}

      <DialogHost />
    </div>
  );
}

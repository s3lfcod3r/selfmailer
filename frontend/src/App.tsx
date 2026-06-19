import { useEffect, useState } from "react";
import { api, auth, type User } from "./lib/api";
import { useLang } from "./lib/i18n";
import { Login } from "./pages/Login";
import { Notes } from "./pages/Notes";
import { Accounts } from "./pages/Accounts";
import { Mail } from "./pages/Mail";
import { Calendar } from "./pages/Calendar";
import { Contacts } from "./pages/Contacts";
import { Sync } from "./pages/Sync";
import { Admin } from "./pages/Admin";
import { Rules } from "./pages/Rules";
import { Wordmark } from "./components/Wordmark";
import { TotpSettings } from "./components/TotpSettings";

type View = "mail" | "calendar" | "contacts" | "notes" | "sync" | "accounts" | "admin" | "rules";

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
  { key: "sync", labelKey: "nav.sync", icon: "🔄" },
  { key: "rules", labelKey: "nav.rules", icon: "🔀" },
  { key: "accounts", labelKey: "nav.accounts", icon: "⚙" },
  { key: "admin", labelKey: "nav.admin", icon: "👥", adminOnly: true },
];

export function App() {
  const { t, lang, setLang } = useLang();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [view, setView] = useState<View>("mail");
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

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("selfmailer.theme", theme);
  }, [theme]);
  useEffect(() => { localStorage.setItem("selfmailer.pollMin", String(pollMin)); }, [pollMin]);

  function loadMe() {
    if (!auth.get()) { setUser(null); setReady(true); return; }
    api.get<User>("/auth/me")
      .then(setUser)
      .catch(() => { auth.clear(); setUser(null); })
      .finally(() => setReady(true));
  }
  useEffect(() => { loadMe(); }, []);

  if (!ready) return <div className="auth-wrap"><span className="muted">{t("common.loading")}</span></div>;
  if (!user) return <Login onAuthed={() => { setReady(false); loadMe(); }} />;

  const isAdmin = user.role === "admin";
  const apps = APPS;
  const settings = SETTINGS.filter((s) => !s.adminOnly || isAdmin);

  function go(v: View) { setView(v); setMenu(null); }
  function logout() { auth.clear(); setUser(null); }
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
              title={t(a.labelKey)}
              onClick={() => go(a.key)}
            >{a.icon}</button>
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

          <div className="user-menu-section">{t("menu.appearance")}</div>
          <button onClick={() => { setLang(lang === "de" ? "en" : "de"); setMenu(null); }}>
            <span>🌐</span> {t("shell.langSwitch")}
          </button>
          <button onClick={() => setTheme((tm) => (tm === "dark" ? "light" : "dark"))}>
            <span>{theme === "dark" ? "☀" : "🌙"}</span> {theme === "dark" ? t("shell.themeLight") : t("shell.themeDark")}
          </button>
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
        {/* Mail bleibt gemountet (nur versteckt), damit beim Zurückwechseln
            nicht neu geladen wird – kein sichtbares Nachladen. */}
        <div style={{ display: view === "mail" ? "contents" : "none" }}>
          <Mail search={search} filter={filter} pollMin={pollMin} />
        </div>
        {view === "calendar" && <Calendar />}
        {view === "contacts" && <Contacts />}
        {view === "notes" && <Notes />}
        {view === "sync" && <Sync />}
        {view === "accounts" && <Accounts />}
        {view === "rules" && <Rules />}
        {view === "admin" && isAdmin && <Admin meId={user.id} />}
      </main>

      {totpOpen && <TotpSettings onClose={() => setTotpOpen(false)} />}

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
    </div>
  );
}

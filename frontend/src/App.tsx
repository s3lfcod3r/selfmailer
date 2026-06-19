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
import { Wordmark } from "./components/Wordmark";

type View = "mail" | "calendar" | "contacts" | "notes" | "sync" | "accounts" | "admin";

type AppItem = { key: View; labelKey: string; icon: string; adminOnly?: boolean };

// App-Switcher (4-Kachel-Menü) — die „Apps".
const APPS: AppItem[] = [
  { key: "mail", labelKey: "nav.mail", icon: "✉" },
  { key: "calendar", labelKey: "nav.calendar", icon: "📅" },
  { key: "contacts", labelKey: "nav.contacts", icon: "👤" },
  { key: "notes", labelKey: "nav.notes", icon: "🗒" },
  { key: "sync", labelKey: "nav.sync", icon: "🔄" },
];
// Einstellungen im Benutzer-Menü.
const SETTINGS: AppItem[] = [
  { key: "accounts", labelKey: "nav.accounts", icon: "⚙" },
  { key: "admin", labelKey: "nav.admin", icon: "👥", adminOnly: true },
];

const ALL = [...APPS, ...SETTINGS];

export function App() {
  const { t, lang, setLang } = useLang();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [view, setView] = useState<View>("mail");
  const [search, setSearch] = useState("");
  const [menu, setMenu] = useState<"apps" | "user" | null>(null);
  const [theme, setTheme] = useState<string>(() => localStorage.getItem("selfmailer.theme") || "dark");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("selfmailer.theme", theme);
  }, [theme]);

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
  const current = ALL.find((a) => a.key === view);

  function go(v: View) { setView(v); setMenu(null); }
  function logout() { auth.clear(); setUser(null); }

  return (
    <div className="app-shell">
      <header className="topbar-main">
        <div className="topbar-brand"><Wordmark /></div>

        <div className="topbar-search">
          <span aria-hidden>🔍</span>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("search.placeholder")}
          />
          {search && <button className="ghost" style={{ padding: "0 0.3rem" }} onClick={() => setSearch("")}>✕</button>}
        </div>

        <div className="topbar-actions">
          <button className="icon-btn" title={t("apps.title")} onClick={() => setMenu(menu === "apps" ? null : "apps")}>▦</button>
          <button className="user-chip" onClick={() => setMenu(menu === "user" ? null : "user")}>
            <span>👤</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 140 }}>
              {user.display_name || user.username}
            </span>
            {isAdmin && <span className="label">{t("shell.adminBadge")}</span>}
            <span aria-hidden>▾</span>
          </button>
        </div>
      </header>

      {menu && <div className="menu-backdrop" onClick={() => setMenu(null)} />}

      {menu === "apps" && (
        <div className="app-switcher">
          {apps.map((a) => (
            <button key={a.key} className={`app-tile ${view === a.key ? "active" : ""}`} onClick={() => go(a.key)}>
              <span className="app-ico">{a.icon}</span>
              <span>{t(a.labelKey)}</span>
            </button>
          ))}
        </div>
      )}

      {menu === "user" && (
        <div className="user-menu">
          {settings.map((s) => (
            <button key={s.key} onClick={() => go(s.key)}><span>{s.icon}</span> {t(s.labelKey)}</button>
          ))}
          <hr />
          <button onClick={() => { setLang(lang === "de" ? "en" : "de"); setMenu(null); }}>
            <span>🌐</span> {t("shell.langSwitch")}
          </button>
          <button onClick={() => setTheme((tm) => (tm === "dark" ? "light" : "dark"))}>
            <span>{theme === "dark" ? "☀" : "🌙"}</span> {theme === "dark" ? t("shell.themeLight") : t("shell.themeDark")}
          </button>
          <hr />
          <button onClick={logout}><span>⎋</span> {t("shell.logout")}</button>
        </div>
      )}

      <main className="app-main">
        {view !== "mail" && (
          <div className="view-title">{t(current?.labelKey ?? "")}</div>
        )}
        {view === "mail" && <Mail search={search} />}
        {view === "calendar" && <Calendar />}
        {view === "contacts" && <Contacts />}
        {view === "notes" && <Notes />}
        {view === "sync" && <Sync />}
        {view === "accounts" && <Accounts />}
        {view === "admin" && isAdmin && <Admin meId={user.id} />}
      </main>
    </div>
  );
}

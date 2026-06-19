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

type NavItem = { key: View; labelKey: string; icon: string; adminOnly?: boolean };

const NAV: NavItem[] = [
  { key: "mail", labelKey: "nav.mail", icon: "✉" },
  { key: "calendar", labelKey: "nav.calendar", icon: "📅" },
  { key: "contacts", labelKey: "nav.contacts", icon: "👤" },
  { key: "notes", labelKey: "nav.notes", icon: "🗒" },
  { key: "sync", labelKey: "nav.sync", icon: "🔄" },
  { key: "accounts", labelKey: "nav.accounts", icon: "⚙" },
  { key: "admin", labelKey: "nav.admin", icon: "👥", adminOnly: true },
];

export function App() {
  const { t, lang, setLang } = useLang();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [view, setView] = useState<View>("mail");
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
  const nav = NAV.filter((n) => !n.adminOnly || isAdmin);

  function logout() { auth.clear(); setUser(null); }

  return (
    <div className="shell">
      <aside className="side">
        <div style={{ padding: "0.2rem 0.4rem 1rem" }}><Wordmark /></div>
        {nav.map((n) => (
          <div
            key={n.key}
            className={`nav-item ${view === n.key ? "active" : ""}`}
            onClick={() => setView(n.key)}
          >
            <span>{n.icon}</span> {t(n.labelKey)}
          </div>
        ))}
        <span className="grow" />
        <div
          className="nav-item"
          onClick={() => setLang(lang === "de" ? "en" : "de")}
        >
          <span>🌐</span>
          {t("shell.langSwitch")}
        </div>
        <div
          className="nav-item"
          onClick={() => setTheme((tm) => (tm === "dark" ? "light" : "dark"))}
        >
          <span>{theme === "dark" ? "☀" : "🌙"}</span>
          {theme === "dark" ? t("shell.themeLight") : t("shell.themeDark")}
        </div>
        <div className="nav-item" style={{ cursor: "default" }}>
          <span>👤</span>
          <span
            className="grow"
            style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {user.display_name || user.username}
          </span>
          {isAdmin && <span className="label">{t("shell.adminBadge")}</span>}
        </div>
        <div className="nav-item" onClick={logout}><span>⎋</span> {t("shell.logout")}</div>
      </aside>

      <main className="main">
        <div className="topbar">
          <h1 style={{ margin: 0, fontSize: "1.4rem" }}>
            {t(nav.find((n) => n.key === view)?.labelKey ?? "")}
          </h1>
        </div>

        {view === "mail" && <Mail />}
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

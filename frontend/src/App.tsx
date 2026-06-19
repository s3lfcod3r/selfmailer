import { useEffect, useState } from "react";
import { api, auth, type User } from "./lib/api";
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

type NavItem = { key: View; label: string; icon: string; adminOnly?: boolean };

const NAV: NavItem[] = [
  { key: "mail", label: "Mail", icon: "✉" },
  { key: "calendar", label: "Kalender", icon: "📅" },
  { key: "contacts", label: "Kontakte", icon: "👤" },
  { key: "notes", label: "Notizen", icon: "🗒" },
  { key: "sync", label: "Sync & Export", icon: "🔄" },
  { key: "accounts", label: "Konten", icon: "⚙" },
  { key: "admin", label: "Verwaltung", icon: "👥", adminOnly: true },
];

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [view, setView] = useState<View>("mail");

  function loadMe() {
    if (!auth.get()) { setUser(null); setReady(true); return; }
    api.get<User>("/auth/me")
      .then(setUser)
      .catch(() => { auth.clear(); setUser(null); })
      .finally(() => setReady(true));
  }
  useEffect(() => { loadMe(); }, []);

  if (!ready) return <div className="auth-wrap"><span className="muted">Laden…</span></div>;
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
            <span>{n.icon}</span> {n.label}
          </div>
        ))}
        <span className="grow" />
        <div className="nav-item" onClick={logout}><span>⎋</span> Abmelden</div>
      </aside>

      <main className="main">
        <div className="topbar">
          <h1 style={{ margin: 0, fontSize: "1.4rem" }}>
            {nav.find((n) => n.key === view)?.label}
          </h1>
          <div className="row">
            <span className="muted">{user.display_name || user.username}</span>
            {isAdmin && <span className="label">Admin</span>}
          </div>
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

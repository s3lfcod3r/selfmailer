import { useEffect, useState } from "react";
import { api, auth } from "../lib/api";
import { Wordmark } from "../components/Wordmark";

type Token = { access_token: string };

export function Login({ onAuthed }: { onAuthed: () => void }) {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [adminToken, setAdminToken] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<{ needs_setup: boolean }>("/auth/status")
      .then((s) => setNeedsSetup(s.needs_setup))
      .catch(() => setNeedsSetup(false));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      const res = needsSetup
        ? await api.post<Token>("/auth/setup", {
            username, password, display_name: displayName, admin_token: adminToken,
          })
        : await api.post<Token>("/auth/login", { username, password });
      auth.set(res.access_token);
      onAuthed();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (needsSetup === null) return <div className="auth-wrap"><span className="muted">Laden…</span></div>;

  return (
    <div className="auth-wrap">
      <form className="auth-card card stack" onSubmit={submit}>
        <Wordmark size={1.8} />
        <h1>{needsSetup ? "Erstes Konto anlegen" : "Anmelden"}</h1>
        <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>
          {needsSetup
            ? "Lege den Admin-Zugang für SelfMailer an."
            : "Dein eigener Mail-Client."}
        </p>

        <div className="stack">
          <label className="label">Benutzername / E-Mail</label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
        </div>

        {needsSetup && (
          <div className="stack">
            <label className="label">Anzeigename</label>
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
        )}

        <div className="stack">
          <label className="label">Passwort</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>

        {needsSetup && (
          <div className="stack">
            <label className="label">Admin-Token (falls per Env gesetzt)</label>
            <input value={adminToken} onChange={(e) => setAdminToken(e.target.value)} />
          </div>
        )}

        {err && <div className="err">{err}</div>}
        <button className="primary" disabled={busy}>
          {busy ? "…" : needsSetup ? "Konto anlegen" : "Anmelden"}
        </button>
      </form>
    </div>
  );
}

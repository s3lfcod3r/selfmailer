import { useEffect, useState } from "react";
import { api, auth } from "../lib/api";
import { useLang } from "../lib/i18n";
import { Wordmark } from "../components/Wordmark";

type Token = { access_token: string };

export function Login({ onAuthed }: { onAuthed: () => void }) {
  const { t } = useLang();
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

  if (needsSetup === null) return <div className="auth-wrap"><span className="muted">{t("common.loading")}</span></div>;

  return (
    <div className="auth-wrap">
      <form className="auth-card card stack" onSubmit={submit}>
        <Wordmark size={1.8} />
        <h1>{needsSetup ? t("login.titleSetup") : t("login.titleLogin")}</h1>
        <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>
          {needsSetup ? t("login.subSetup") : t("login.subLogin")}
        </p>

        <div className="stack">
          <label className="label">{t("login.userLabel")}</label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
        </div>

        {needsSetup && (
          <div className="stack">
            <label className="label">{t("common.displayName")}</label>
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
        )}

        <div className="stack">
          <label className="label">{t("common.password")}</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </div>

        {needsSetup && (
          <div className="stack">
            <label className="label">{t("login.adminToken")}</label>
            <input value={adminToken} onChange={(e) => setAdminToken(e.target.value)} />
          </div>
        )}

        {err && <div className="err">{err}</div>}
        <button className="primary" disabled={busy}>
          {busy ? "…" : needsSetup ? t("login.submitSetup") : t("login.submitLogin")}
        </button>
      </form>
    </div>
  );
}

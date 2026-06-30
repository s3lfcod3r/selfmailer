import { useEffect, useState } from "react";
import { api, type LoginResponse } from "../lib/api";
import { useLang } from "../lib/i18n";
import { LangPicker } from "../components/LangPicker";
import { Wordmark } from "../components/Wordmark";

export function Login({ onAuthed }: { onAuthed: () => void }) {
  const { t } = useLang();

  // Sprachauswahl — zentriert unten in der Login-Karte (kein Benutzermenü hier).
  const langToggle = (
    <div className="auth-lang-row">
      <LangPicker className="auth-lang" />
    </div>
  );
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [adminToken, setAdminToken] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // 2FA-Zwischenschritt: gesetzt, sobald das Passwort stimmt und 2FA aktiv ist.
  const [mfaToken, setMfaToken] = useState("");
  const [code, setCode] = useState("");

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
      if (needsSetup) {
        // Backend setzt das Session-Cookie selbst (Set-Cookie). Kein Token-Handling im JS.
        await api.post("/auth/setup", {
          username, password, display_name: displayName, admin_token: adminToken,
        });
        onAuthed();
        return;
      }
      const res = await api.post<LoginResponse>("/auth/login", { username, password });
      if (res.needs_totp) {
        setMfaToken(res.mfa_token);   // zweiter Schritt: Code abfragen
        return;
      }
      onAuthed();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await api.post("/auth/login/totp", { mfa_token: mfaToken, code });
      setMfaToken(""); setCode("");
      onAuthed();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function cancelMfa() {
    setMfaToken(""); setCode(""); setErr(""); setPassword("");
  }

  if (needsSetup === null) return <div className="auth-wrap"><span className="muted">{t("common.loading")}</span></div>;

  // ── Zweiter Schritt: 2FA-Code ──────────────────────────────────────────
  if (mfaToken) {
    return (
      <div className="auth-wrap">
        <form className="auth-card card stack" onSubmit={submitCode}>
          <Wordmark size={1.8} />
          <h1>{t("totp.loginTitle")}</h1>
          <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>{t("totp.loginSub")}</p>
          <div className="stack">
            <label className="label">{t("totp.codeLabel")}</label>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="123456"
              autoFocus
              required
            />
          </div>
          {err && <div className="err">{err}</div>}
          <button className="primary" disabled={busy}>{busy ? "…" : t("totp.verify")}</button>
          <button type="button" className="ghost" onClick={cancelMfa}>{t("common.cancel")}</button>
          {langToggle}
        </form>
      </div>
    );
  }

  // ── Erster Schritt: Login / Setup ──────────────────────────────────────
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
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
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
        {langToggle}
      </form>
    </div>
  );
}

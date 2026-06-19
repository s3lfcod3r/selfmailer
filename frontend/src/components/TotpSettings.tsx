import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { api, type TotpSetup, type TotpStatus } from "../lib/api";
import { useLang } from "../lib/i18n";

/**
 * 2FA-Verwaltung als Modal-Inhalt. Drei Zustaende:
 *  - geladen + aus  -> Einrichtung starten (QR + Secret, dann Code bestaetigen)
 *  - geladen + an   -> Status + Deaktivieren (Passwort)
 *  - frisch aktiviert -> Backup-Codes einmalig anzeigen
 * Der QR wird lokal aus der otpauth-URI gerendert; das Secret verlaesst den
 * Browser nie an einen Dritt-Dienst.
 */
export function TotpSettings({ onClose }: { onClose: () => void }) {
  const { t } = useLang();
  const [status, setStatus] = useState<TotpStatus | null>(null);
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [qr, setQr] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { reloadStatus(); }, []);

  function reloadStatus() {
    api.get<TotpStatus>("/auth/totp/status").then(setStatus).catch((e) => setErr((e as Error).message));
  }

  async function startSetup() {
    setErr(""); setBusy(true);
    try {
      const s = await api.post<TotpSetup>("/auth/totp/setup");
      setSetup(s);
      setQr(await QRCode.toDataURL(s.otpauth_uri, { errorCorrectionLevel: "M", margin: 1, width: 220 }));
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function enable(e: React.FormEvent) {
    e.preventDefault(); setErr(""); setBusy(true);
    try {
      const res = await api.post<{ backup_codes: string[] }>("/auth/totp/enable", { code });
      setBackupCodes(res.backup_codes);
      setSetup(null); setCode("");
      reloadStatus();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function disable(e: React.FormEvent) {
    e.preventDefault(); setErr(""); setBusy(true);
    try {
      await api.post("/auth/totp/disable", { password });
      setPassword("");
      reloadStatus();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <form className="modal card stack" onClick={(e) => e.stopPropagation()} onSubmit={(e) => e.preventDefault()}>
        <div className="topbar">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>{t("totp.title")}</h2>
          <button type="button" className="ghost" onClick={onClose}>✕</button>
        </div>

        {!status && !backupCodes && <span className="muted">{t("common.loading")}</span>}

        {/* Frisch aktiviert: Backup-Codes einmalig zeigen */}
        {backupCodes && (
          <div className="stack">
            <div className="muted">{t("totp.enabledOk")}</div>
            <strong>{t("totp.backupTitle")}</strong>
            <p className="muted" style={{ margin: 0, fontSize: "0.84rem" }}>{t("totp.backupHint")}</p>
            <div className="totp-backup-grid">
              {backupCodes.map((c) => <code key={c}>{c}</code>)}
            </div>
            <div className="row">
              <span className="grow" />
              <button type="button" className="primary" onClick={onClose}>{t("totp.done")}</button>
            </div>
          </div>
        )}

        {/* Aktiv: Status + Deaktivieren */}
        {status?.enabled && !backupCodes && (
          <div className="stack">
            <div className="totp-badge on">✓ {t("totp.active")}</div>
            <div className="muted" style={{ fontSize: "0.86rem" }}>
              {t("totp.backupRemaining", { n: String(status.backup_codes_remaining) })}
            </div>
            <hr />
            <strong>{t("totp.disableTitle")}</strong>
            <div className="stack">
              <label className="label">{t("pw.current")}</label>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button type="button" className="ghost" onClick={onClose}>{t("common.cancel")}</button>
              <button type="button" className="danger" disabled={busy || !password} onClick={disable}>
                {busy ? "…" : t("totp.disable")}
              </button>
            </div>
          </div>
        )}

        {/* Aus + noch nicht im Setup: Einrichtung anbieten */}
        {status && !status.enabled && !setup && !backupCodes && (
          <div className="stack">
            <div className="totp-badge">{t("totp.inactive")}</div>
            <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>{t("totp.intro")}</p>
            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button type="button" className="primary" disabled={busy} onClick={startSetup}>
                {busy ? "…" : t("totp.setupStart")}
              </button>
            </div>
          </div>
        )}

        {/* Im Setup: QR + Secret + Code bestaetigen */}
        {setup && (
          <div className="stack">
            <p className="muted" style={{ margin: 0, fontSize: "0.88rem" }}>{t("totp.scanHint")}</p>
            {qr && <img src={qr} alt="QR" width={220} height={220} style={{ alignSelf: "center", borderRadius: 8, background: "#fff", padding: 8 }} />}
            <label className="label">{t("totp.manualKey")}</label>
            <code className="totp-secret">{setup.secret}</code>
            <hr />
            <label className="label">{t("totp.confirmCode")}</label>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode="numeric"
              autoComplete="one-time-code"
              placeholder="123456"
              required
            />
            {err && <div className="err">{err}</div>}
            <div className="row">
              <span className="grow" />
              <button type="button" className="ghost" onClick={() => { setSetup(null); setCode(""); setErr(""); }}>
                {t("common.cancel")}
              </button>
              <button type="button" className="primary" disabled={busy || !code} onClick={enable}>
                {busy ? "…" : t("totp.activate")}
              </button>
            </div>
          </div>
        )}
      </form>
    </div>
  );
}

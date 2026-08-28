import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useLang } from "../lib/i18n";

type Vacation = {
  account_id: number; enabled: boolean; subject: string; body: string;
  start_date: string; end_date: string; interval_days: number;
};

/**
 * Abwesenheitsnotiz eines Kontos (Auto-Antwort). Bewusst defensiv: Standard AUS,
 * antwortet nur auf Mails, die NACH dem Einschalten ankommen, je Absender höchstens
 * alle N Tage, und nie auf Newsletter/automatische Mails (Schleifenschutz im Server).
 */
export function VacationManager({ accountId }: { accountId: number }) {
  const { t } = useLang();
  const [v, setV] = useState<Vacation | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.get<Vacation>(`/vacation/${accountId}`)
      .then((r) => { if (!cancelled) setV(r); })
      .catch((e) => { if (!cancelled) setErr((e as Error).message); });
    return () => { cancelled = true; };
  }, [accountId]);

  function set<K extends keyof Vacation>(k: K, val: Vacation[K]) {
    setV((p) => (p ? { ...p, [k]: val } : p));
    setMsg("");
  }

  async function save() {
    if (!v) return;
    setErr(""); setMsg("");
    if (v.enabled && !v.body.trim()) { setErr(t("vacation.needBody")); return; }
    setBusy(true);
    try {
      setV(await api.put<Vacation>(`/vacation/${accountId}`, {
        enabled: v.enabled, subject: v.subject, body: v.body,
        start_date: v.start_date, end_date: v.end_date, interval_days: v.interval_days,
      }));
      setMsg(t("vacation.saved"));
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  if (!v) return err ? <div className="err">{err}</div> : null;
  return (
    <fieldset className="acc-fieldset">
      <legend>🏖 {t("vacation.section")}</legend>
      <p className="mail-from" style={{ margin: "0 0 0.6rem" }}>{t("vacation.hint")}</p>
      {err && <div className="err" style={{ marginBottom: "0.5rem" }}>{err}</div>}
      {msg && <div className="muted" style={{ marginBottom: "0.5rem" }}>{msg}</div>}

      <label className="acc-check" style={{ marginBottom: "0.5rem" }}>
        <input type="checkbox" checked={v.enabled} onChange={(e) => set("enabled", e.target.checked)} />
        {t("vacation.enable")}
      </label>

      <div className="stack" style={{ gap: "0.5rem" }}>
        <label className="stack"><span className="label">{t("vacation.subject")}</span>
          <input value={v.subject} maxLength={200} onChange={(e) => set("subject", e.target.value)} /></label>
        <label className="stack"><span className="label">{t("vacation.body")}</span>
          <textarea value={v.body} maxLength={5000} rows={4}
            placeholder={t("vacation.bodyPlaceholder")}
            onChange={(e) => set("body", e.target.value)} /></label>
        <div className="acc-grid">
          <label className="stack"><span className="label">{t("vacation.start")}</span>
            <input type="date" value={v.start_date} onChange={(e) => set("start_date", e.target.value)} /></label>
          <label className="stack"><span className="label">{t("vacation.end")}</span>
            <input type="date" value={v.end_date} onChange={(e) => set("end_date", e.target.value)} /></label>
        </div>
        <label className="stack"><span className="label">{t("vacation.interval")}</span>
          <select value={v.interval_days} onChange={(e) => set("interval_days", Number(e.target.value))}>
            <option value={1}>1</option><option value={3}>3</option>
            <option value={7}>7</option><option value={14}>14</option><option value={30}>30</option>
          </select></label>
        <div className="row">
          <span className="grow" />
          <button type="button" className="primary" disabled={busy} onClick={save}>{t("accounts.save")}</button>
        </div>
      </div>
    </fieldset>
  );
}

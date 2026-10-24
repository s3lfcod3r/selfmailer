import { useEffect, useState } from "react";
import { api, type SecurityStatus } from "../lib/api";
import { useLang } from "../lib/i18n";

/**
 * Einstellungen → Sicherheit → Spam- & Virenschutz (seit 1.97.3).
 *
 * Jeder Schalter landet sofort in den geräteübergreifenden Einstellungen
 * (PUT /settings/ui, Teil-Update) und wirkt serverseitig — also auch für die App.
 * Alles ist AUS, bis man es einschaltet. Virenscan und Spamfilter brauchen einen
 * Dienst auf dem Server; ist er nicht eingerichtet, bleibt der Schalter gesperrt
 * (ein bereits eingeschalteter lässt sich trotzdem immer ausschalten).
 */
type Flags = {
  sec_block_all_accounts: boolean;
  sec_unsubscribe: boolean;
  sec_virus_scan: boolean;
  sec_spam_filter: boolean;
};
const KEYS: (keyof Flags)[] = ["sec_block_all_accounts", "sec_unsubscribe", "sec_virus_scan", "sec_spam_filter"];

function Toggle({ on, onChange, label, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      className={on ? "um-switch on" : "um-switch"} onClick={() => onChange(!on)} />
  );
}

export function SecuritySettings() {
  const { t } = useLang();
  const [flags, setFlags] = useState<Flags | null>(null);
  const [status, setStatus] = useState<SecurityStatus | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get<Record<string, unknown>>("/settings/ui")
      .then((s) => setFlags(Object.fromEntries(KEYS.map((k) => [k, s[k] === true])) as Flags))
      .catch((e) => setErr((e as Error).message));
    api.get<SecurityStatus>("/security/status").then(setStatus).catch(() => setStatus(null));
  }, []);

  async function set(key: keyof Flags, value: boolean) {
    if (!flags) return;
    const before = flags;
    setFlags({ ...flags, [key]: value });   // sofort zeigen, bei Fehler zurück
    setErr("");
    try {
      await api.put("/settings/ui", { [key]: value });
    } catch (e) {
      setFlags(before);
      setErr((e as Error).message);
    }
  }

  function serviceLine(kind: "clamav" | "rspamd"): { cls: string; text: string; usable: boolean } {
    const s = status?.[kind];
    if (!s || !s.configured) return { cls: "off", text: t("sec.notConfigured"), usable: false };
    if (!s.reachable) return { cls: "warn", text: t("sec.unreachable"), usable: true };
    if (kind === "clamav") return { cls: "ok", text: `${t("sec.ready")} · ${status!.clamav.version.split("/")[0]}`, usable: true };
    return { cls: "ok", text: status!.rspamd.learning ? t("sec.ready") : `${t("sec.ready")} · ${t("sec.learningOff")}`, usable: true };
  }

  function row(key: keyof Flags, label: string, hint: string, service?: "clamav" | "rspamd") {
    const on = flags?.[key] ?? false;
    const svc = service ? serviceLine(service) : null;
    // Ohne Dienst nicht EINschaltbar - ausschalten geht immer.
    const disabled = !flags || (svc !== null && !svc.usable && !on);
    return (
      <div className="set-row" key={key}>
        <div className="set-row-text">
          <span className="set-row-label">{label}</span>
          <span className="set-row-hint">{hint}</span>
          {svc && <span className={`set-status ${svc.cls}`}>{svc.text}{!svc.usable ? ` – ${t("sec.adminHint")}` : ""}</span>}
        </div>
        <div className="set-row-control">
          <Toggle on={on} disabled={disabled} onChange={(v) => set(key, v)} label={label} />
        </div>
      </div>
    );
  }

  return (
    <section className="set-group">
      <h3 className="set-group-title">{t("sec.title")}</h3>
      <p className="set-intro">{t("sec.intro")}</p>
      {err && <p role="alert" className="set-status warn">{err}</p>}
      <div className="set-card">
        {row("sec_block_all_accounts", t("sec.blockAll"), t("sec.blockAllHint"))}
        {row("sec_unsubscribe", t("sec.unsub"), t("sec.unsubHint"))}
        {row("sec_virus_scan", t("sec.virus"), t("sec.virusHint"), "clamav")}
        {row("sec_spam_filter", t("sec.spam"), t("sec.spamHint"), "rspamd")}
      </div>
    </section>
  );
}

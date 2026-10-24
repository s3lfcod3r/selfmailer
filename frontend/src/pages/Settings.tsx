import { useState } from "react";
import { useLang } from "../lib/i18n";
import { LangPicker } from "../components/LangPicker";
import { SecuritySettings } from "../components/SecuritySettings";

/**
 * Einstellungen-Seite.
 *
 * Sammelt die Optionen, die früher als Schalter im Benutzermenü hingen. Ein
 * Klapp-Menü ist zum Springen da, nicht zum Konfigurieren — dort blieben nur
 * die Ziele stehen.
 *
 * Bewusst NICHT hier: Optionen, die inzwischen fest entschieden sind (externe
 * Bilder werden immer blockiert, die Mail-Ansicht folgt dem App-Theme). Wer sie
 * pro Mail übersteuern will, findet die Knöpfe weiterhin im Lesekopf.
 */
export type SettingsProps = {
  theme: string;
  onTheme: (t: string) => void;
  uiScale: number;
  onUiScale: (n: number) => void;
  pollMin: number;
  onPollMin: (n: number) => void;
  pinFlagged: boolean;
  onPinFlagged: (v: boolean) => void;
  conversationView: boolean;
  onConversationView: (v: boolean) => void;
  onOpenDesign: () => void;
  onOpenPassword: () => void;
  onOpenTotp: () => void;
  appVersion: string;
};

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="set-row">
      <div className="set-row-text">
        <span className="set-row-label">{label}</span>
        {hint && <span className="set-row-hint">{hint}</span>}
      </div>
      <div className="set-row-control">{children}</div>
    </div>
  );
}

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={on ? "um-switch on" : "um-switch"}
      onClick={() => onChange(!on)}
    />
  );
}

export function Settings(p: SettingsProps) {
  const { t } = useLang();
  // Reiter (seit 1.97.3): „Allgemein" und „Sicherheit" (inkl. Spam- & Virenschutz).
  const [tab, setTab] = useState<"general" | "security">(() => {
    try { return localStorage.getItem("selfmailer.settingsTab") === "security" ? "security" : "general"; } catch { return "general"; }
  });
  function pick(next: "general" | "security") {
    setTab(next);
    try { localStorage.setItem("selfmailer.settingsTab", next); } catch { /* nur Komfort */ }
  }
  return (
    <div className="set-page">
      <h2 className="set-title">{t("nav.settings")}</h2>
      <div className="set-tabs" role="tablist">
        <button type="button" role="tab" className="set-tab" aria-selected={tab === "general"} onClick={() => pick("general")}>{t("settings.tabGeneral")}</button>
        <button type="button" role="tab" className="set-tab" aria-selected={tab === "security"} onClick={() => pick("security")}>{t("menu.security")}</button>
      </div>

      {tab === "general" && <>
      <section className="set-group">
        <h3 className="set-group-title">{t("menu.appearance")}</h3>
        <div className="set-card">
          <Row label={p.theme === "dark" ? t("shell.themeLight") : t("shell.themeDark")}>
            <button className="ghost" onClick={() => p.onTheme(p.theme === "dark" ? "light" : "dark")}>
              {p.theme === "dark" ? "☀" : "🌙"}
            </button>
          </Row>
          <Row label={t("shell.design")}>
            <button className="ghost" onClick={p.onOpenDesign}>🎨</button>
          </Row>
          <Row label={t("shell.textSize")}>
            <select value={p.uiScale} onChange={(e) => p.onUiScale(Number(e.target.value))}>
              <option value={100}>100%</option>
              <option value={110}>110%</option>
              <option value={125}>125%</option>
              <option value={150}>150%</option>
            </select>
          </Row>
          <Row label={t("shell.conversationView")}>
            <Toggle on={p.conversationView} onChange={p.onConversationView} label={t("shell.conversationView")} />
          </Row>
          <Row label={t("shell.pinFlagged")}>
            <Toggle on={p.pinFlagged} onChange={p.onPinFlagged} label={t("shell.pinFlagged")} />
          </Row>
        </div>
      </section>

      <section className="set-group">
        <h3 className="set-group-title">{t("settings.general")}</h3>
        <div className="set-card">
          <Row label={t("shell.langSwitch")}>
            <LangPicker />
          </Row>
          <Row label={t("shell.autoRefresh")}>
            <select value={p.pollMin} onChange={(e) => p.onPollMin(Number(e.target.value))}>
              <option value={0}>{t("shell.autoOff")}</option>
              <option value={1}>1 min</option>
              <option value={5}>5 min</option>
              <option value={15}>15 min</option>
              <option value={30}>30 min</option>
            </select>
          </Row>
        </div>
      </section>
      </>}

      {tab === "security" && <>
      <section className="set-group">
        <h3 className="set-group-title">{t("menu.security")}</h3>
        <div className="set-card">
          <Row label={t("user.changePassword")}>
            <button className="ghost" onClick={p.onOpenPassword}>🔑</button>
          </Row>
          <Row label={t("totp.menu")}>
            <button className="ghost" onClick={p.onOpenTotp}>🛡</button>
          </Row>
        </div>
      </section>
      <SecuritySettings />
      </>}

      <div className="set-foot">
        <span>SelfMailer{p.appVersion && ` ${p.appVersion}`}</span>
        <a href="https://github.com/s3lfcod3r/selfmailer" target="_blank" rel="noopener noreferrer">GitHub ↗</a>
      </div>
    </div>
  );
}

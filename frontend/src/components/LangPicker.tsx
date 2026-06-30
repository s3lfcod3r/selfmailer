// Language dropdown — lists all supported languages by their native name.
// Used on the login screen and in the user menu.
import { useLang, LANGS, type Lang } from "../lib/i18n";

export function LangPicker({ className }: { className?: string }) {
  const { lang, setLang, t } = useLang();
  return (
    <select
      className={className}
      value={lang}
      onChange={(e) => setLang(e.target.value as Lang)}
      aria-label={t("shell.langSwitch")}
      title={t("shell.langSwitch")}
    >
      {LANGS.map((l) => (
        <option key={l.code} value={l.code}>
          {l.label}
        </option>
      ))}
    </select>
  );
}

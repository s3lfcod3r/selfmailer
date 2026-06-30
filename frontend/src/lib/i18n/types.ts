// Shared i18n types and language metadata.
// `Dict` is derived from the English source, so every language dictionary
// is forced by the compiler to provide exactly the same set of keys.
import { en } from "./en";

export type TKey = keyof typeof en;
export type Dict = Record<TKey, string>;

// The 12 supported European languages.
export type Lang =
  | "de" | "en" | "fr" | "es" | "it" | "nl"
  | "pl" | "pt" | "sv" | "da" | "cs" | "el";

export interface LangMeta {
  code: Lang;
  /** Native name shown in the picker. */
  label: string;
  /** BCP-47 locale for date/number formatting. */
  locale: string;
}

// Order shown in the language picker (German first, then English, then the rest).
export const LANGS: LangMeta[] = [
  { code: "de", label: "Deutsch", locale: "de-DE" },
  { code: "en", label: "English", locale: "en-US" },
  { code: "fr", label: "Français", locale: "fr-FR" },
  { code: "es", label: "Español", locale: "es-ES" },
  { code: "it", label: "Italiano", locale: "it-IT" },
  { code: "nl", label: "Nederlands", locale: "nl-NL" },
  { code: "pl", label: "Polski", locale: "pl-PL" },
  { code: "pt", label: "Português", locale: "pt-PT" },
  { code: "sv", label: "Svenska", locale: "sv-SE" },
  { code: "da", label: "Dansk", locale: "da-DK" },
  { code: "cs", label: "Čeština", locale: "cs-CZ" },
  { code: "el", label: "Ελληνικά", locale: "el-GR" },
];

export const LANG_CODES: Lang[] = LANGS.map((l) => l.code);

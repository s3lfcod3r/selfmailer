// Lightweight i18n without dependencies: Context + useLang() hook + localStorage.
// 12 European languages. t(key, params) replaces {placeholders} in the string.
// The dictionaries live in one file per language; en.ts is the canonical key source.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Dict, Lang, TKey } from "./types";
import { LANG_CODES, LANGS } from "./types";
import { en } from "./en";
import { de } from "./de";
import { fr } from "./fr";
import { es } from "./es";
import { it } from "./it";
import { nl } from "./nl";
import { pl } from "./pl";
import { pt } from "./pt";
import { sv } from "./sv";
import { da } from "./da";
import { cs } from "./cs";
import { el } from "./el";

export type { Lang, LangMeta, Dict, TKey } from "./types";
export { LANGS, LANG_CODES } from "./types";

const LANG_KEY = "selfmailer.lang";

const DICTS: Record<Lang, Dict> = { de, en, fr, es, it, nl, pl, pt, sv, da, cs, el };

// Date locale for toLocaleString etc., derived from the language metadata.
export function dateLocale(lang: Lang): string {
  return LANGS.find((l) => l.code === lang)?.locale ?? "en-US";
}

export type TFunc = (key: string, params?: Record<string, string | number>) => string;

function translate(lang: Lang, key: string, params?: Record<string, string | number>): string {
  // Target language → English fallback → raw key, so a missing translation never blanks the UI.
  const raw = DICTS[lang][key as TKey] ?? en[key as TKey] ?? key;
  if (!params) return raw;
  return raw.replace(/\{(\w+)\}/g, (_, name) =>
    name in params ? String(params[name]) : `{${name}}`,
  );
}

type LangContextValue = { lang: Lang; setLang: (l: Lang) => void; t: TFunc };

const LangContext = createContext<LangContextValue | null>(null);

function initialLang(): Lang {
  const stored = localStorage.getItem(LANG_KEY);
  if (stored && (LANG_CODES as string[]).includes(stored)) return stored as Lang;
  const nav = navigator.language?.slice(0, 2).toLowerCase();
  if (nav && (LANG_CODES as string[]).includes(nav)) return nav as Lang;
  return "de";
}

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);

  useEffect(() => {
    localStorage.setItem(LANG_KEY, lang);
    document.documentElement.setAttribute("lang", lang);
  }, [lang]);

  // Kontextwert memoisieren (nur bei Sprachwechsel neu) — sonst bekommt jeder
  // Consumer bei jedem Provider-Render eine neue Objekt-/t-Referenz.
  const value = useMemo<LangContextValue>(
    () => ({ lang, setLang: setLangState, t: (key, params) => translate(lang, key, params) }),
    [lang],
  );
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): LangContextValue {
  const ctx = useContext(LangContext);
  if (!ctx) throw new Error("useLang must be used within LangProvider");
  return ctx;
}

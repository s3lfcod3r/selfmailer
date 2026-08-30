import { useEffect, useRef } from "react";
import DOMPurify from "dompurify";
import { useLang } from "../lib/i18n";
import { promptDialog } from "../lib/dialog";
import { safeLinkUrl } from "../lib/url";

/**
 * Kleiner Rich-Text-Editor (contentEditable + Formatier-Toolbar via
 * execCommand). Liefert HTML über onChange. Wiederverwendbar für Signatur
 * (Konten) und potenziell andere Felder. Initialwert wird einmalig gesetzt,
 * damit der Cursor beim Tippen nicht springt.
 */
const FORMATS: { cmd: string; label: string; title: string }[] = [
  { cmd: "bold", label: "B", title: "Fett" },
  { cmd: "italic", label: "I", title: "Kursiv" },
  { cmd: "underline", label: "U", title: "Unterstrichen" },
  { cmd: "strikeThrough", label: "S", title: "Durchgestrichen" },
  { cmd: "insertUnorderedList", label: "•", title: "Aufzählung" },
  { cmd: "removeFormat", label: "⌫", title: "Format entfernen" },
];

export function RichEditor({
  value, onChange, placeholder, minHeight = 90,
}: {
  value: string;
  onChange: (html: string) => void;
  placeholder?: string;
  minHeight?: number;
}) {
  const { t } = useLang();
  const ref = useRef<HTMLDivElement>(null);

  // Externen Wert sanitisiert übernehmen. Nur setzen, wenn der Editor nicht
  // fokussiert ist und sich der Inhalt unterscheidet, damit der Cursor beim
  // Tippen nicht springt.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const clean = DOMPurify.sanitize(value);
    if (document.activeElement !== el && el.innerHTML !== clean) el.innerHTML = clean;
  }, [value]);

  function exec(cmd: string) {
    document.execCommand(cmd, false);
    ref.current?.focus();
    onChange(ref.current?.innerHTML ?? "");
  }
  async function addLink() {
    const url = safeLinkUrl(await promptDialog(t("compose.linkPrompt")));
    if (url) { document.execCommand("createLink", false, url); onChange(ref.current?.innerHTML ?? ""); }
  }
  // Eingefügtes HTML NIE roh ins DOM lassen (könnte onerror/onmouseover-Handler aus einer
  // bösartigen Quelle enthalten) — abfangen, mit DOMPurify säubern, dann einfügen.
  function onPaste(e: React.ClipboardEvent<HTMLDivElement>) {
    e.preventDefault();
    const html = e.clipboardData.getData("text/html");
    if (html) {
      document.execCommand("insertHTML", false, DOMPurify.sanitize(html));
    } else {
      document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
    }
    onChange(ref.current?.innerHTML ?? "");
  }

  return (
    <div className="rich-editor">
      <div className="compose-toolbar">
        {FORMATS.map((f) => (
          <button key={f.cmd} type="button" className="ghost" title={f.title}
            onMouseDown={(e) => { e.preventDefault(); exec(f.cmd); }}>{f.label}</button>
        ))}
        <button type="button" className="ghost" title={t("compose.link")} aria-label={t("compose.link")}
          onMouseDown={(e) => { e.preventDefault(); addLink(); }}>🔗</button>
      </div>
      <div
        ref={ref}
        className="compose-editor"
        style={{ minHeight }}
        contentEditable
        suppressContentEditableWarning
        data-placeholder={placeholder}
        onPaste={onPaste}
        onInput={() => onChange(ref.current?.innerHTML ?? "")}
      />
    </div>
  );
}

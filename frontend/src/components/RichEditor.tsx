import { useEffect, useRef } from "react";
import { useLang } from "../lib/i18n";
import { promptDialog } from "../lib/dialog";
import { safeLinkUrl } from "../lib/url";

/**
 * Kleiner Rich-Text-Editor (contentEditable + Formatier-Toolbar via
 * execCommand). Liefert HTML ueber onChange. Wiederverwendbar fuer Signatur
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

  // Initialinhalt einmalig setzen (oder wenn extern komplett ersetzt).
  useEffect(() => {
    if (ref.current && ref.current.innerHTML !== value) ref.current.innerHTML = value;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function exec(cmd: string) {
    document.execCommand(cmd, false);
    ref.current?.focus();
    onChange(ref.current?.innerHTML ?? "");
  }
  async function addLink() {
    const url = safeLinkUrl(await promptDialog(t("compose.linkPrompt")));
    if (url) { document.execCommand("createLink", false, url); onChange(ref.current?.innerHTML ?? ""); }
  }

  return (
    <div className="rich-editor">
      <div className="compose-toolbar">
        {FORMATS.map((f) => (
          <button key={f.cmd} type="button" className="ghost" title={f.title}
            onMouseDown={(e) => { e.preventDefault(); exec(f.cmd); }}>{f.label}</button>
        ))}
        <button type="button" className="ghost" title={t("compose.link")}
          onMouseDown={(e) => { e.preventDefault(); addLink(); }}>🔗</button>
      </div>
      <div
        ref={ref}
        className="compose-editor"
        style={{ minHeight }}
        contentEditable
        suppressContentEditableWarning
        data-placeholder={placeholder}
        onInput={() => onChange(ref.current?.innerHTML ?? "")}
      />
    </div>
  );
}

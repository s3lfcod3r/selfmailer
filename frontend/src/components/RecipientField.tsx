import { useEffect, useRef, useState } from "react";
import { api, type Contact } from "../lib/api";

// Eingabefeld für Empfänger (An/Cc/Bcc) mit Autocomplete aus den Kontakten.
// Mehrere Adressen sind komma-/semikolongetrennt; ergänzt wird nur die
// aktuell getippte (letzte) Adresse. Eingefügt wird die REINE E-Mail-Adresse,
// damit der Versand (SendRequest -> EmailStr) gültig bleibt.

type Props = {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  style?: React.CSSProperties;
};

// Zerlegt den Wert in „bereits fertige" Adressen + die aktuell getippte.
function splitCurrent(v: string): { head: string; current: string } {
  const sep = Math.max(v.lastIndexOf(","), v.lastIndexOf(";"));
  const head = sep >= 0 ? v.slice(0, sep + 1) + " " : "";
  const current = v.slice(sep + 1).trim();
  return { head, current };
}

function contactName(c: Contact): string {
  return [c.first_name, c.last_name].filter(Boolean).join(" ") || c.organization || c.email;
}

const SEARCH_DEBOUNCE_MS = 250;

export function RecipientField({ value, onChange, placeholder, autoFocus, style }: Props) {
  const [matches, setMatches] = useState<Contact[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  // Bei Änderung der aktuell getippten Adresse passende Kontakte (mit E-Mail) holen.
  // Die API-Anfrage wird entprellt (wie in Contacts.tsx), damit nicht bei jedem
  // Tastendruck eine /contacts-Anfrage rausgeht; das Cleanup verwirft laufende.
  useEffect(() => {
    const { current } = splitCurrent(value);
    if (current.length < 2) { setMatches([]); setOpen(false); return; }
    let ignore = false;
    const timer = setTimeout(() => {
      api.get<Contact[]>(`/contacts?q=${encodeURIComponent(current)}`)
        .then((list) => {
          if (ignore) return;
          const withMail = list.filter((c) => c.email).slice(0, 6);
          setMatches(withMail);
          setActive(0);
          setOpen(withMail.length > 0);
        })
        .catch(() => { if (!ignore) { setMatches([]); setOpen(false); } });
    }, SEARCH_DEBOUNCE_MS);
    return () => { ignore = true; clearTimeout(timer); };
  }, [value]);

  function pick(c: Contact) {
    const { head } = splitCurrent(value);
    onChange(head + c.email + ", ");
    setMatches([]);
    setOpen(false);
  }

  function onKey(e: React.KeyboardEvent) {
    if (!open || matches.length === 0) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % matches.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a - 1 + matches.length) % matches.length); }
    else if (e.key === "Enter") { e.preventDefault(); pick(matches[active]); }
    else if (e.key === "Escape") { setOpen(false); }
  }

  return (
    <div className="recipient-field" ref={boxRef} style={style}>
      <input
        style={{ width: "100%" }}
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKey}
        onBlur={() => setTimeout(() => setOpen(false), 120)}
        onFocus={() => { if (matches.length) setOpen(true); }}
        autoComplete="off"
      />
      {open && (
        <div className="recipient-suggest">
          {matches.map((c, i) => (
            <button
              key={c.id}
              type="button"
              className={`recipient-item ${i === active ? "active" : ""}`}
              onMouseDown={(e) => { e.preventDefault(); pick(c); }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="recipient-name">{contactName(c)}</span>
              <span className="recipient-mail">{c.email}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

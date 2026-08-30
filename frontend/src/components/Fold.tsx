import { useState, type ReactNode } from "react";

/**
 * Aufklappbarer Abschnitt.
 *
 * Gedacht fuer Seiten, die aus mehreren Einrichtungs-Bloecken bestehen: sichtbar
 * ist erst einmal nur die Ueberschrift, der Inhalt kommt auf Klick. Damit steht
 * nicht mehr jedes Formular der Seite gleichzeitig offen.
 *
 * Bewusst KEIN <details>/<summary>: Safari animiert das anders als Chrome, und
 * wir brauchen den Zustand ohnehin im JSX (z. B. um beim Anlegen automatisch
 * aufzuklappen). Ein Knopf mit aria-expanded ist dasselbe fuer Hilfsmittel —
 * so macht es Notify.tsx seit jeher.
 */
export function Fold({
  title,
  hint,
  defaultOpen = false,
  badge,
  children,
}: {
  title: ReactNode;
  /** Erklaerender Satz unter der Ueberschrift. Nur im offenen Zustand sichtbar. */
  hint?: ReactNode;
  defaultOpen?: boolean;
  /** Kurze Zustandsangabe rechts im Kopf, z. B. eine Anzahl. Immer sichtbar. */
  badge?: ReactNode;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`fold ${open ? "open" : ""}`}>
      <button
        type="button"
        className="fold-head"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="fold-caret" aria-hidden>{open ? "▾" : "▸"}</span>
        <span className="fold-title">{title}</span>
        {badge != null && <span className="fold-badge">{badge}</span>}
      </button>
      {open && (
        <div className="fold-body stack">
          {hint && <p className="muted fold-hint">{hint}</p>}
          {children}
        </div>
      )}
    </section>
  );
}

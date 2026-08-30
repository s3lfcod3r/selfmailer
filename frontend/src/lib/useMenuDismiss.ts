import { useEffect, useRef } from "react";

/**
 * Escape schliesst ein offenes Menue — und NUR das Menue.
 *
 * Ohne das lief Escape bei offenem Menue bis zum globalen Tastaturkuerzel in
 * Mail.tsx durch und schloss die ganze Mail; das Menue verschwand nur als
 * Nebenwirkung, und der Fokus landete auf <body>. Wer mit der Tastatur
 * arbeitet, hat danach keinen Anhaltspunkt mehr.
 *
 * Deshalb zwei Dinge:
 *
 * 1. Der Listener haengt mit `capture: true` an `document`. Der globale
 *    Hotkey-Listener haengt in der Bubble-Phase an `window` und laeuft damit
 *    spaeter — `stopPropagation()` hier verhindert, dass er ueberhaupt feuert.
 *    Ein zweites Escape schliesst dann wie gewohnt die Mail.
 * 2. Beim Oeffnen wird gemerkt, was den Fokus hatte (in aller Regel der Knopf,
 *    der das Menue aufmacht); beim Schliessen geht der Fokus dorthin zurueck.
 *
 * `close` darf ruhig eine Inline-Funktion sein: sie wird in einer Ref gehalten,
 * damit der Effekt nicht bei jedem Rendern neu aufgesetzt wird.
 */
export function useMenuDismiss(open: boolean, close: () => void): void {
  const closeRef = useRef(close);
  closeRef.current = close;
  const triggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    // Beim Oeffnen merken, wohin der Fokus zurueckkehren soll.
    const active = document.activeElement;
    triggerRef.current = active instanceof HTMLElement && active !== document.body ? active : null;

    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      closeRef.current();
      // Der Knopf kann inzwischen aus dem DOM sein (z. B. weil das Menue ihn
      // ersetzt hat) — dann bleibt der Fokus, wo er ist, statt zu springen.
      const back = triggerRef.current;
      if (back && back.isConnected) back.focus();
    }

    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open]);
}

// App-weiter Dialog (Bestaetigung/Eingabe) im eigenen Design statt window.confirm/
// window.prompt. Imperativ aufrufbar aus jeder Komponente:
//   if (await confirmDialog("wirklich?")) { ... }
//   const name = await promptDialog("Name?", "Vorgabe");
// <DialogHost/> wird EINMAL in App.tsx montiert. Ist er (noch) nicht montiert,
// fallen die Funktionen auf die nativen Dialoge zurueck.
import { useEffect, useState } from "react";
import { useLang } from "./i18n";

type ConfirmReq = { kind: "confirm"; message: string; resolve: (v: boolean) => void };
type PromptReq = { kind: "prompt"; message: string; value: string; resolve: (v: string | null) => void };
type Req = ConfirmReq | PromptReq;

let emit: ((r: Req) => void) | null = null;

export function confirmDialog(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (emit) emit({ kind: "confirm", message, resolve });
    else resolve(window.confirm(message));
  });
}

export function promptDialog(message: string, value = ""): Promise<string | null> {
  return new Promise((resolve) => {
    if (emit) emit({ kind: "prompt", message, value, resolve });
    else resolve(window.prompt(message, value));
  });
}

export function DialogHost() {
  const { t } = useLang();
  const [req, setReq] = useState<Req | null>(null);
  const [val, setVal] = useState("");
  useEffect(() => {
    emit = (r) => { setReq(r); if (r.kind === "prompt") setVal(r.value); };
    return () => { emit = null; };
  }, []);
  if (!req) return null;
  const isPrompt = req.kind === "prompt";
  const done = (result: boolean | string | null) => { setReq(null); (req.resolve as (v: unknown) => void)(result); };
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={(e) => { if (e.target === e.currentTarget) done(isPrompt ? null : false); }}
      style={{ position: "fixed", inset: 0, zIndex: 10070, background: "rgba(0,0,0,0.55)", backdropFilter: "blur(2px)", display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}
    >
      <div style={{ width: "min(440px, 100%)", background: "var(--self-bg-2)", border: "1px solid var(--self-line)", borderRadius: 14, boxShadow: "0 12px 40px rgba(0,0,0,0.5)", padding: 20 }}>
        <p style={{ margin: 0, fontSize: 14, color: "var(--self-text)", lineHeight: 1.55, whiteSpace: "pre-wrap" }}>{req.message}</p>
        {isPrompt && (
          <input
            autoFocus
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") done(val); if (e.key === "Escape") done(null); }}
            style={{ width: "100%", marginTop: 12, padding: "8px 12px", borderRadius: 8, border: "1px solid var(--self-line)", background: "var(--self-bg-3)", color: "var(--self-text)", fontSize: 14, boxSizing: "border-box", outline: "none" }}
          />
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
          <button type="button" className="ghost" onClick={() => done(isPrompt ? null : false)}>{t("common.cancel")}</button>
          <button type="button" className="primary" autoFocus={!isPrompt} onClick={() => done(isPrompt ? val : true)}>OK</button>
        </div>
      </div>
    </div>
  );
}

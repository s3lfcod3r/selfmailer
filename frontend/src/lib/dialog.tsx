// App-weiter Dialog (Bestätigung/Eingabe) im eigenen Design statt window.confirm/
// window.prompt. Imperativ aufrufbar aus jeder Komponente:
//   if (await confirmDialog("wirklich?")) { ... }
//   const name = await promptDialog("Name?", "Vorgabe");
// <DialogHost/> wird EINMAL in App.tsx montiert. Ist er (noch) nicht montiert,
// fallen die Funktionen auf die nativen Dialoge zurück.
import { useEffect, useRef, useState } from "react";
import { useLang } from "./i18n";
import { Modal } from "../components/Modal";

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
  const pending = useRef<{ id: number; req: Req }[]>([]);
  const sequence = useRef(0);
  const returnFocus = useRef<Element | null>(null);
  const [active, setActive] = useState<{ id: number; req: Req } | null>(null);
  useEffect(() => {
    const receive = (req: Req) => {
      if (pending.current.length === 0) returnFocus.current = document.activeElement;
      pending.current.push({ id: ++sequence.current, req });
      if (pending.current.length === 1) setActive(pending.current[0]);
    };
    emit = receive;
    return () => {
      if (emit === receive) emit = null;
      for (const { req } of pending.current.splice(0)) {
        if (req.kind === "confirm") req.resolve(false);
        else req.resolve(null);
      }
    };
  }, []);
  if (!active) return null;
  const done = (result: boolean | string | null) => {
    if (pending.current[0]?.id !== active.id) return;
    pending.current.shift();
    setActive(pending.current[0] ?? null);
    (active.req.resolve as (v: unknown) => void)(result);
  };
  return <DialogRequest key={active.id} req={active.req} done={done} returnFocus={returnFocus.current} />;
}

function DialogRequest({ req, done, returnFocus }: { req: Req; done: (v: boolean | string | null) => void; returnFocus: Element | null }) {
  const { t } = useLang();
  const [val, setVal] = useState(req.kind === "prompt" ? req.value : "");
  const isPrompt = req.kind === "prompt";
  return (
    <Modal label={req.message} onClose={() => done(isPrompt ? null : false)} returnFocus={returnFocus}>
      <div style={{ width: "min(440px, 100%)", background: "var(--self-bg-2)", border: "1px solid var(--self-line)", borderRadius: 14, boxShadow: "0 12px 40px rgba(0,0,0,0.5)", padding: 20 }}>
        <p style={{ margin: 0, fontSize: 14, color: "var(--self-text)", lineHeight: 1.55, whiteSpace: "pre-wrap" }}>{req.message}</p>
        {isPrompt && (
          <input
            autoFocus
            data-modal-focus
            aria-label={req.message}
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); done(val); } }}
            style={{ width: "100%", marginTop: 12, padding: "8px 12px", borderRadius: 8, border: "1px solid var(--self-line)", background: "var(--self-bg-3)", color: "var(--self-text)", fontSize: 14, boxSizing: "border-box", outline: "none" }}
          />
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
          <button type="button" className="ghost" data-modal-focus={!isPrompt || undefined} autoFocus={!isPrompt} onClick={() => done(isPrompt ? null : false)}>{t("common.cancel")}</button>
          <button type="button" className="primary" onClick={() => done(isPrompt ? val : true)}>OK</button>
        </div>
      </div>
    </Modal>
  );
}

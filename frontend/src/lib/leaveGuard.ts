import { useCallback, useEffect, useRef } from "react";
import { confirmDialog } from "./dialog";

export type LeaveGuard = () => Promise<boolean>;

/** No persistent draft storage: protect in-memory edits and in-flight writes. */
export function useLeaveGuard(dirty: boolean, busy: boolean, message: string): LeaveGuard {
  const state = useRef({ dirty, busy, message });
  state.current = { dirty, busy, message };
  const asking = useRef(false);
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (!state.current.dirty && !state.current.busy) return;
      e.preventDefault(); e.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);
  return useCallback(async () => {
    if (state.current.busy || asking.current) return false;
    if (!state.current.dirty) return true;
    asking.current = true;
    try { return await confirmDialog(state.current.message); }
    finally { asking.current = false; }
  }, []);
}

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** Native modal dialogs provide focus containment and make the background inert. */
export function Modal({ children, label, onClose, returnFocus }: {
  children: ReactNode; label: string; onClose: () => void; returnFocus?: Element | null;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // Capture before React commits autoFocus on a child input/button.
  const previousFocus = useRef(returnFocus ?? document.activeElement);
  useLayoutEffect(() => {
    const dialog = ref.current!;
    const previous = previousFocus.current;
    dialog.showModal();
    dialog.querySelector<HTMLElement>("[data-modal-focus]")?.focus();
    return () => {
      dialog.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  return createPortal(
    <dialog ref={ref} className="accessible-modal" aria-label={label} aria-modal="true"
      onKeyDown={(e) => e.stopPropagation()}
      onCancel={(e) => { e.preventDefault(); onClose(); }}
      onClick={(e) => {
        if (e.target !== e.currentTarget) return;
        const rect = e.currentTarget.getBoundingClientRect();
        if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) onClose();
      }}>
      {children}
    </dialog>, document.body,
  );
}

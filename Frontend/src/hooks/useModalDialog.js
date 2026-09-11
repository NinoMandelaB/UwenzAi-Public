import { useLayoutEffect, useRef } from "react";

// Keep the existing popup presentation; make surrounding actions inaccessible
// to keyboard/pointer users while it is active, including nested chat dialogs.
export function useModalDialog(onClose) {
  const root = useRef(null);
  const close = useRef(onClose);
  useLayoutEffect(() => {
    close.current = onClose;
  });
  useLayoutEffect(() => {
    const element = root.current;
    if (!element) return;
    const previousFocus = document.activeElement;
    const siblings = [];
    let branch = element;
    while (branch.parentElement && branch !== document.body) {
      for (const sibling of branch.parentElement.children) {
        if (sibling !== branch) {
          siblings.push([sibling, sibling.inert]);
          sibling.inert = true;
        }
      }
      branch = branch.parentElement;
    }
    const focusable = () => [
      ...element.querySelectorAll(
        'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
      ),
    ];
    focusable()[0]?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close.current?.();
      }
      if (event.key !== "Tab") return;
      const nodes = focusable();
      const first = nodes[0];
      const last = nodes.at(-1);
      if (!nodes.length) {
        event.preventDefault();
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          !element.contains(document.activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last ||
          !element.contains(document.activeElement))
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      siblings.forEach(([sibling, inert]) => {
        sibling.inert = inert;
      });
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  return root;
}

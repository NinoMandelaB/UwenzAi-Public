import { useCallback, useLayoutEffect, useRef } from "react";

// Keyed panels isolate drafts. This guard also prevents late async work from
// continuing after that identity's panel has been retired.
export function usePanelLifetime() {
  const active = useRef(false);
  useLayoutEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  return useCallback(async (promise) => {
    const result = await promise;
    if (!active.current) throw new DOMException("Panel closed.", "AbortError");
    return result;
  }, []);
}

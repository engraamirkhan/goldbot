import { useEffect, useState } from "react";

/** True while the viewport is at least `minPx` wide. No matchMedia (tests, old browsers): false, the phone layout. */
export function useWide(minPx = 1024): boolean {
  const q = `(min-width: ${minPx}px)`;
  const mm = typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia : null;
  const [wide, setWide] = useState(() => (mm ? mm(q).matches : false));
  useEffect(() => {
    if (!mm) return;
    const m = mm(q);
    const on = () => setWide(m.matches);
    on();
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, [mm, q]);
  return wide;
}

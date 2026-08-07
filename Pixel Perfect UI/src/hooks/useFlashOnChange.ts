import { useEffect, useRef, useState } from "react";

/**
 * Returns `true` for a brief window right after `value` changes — drives a
 * highlight-flash CSS class so live-polled numbers (LTP, GEX, OI) visibly
 * tick instead of silently swapping. Purely visual; does not affect polling
 * or data flow.
 *
 * Skips the flash on first mount (nothing "changed" yet) and on transitions
 * to/from `null`/`undefined` (loading states), only flashing real value-to-
 * value changes.
 */
export function useFlashOnChange(value: number | string | null | undefined, durationMs = 700): boolean {
  const [flashing, setFlashing] = useState(false);
  const prevRef = useRef(value);
  const mountedRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = value;

    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    if (prev == null || value == null || prev === value) return;

    setFlashing(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setFlashing(false), durationMs);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  return flashing;
}

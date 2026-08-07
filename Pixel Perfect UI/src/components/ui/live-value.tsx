import type { ReactNode } from "react";

import { useFlashOnChange } from "@/hooks/useFlashOnChange";
import { cn } from "@/lib/utils";

type Props = {
  /** Raw comparable value (not the formatted string) — used only to detect ticks. */
  value: number | string | null | undefined;
  /** Already-formatted display content, e.g. `fmt(spot, 2)`. */
  children: ReactNode;
  className?: string;
  flashDurationMs?: number;
};

/**
 * Wraps a formatted live number (LTP, GEX, OI, ...) with tabular-nums/mono
 * alignment and a brief highlight flash when the value changes tick-to-tick.
 * Presentational only — does not fetch or format data itself, and adopting
 * it in a route touches no data-fetching logic, only the render output.
 *
 * Usage:
 *   <LiveValue value={spot}>{fmt(spot, 2)}</LiveValue>
 */
export function LiveValue({ value, children, className, flashDurationMs }: Props) {
  const flashing = useFlashOnChange(value, flashDurationMs);
  return (
    <span
      className={cn(
        "inline-block rounded-sm px-0.5 font-mono tabular-nums",
        flashing && "value-flash",
        className,
      )}
    >
      {children}
    </span>
  );
}

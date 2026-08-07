import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";

import type { CasIndicative } from "@/lib/types";

type CasChipProps = {
  cas?: CasIndicative | null;
  /** Fallback LTP when `cas.spot` is null. */
  spot?: number | null;
};

function formatSigned(n: number, digits: number): string {
  const abs = Math.abs(n).toFixed(digits);
  return n > 0 ? `+${abs}` : n < 0 ? `-${abs}` : abs;
}

/** Compact CAS meta fragment for Gamma / OI Movers — hidden outside the CAS window. */
export function CasChip({ cas, spot }: CasChipProps) {
  if (!cas?.in_cas_window) return null;

  if (cas.indicative == null || !Number.isFinite(cas.indicative)) {
    return (
      <>
        {" · "}
        <Link
          to="/cas-indicative"
          className="font-mono text-muted-foreground underline-offset-2 hover:underline"
          title="Open CAS Indicative desk"
        >
          CAS n/a
        </Link>
      </>
    );
  }

  const ltp =
    cas.spot != null && Number.isFinite(cas.spot)
      ? cas.spot
      : spot != null && Number.isFinite(spot)
        ? spot
        : null;

  let delta: ReactNode = null;
  if (ltp != null && ltp !== 0) {
    const pts = cas.indicative - ltp;
    const pct = (pts / ltp) * 100;
    delta = (
      <span className="text-muted-foreground">
        {" "}
        ({formatSigned(pts, 2)} pts, {formatSigned(pct, 2)}%)
      </span>
    );
  }

  return (
    <>
      {" · "}
      <Link
        to="/cas-indicative"
        className="font-mono font-semibold text-foreground underline-offset-2 hover:underline"
        title="Open CAS Indicative desk"
      >
        CAS {cas.indicative.toFixed(2)}
      </Link>
      {delta}
    </>
  );
}

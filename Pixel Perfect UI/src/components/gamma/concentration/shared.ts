import type { GammaConcentrationBand, GammaStrikeRow } from "@/lib/types";

/** Call / CE — red. Put / PE — green. Same mapping as the rest of the gamma desk. */
export const CE_COLOR = "#ef4444";
export const PE_COLOR = "#22c55e";
export const MIXED_COLOR = "#94a3b8";

/** Dealer-gamma sign colours for the ladder and the peak markers. */
export const POS_GAMMA = "#14b8a6";
export const NEG_GAMMA = "#f43f5e";

export const SPOT_LINE = "#0891b2";
export const PIN_LINE = "#ca8a04";
export const CLIFF_LINE = "#e11d48";

/** Desk vocabulary. The API sends `band_label`; this is the client-side fallback. */
export const BAND_LABEL: Record<GammaConcentrationBand, string> = {
  concentrated: "compressed",
  mixed: "balanced",
  diffuse: "dispersed",
};

export function bandLabel(
  band: GammaConcentrationBand | null | undefined,
  fromApi?: string | null,
): string {
  if (fromApi) return fromApi;
  return band ? BAND_LABEL[band] : "—";
}

export function bandTone(band: GammaConcentrationBand | null | undefined): string {
  if (band === "concentrated") {
    return "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200";
  }
  if (band === "diffuse") {
    return "border-sky-500/40 bg-sky-500/10 text-sky-800 dark:text-sky-200";
  }
  if (band === "mixed") {
    return "border-slate-400/40 bg-slate-500/10 text-slate-700 dark:text-slate-200";
  }
  return "border-border bg-muted text-muted-foreground";
}

/** Chart/SVG fills — large areas, so the single hex reads in both themes. */
export function bandColor(band: GammaConcentrationBand | null | undefined): string {
  if (band === "concentrated") return "#f59e0b";
  if (band === "diffuse") return "#0ea5e9";
  if (band === "mixed") return "#94a3b8";
  return MIXED_COLOR;
}

/**
 * Text colour for the band. Uses per-theme shades rather than one hex — the
 * chart amber is far too light to read as type on a light card.
 */
export function bandTextClass(band: GammaConcentrationBand | null | undefined): string {
  if (band === "concentrated") return "text-amber-600 dark:text-amber-400";
  if (band === "diffuse") return "text-sky-600 dark:text-sky-400";
  if (band === "mixed") return "text-slate-500 dark:text-slate-300";
  return "text-muted-foreground";
}

export function sideBiasColor(bias: string | null | undefined): string {
  if (bias === "call") return CE_COLOR;
  if (bias === "put") return PE_COLOR;
  return MIXED_COLOR;
}

export function fmt(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

/** English ordinal for rank display (81 → 81st). */
export function ordinal(n: number): string {
  const v = Math.round(Math.abs(n));
  const mod100 = v % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${v}th`;
  switch (v % 10) {
    case 1:
      return `${v}st`;
    case 2:
      return `${v}nd`;
    case 3:
      return `${v}rd`;
    default:
      return `${v}th`;
  }
}

/** ₹ crore, stepped into Indian units so a 12-digit GEX stays readable. */
export function gexIndian(inr: number | null | undefined): string {
  if (inr == null || !Number.isFinite(inr)) return "—";
  const cr = inr / 1e7;
  const sign = cr < 0 ? "−" : "+";
  const abs = Math.abs(cr);
  if (abs >= 1e5) return `${sign}${(abs / 1e5).toFixed(2)}L Cr`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(2)}K Cr`;
  return `${sign}${abs.toFixed(2)} Cr`;
}

export function gexCrore(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v / 1e7).toFixed(2);
}

/** Contract counts run to tens of millions — show M/K, not raw digits. */
export function compactOi(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : "+";
  const abs = Math.abs(v);
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(digits)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(digits)}K`;
  return `${sign}${abs.toFixed(0)}`;
}

export function signedPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return `${sign}${Math.abs(v).toFixed(digits)}%`;
}

export function formatDayMon(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }).toUpperCase();
}

export function dteLabel(dte: number | null | undefined): string | null {
  if (dte == null || !Number.isFinite(dte)) return null;
  if (dte <= 0) return "0 DTE";
  return `${dte} DTE`;
}

/** Dominant CE/PE mass at a strike — same 5% threshold as the backend. */
export function strikeSideBias(
  r: Pick<GammaStrikeRow, "ce_gex" | "pe_gex" | "net_gex">,
): "call" | "put" | "mixed" {
  const ce = Math.abs(Number(r.ce_gex ?? 0) || 0);
  const pe = Math.abs(Number(r.pe_gex ?? 0) || 0);
  if (ce > pe * 1.05) return "call";
  if (pe > ce * 1.05) return "put";
  const net = Number(r.net_gex ?? 0) || 0;
  if (ce === 0 && pe === 0) {
    if (net > 0) return "put";
    if (net < 0) return "call";
  }
  return "mixed";
}

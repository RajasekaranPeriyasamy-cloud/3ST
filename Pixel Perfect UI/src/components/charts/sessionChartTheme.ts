/**
 * Straddle Watch–aligned light theme for Plotly session charts.
 * Charts stay light even when the app shell is `.dark`.
 */
export const SESSION_CHART = {
  paperBg: "#ffffff",
  plotBg: "#ffffff",
  grid: "#eef1f4",
  axis: "#666666",
  zeroline: "#cbd5e1",
  fontFamily: "Segoe UI, Helvetica, Arial, sans-serif",
  annotation: "#222222",
  hoverBg: "rgba(255, 255, 255, 0.96)",
  hoverBorder: "#e2e8f0",
  hoverText: "#334155",
} as const;

/** Outer card — matches StraddleWatchChart shell. */
export const SESSION_SHELL =
  "relative overflow-hidden rounded-sm border border-slate-200 bg-white text-slate-800 shadow-sm";

/** Plot inset — light border, no dark terminal hole. */
export const SESSION_PLOT_INSET =
  "relative mx-3 mb-3 overflow-visible rounded-sm border border-slate-200 bg-white";

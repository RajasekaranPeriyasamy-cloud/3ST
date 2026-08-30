/**
 * Plotly palette for the session charts (GEX session, OI movers).
 *
 * These used to be pinned light — "charts stay light even when the app shell is
 * `.dark`" — which left a white 380px rectangle burning in the middle of an
 * otherwise dark desk. They now follow the app theme; call
 * `sessionChartTheme(isDark)` inside the layout memo and put `isDark` in its
 * deps so a toggle repaints the chart.
 *
 * The dark surfaces are blue-black (#171b22 / #1b2029), never #000: pure black
 * behind the emerald/red series is the worst case for halation.
 */
export type SessionChartTheme = {
  paperBg: string;
  plotBg: string;
  grid: string;
  axis: string;
  zeroline: string;
  fontFamily: string;
  annotation: string;
  hoverBg: string;
  hoverBorder: string;
  hoverText: string;
};

const FONT_FAMILY = "Segoe UI, Helvetica, Arial, sans-serif";

const LIGHT: SessionChartTheme = {
  paperBg: "#ffffff",
  plotBg: "#ffffff",
  grid: "#eef1f4",
  axis: "#666666",
  zeroline: "#cbd5e1",
  fontFamily: FONT_FAMILY,
  annotation: "#222222",
  hoverBg: "rgba(255, 255, 255, 0.96)",
  hoverBorder: "#e2e8f0",
  hoverText: "#334155",
};

const DARK: SessionChartTheme = {
  paperBg: "#1b2029",
  plotBg: "#171b22",
  grid: "rgba(148, 163, 184, 0.16)",
  axis: "rgba(212, 220, 232, 0.78)",
  zeroline: "rgba(148, 163, 184, 0.42)",
  fontFamily: FONT_FAMILY,
  annotation: "#e6ebf2",
  hoverBg: "rgba(30, 36, 46, 0.96)",
  hoverBorder: "rgba(148, 163, 184, 0.4)",
  hoverText: "#dbe2ec",
};

export function sessionChartTheme(isDark: boolean): SessionChartTheme {
  return isDark ? DARK : LIGHT;
}

/** Light palette. Kept for module-scope constants that never re-render. */
export const SESSION_CHART = LIGHT;

/** Outer card — matches StraddleWatchChart shell. */
export const SESSION_SHELL =
  "relative overflow-hidden rounded-sm border border-border bg-card text-foreground shadow-sm";

/** Plot inset — follows the card surface, no dark terminal hole in light mode. */
export const SESSION_PLOT_INSET =
  "relative mx-3 mb-3 overflow-visible rounded-sm border border-border bg-card";

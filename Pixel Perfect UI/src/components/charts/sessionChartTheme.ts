/**
 * Session chart shell classes — follow the app's design tokens (see
 * `styles.css`) so session charts match the light/dark theme of the shell
 * around them instead of a fixed light-only palette.
 *
 * Per-library colors (Plotly layout, Highcharts theme options, Recharts
 * config) come from `useChartTheme()` in `@/hooks/useChartTheme`, which
 * reads the same CSS custom properties live and reacts to theme toggles.
 */

/** Outer card. */
export const SESSION_SHELL =
  "relative overflow-hidden rounded-sm border border-border bg-card text-card-foreground shadow-sm";

/** Plot inset. */
export const SESSION_PLOT_INSET =
  "relative mx-3 mb-3 overflow-visible rounded-sm border border-border bg-card";

import { useEffect, useState } from "react";

/**
 * Chart palette resolved live from the app's CSS custom properties (see
 * `styles.css`). One source of truth for every chart library — Plotly,
 * Highcharts, Recharts — so a chart never looks like it belongs to a
 * different app than the shell around it, in either theme.
 *
 * Reactive to the `.dark` class on `<html>` via a MutationObserver, so it
 * updates immediately when the user flips `ThemeToggle` — independent of
 * `useTheme()`'s local state, which only tracks whichever single component
 * called it.
 */
export type ChartPalette = {
  /** Card/plot background. */
  paperBg: string;
  plotBg: string;
  /** Gridlines / axis line color. */
  grid: string;
  /** Axis tick + title text color. */
  axis: string;
  zeroline: string;
  /** Border color, for chart-adjacent shell chrome. */
  border: string;
  fontFamily: string;
  monoFontFamily: string;
  /** Primary readable text (annotations, in-chart labels). */
  annotation: string;
  hoverBg: string;
  hoverBorder: string;
  hoverText: string;
  bull: string;
  bear: string;
  warn: string;
  primary: string;
  chart1: string;
  chart2: string;
  chart3: string;
  chart4: string;
  chart5: string;
};

/** Matches the previous hardcoded light-only theme — used before hydration and as a safety net. */
const FALLBACK: ChartPalette = {
  paperBg: "#ffffff",
  plotBg: "#ffffff",
  grid: "#eef1f4",
  axis: "#666666",
  zeroline: "#cbd5e1",
  border: "#e2e8f0",
  fontFamily: '"DM Sans", "Segoe UI", ui-sans-serif, system-ui, sans-serif',
  monoFontFamily: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace',
  annotation: "#222222",
  hoverBg: "rgba(255, 255, 255, 0.96)",
  hoverBorder: "#e2e8f0",
  hoverText: "#334155",
  bull: "#22c55e",
  bear: "#ef4444",
  warn: "#ca8a04",
  primary: "#0f766e",
  chart1: "#22c55e",
  chart2: "#ef4444",
  chart3: "#0f766e",
  chart4: "#ca8a04",
  chart5: "#a855f7",
};

function readPalette(): ChartPalette {
  if (typeof document === "undefined") return FALLBACK;
  const style = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => {
    const raw = style.getPropertyValue(name).trim();
    return raw || fallback;
  };
  return {
    paperBg: v("--card", FALLBACK.paperBg),
    plotBg: v("--card", FALLBACK.plotBg),
    grid: v("--border", FALLBACK.grid),
    axis: v("--muted-foreground", FALLBACK.axis),
    zeroline: v("--border", FALLBACK.zeroline),
    border: v("--border", FALLBACK.border),
    fontFamily: v("--font-sans", FALLBACK.fontFamily),
    monoFontFamily: v("--font-mono", FALLBACK.monoFontFamily),
    annotation: v("--foreground", FALLBACK.annotation),
    hoverBg: v("--popover", FALLBACK.hoverBg),
    hoverBorder: v("--border", FALLBACK.hoverBorder),
    hoverText: v("--popover-foreground", FALLBACK.hoverText),
    bull: v("--bull", FALLBACK.bull),
    bear: v("--bear", FALLBACK.bear),
    warn: v("--warn", FALLBACK.warn),
    primary: v("--primary", FALLBACK.primary),
    chart1: v("--chart-1", FALLBACK.chart1),
    chart2: v("--chart-2", FALLBACK.chart2),
    chart3: v("--chart-3", FALLBACK.chart3),
    chart4: v("--chart-4", FALLBACK.chart4),
    chart5: v("--chart-5", FALLBACK.chart5),
  };
}

/** Live chart palette that follows the app's light/dark theme. */
export function useChartTheme(): ChartPalette {
  const [palette, setPalette] = useState<ChartPalette>(FALLBACK);

  useEffect(() => {
    setPalette(readPalette());
    if (typeof document === "undefined") return;
    const target = document.documentElement;
    const observer = new MutationObserver(() => setPalette(readPalette()));
    observer.observe(target, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return palette;
}

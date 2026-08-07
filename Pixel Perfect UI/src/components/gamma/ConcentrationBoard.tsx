import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import type {
  GammaConcentrationBand,
  GammaConcentrationSummary,
  GammaConcentrationSummaryItem,
  GammaSnapshot,
  GammaStrikeRow,
  GammaTopContributor,
  OiUnderlying,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const CE_COLOR = "#ef4444"; // Call / CE — red
const PE_COLOR = "#22c55e"; // Put / PE — green
const MIXED_COLOR = "#94a3b8";
const SPOT_LINE = "#0891b2";
const PIN_LINE = "#ca8a04";
const CLIFF_LINE = "#e11d48";

function sideBiasColor(bias: string | null | undefined): string {
  if (bias === "call") return CE_COLOR;
  if (bias === "put") return PE_COLOR;
  return MIXED_COLOR;
}

/** Dominant CE/PE mass at a strike (same threshold as contributorsFromStrikes). */
function strikeSideBias(r: Pick<GammaStrikeRow, "ce_gex" | "pe_gex" | "net_gex">): "call" | "put" | "mixed" {
  const ce = Math.abs(Number(r.ce_gex ?? 0) || 0);
  const pe = Math.abs(Number(r.pe_gex ?? 0) || 0);
  if (ce > pe * 1.05) return "call";
  if (pe > ce * 1.05) return "put";
  // Fall back to signed net when side GEX missing/equal
  const net = Number(r.net_gex ?? 0) || 0;
  if (ce === 0 && pe === 0) {
    if (net > 0) return "put";
    if (net < 0) return "call";
  }
  return "mixed";
}

const DEFAULT_STRIP: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];
const STRIP_POLL_MS = 90_000;
const HHI_LINE = "#0f766e";
const HHI_FLIP_DOT = "#d97706";
const MAX_FLIP_LIST = 4;

const BAND_LABEL: Record<GammaConcentrationBand, string> = {
  concentrated: "concentrated",
  mixed: "balanced",
  diffuse: "dispersed",
};

type HhiSparkPoint = {
  ms: number;
  label: string;
  hhi: number;
  flipHhi: number | null;
};

type HhiFlip = {
  ms: number;
  label: string;
  dir: "above" | "below";
  hhi: number;
};

function fmt(v: number | null | undefined, digits = 0): string {
  if (v == null) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** English ordinal for rank display (81 → 81st). */
function ordinal(n: number): string {
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

function historyToMs(t: string | null | undefined, tsMs?: number | null): number | null {
  if (tsMs != null && Number.isFinite(tsMs)) return Number(tsMs);
  if (!t) return null;
  const ms = new Date(t).getTime();
  return Number.isFinite(ms) ? ms : null;
}

function formatHhMm(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
}

/** Sign of HHI vs baseline: +1 above, -1 below, 0 on the line. */
function sideVsMean(hhi: number, mean: number): -1 | 0 | 1 {
  if (hhi > mean + 1e-12) return 1;
  if (hhi < mean - 1e-12) return -1;
  return 0;
}

function detectHhiFlips(points: { ms: number; label: string; hhi: number }[], mean: number): HhiFlip[] {
  const out: HhiFlip[] = [];
  let prevSide: -1 | 1 | null = null;
  for (const p of points) {
    const side = sideVsMean(p.hhi, mean);
    if (prevSide != null && side !== 0 && side !== prevSide) {
      out.push({
        ms: p.ms,
        label: p.label,
        dir: side === 1 ? "above" : "below",
        hhi: p.hhi,
      });
    }
    if (side !== 0) prevSide = side;
  }
  return out;
}

function gexCrore(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v / 1e7).toFixed(2);
}

function bandTone(band: GammaConcentrationBand | null | undefined): string {
  if (band === "concentrated") return "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200";
  if (band === "diffuse") return "border-sky-500/40 bg-sky-500/10 text-sky-800 dark:text-sky-200";
  if (band === "mixed") return "border-slate-400/40 bg-slate-500/10 text-slate-700 dark:text-slate-200";
  return "border-border bg-muted text-muted-foreground";
}

/** HHI of absolute shares for one side; mirrors backend fallbacks. */
function sideHhiFromStrikes(strikes: GammaStrikeRow[], side: "call" | "put"): number | null {
  const gexKey = side === "call" ? "ce_gex" : "pe_gex";
  const densKey = side === "call" ? "ce_density" : "pe_density";
  let masses = strikes.map((r) => Math.abs(Number(r[gexKey] ?? 0) || 0));
  let total = masses.reduce((a, b) => a + b, 0);
  if (total <= 0) {
    masses = strikes.map((r) => Math.abs(Number(r[densKey] ?? 0) || 0));
    total = masses.reduce((a, b) => a + b, 0);
  }
  if (total <= 0) {
    masses = strikes.map((r) => {
      const net = Number(r.net_gex ?? 0) || 0;
      return side === "call" ? Math.max(net, 0) : Math.abs(Math.min(net, 0));
    });
    total = masses.reduce((a, b) => a + b, 0);
  }
  if (total <= 0) return null;
  return Math.round(masses.reduce((s, m) => s + (m / total) ** 2, 0) * 10000) / 10000;
}

function contributorsFromStrikes(strikes: GammaStrikeRow[]): GammaTopContributor[] {
  const rows = strikes.map((r) => {
    const net = Number(r.net_gex ?? 0) || 0;
    const dens = Math.abs(Number(r.total_density ?? 0) || 0);
    const mass = Math.abs(net) > 0 ? Math.abs(net) : dens;
    const ce = Math.abs(Number(r.ce_gex ?? 0) || 0);
    const pe = Math.abs(Number(r.pe_gex ?? 0) || 0);
    let side_bias: GammaTopContributor["side_bias"] = "mixed";
    if (ce > pe * 1.05) side_bias = "call";
    else if (pe > ce * 1.05) side_bias = "put";
    return { strike: r.strike, mass, net_gex: net, side_bias };
  });
  const total = rows.reduce((s, r) => s + r.mass, 0);
  if (total <= 0) return [];
  return rows
    .map((r) => ({
      strike: r.strike,
      share: Math.round((r.mass / total) * 10000) / 10000,
      net_gex: r.net_gex,
      side_bias: r.side_bias,
    }))
    .sort((a, b) => b.share - a.share)
    .slice(0, 25);
}

const TOP_N_OPTIONS = [5, 10, 15, 20, 25] as const;
type TopNOption = (typeof TOP_N_OPTIONS)[number];

function cliffFromSnapshot(snap: GammaSnapshot): number | null {
  const concCliff = snap.concentration?.cliff_strike;
  if (concCliff != null && Number.isFinite(concCliff)) return concCliff;
  const strikes = snap.strikes ?? [];
  if (!strikes.length) return null;
  const lo = Math.min(...strikes.map((r) => r.strike));
  const hi = Math.max(...strikes.map((r) => r.strike));
  const flip = snap.flip_level;
  if (flip != null && flip >= lo && flip <= hi) return flip;
  const spot = snap.spot;
  const candidates: number[] = [];
  if (snap.call_wall != null && snap.call_wall >= spot) candidates.push(snap.call_wall);
  if (snap.put_wall != null && snap.put_wall <= spot) candidates.push(snap.put_wall);
  if (!candidates.length) {
    if (snap.call_wall != null) candidates.push(snap.call_wall);
    if (snap.put_wall != null) candidates.push(snap.put_wall);
  }
  if (!candidates.length) return null;
  return candidates.reduce((best, w) =>
    Math.abs(w - spot) > Math.abs(best - spot) ? w : best,
  );
}

function Chip({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-border/70 bg-background/60 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="font-mono text-sm font-semibold tabular-nums">{value}</p>
      {hint ? <p className="text-[10px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function HhiGauge({ hhi, band }: { hhi: number | null; band: GammaConcentrationBand | null }) {
  const pct = hhi == null ? 0 : Math.min(100, Math.max(0, (hhi / 0.5) * 100));
  const label = band ? BAND_LABEL[band] : "—";
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-2">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">HHI</p>
          <p className="font-mono text-3xl font-semibold tabular-nums tracking-tight">
            {hhi != null ? hhi.toFixed(2) : "—"}
          </p>
        </div>
        <Badge variant="outline" className={bandTone(band)}>
          {label}
        </Badge>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[10px] text-muted-foreground">
        concentrated ≥0.25 · balanced ≥0.12 · dispersed below
      </p>
    </div>
  );
}

function quadrantTone(quadrant: string | null | undefined): string {
  if (!quadrant) return "border-border bg-muted text-muted-foreground";
  if (quadrant.startsWith("unequal")) {
    return "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200";
  }
  return "border-sky-500/40 bg-sky-500/10 text-sky-800 dark:text-sky-200";
}

function GiniPanel({
  gini,
  quadrant,
}: {
  gini: number | null | undefined;
  quadrant: string | null | undefined;
}) {
  return (
    <div className="flex flex-col gap-2 border-t border-border/60 pt-3">
      <div className="flex items-end justify-between gap-2">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">Gini</p>
          <p className="font-mono text-2xl font-semibold tabular-nums tracking-tight">
            {gini != null ? gini.toFixed(2) : "—"}
          </p>
        </div>
        {quadrant ? (
          <Badge variant="outline" className={quadrantTone(quadrant)}>
            {quadrant}
          </Badge>
        ) : null}
      </div>
      <p className="text-[10px] text-muted-foreground">
        inequality of strike |GEX|, not concentration
      </p>
    </div>
  );
}

function stripBandLabel(band: GammaConcentrationBand | null | undefined): string {
  if (!band) return "—";
  return BAND_LABEL[band];
}

function emptyStripItem(u: OiUnderlying): GammaConcentrationSummaryItem {
  return {
    underlying: u,
    expiry: null,
    spot: null,
    hhi: null,
    band: null,
    pin_strike: null,
    cliff_strike: null,
  };
}

function IndexConcentrationStrip({
  selected,
  onSelect,
  refreshToken,
}: {
  selected: OiUnderlying;
  onSelect?: (u: OiUnderlying) => void;
  refreshToken?: number;
}) {
  const [items, setItems] = useState<GammaConcentrationSummaryItem[]>([]);
  const [loading, setLoading] = useState(false);

  // Polling updates chip metrics only — never calls onSelect (user click only).
  const fetchSummary = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<GammaConcentrationSummary>(
        "/gamma-density/concentration-summary",
        { silent: true },
      );
      setItems(data.items ?? []);
    } catch {
      // Keep last successful strip; main board still has the selected snap.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchSummary();
  }, [fetchSummary, refreshToken]);

  useEffect(() => {
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSummary();
    }, STRIP_POLL_MS);
    return () => clearInterval(id);
  }, [fetchSummary]);

  const displayItems = useMemo(() => {
    const base = items.length
      ? items
      : DEFAULT_STRIP.map((u) => emptyStripItem(u));
    const sel = String(selected || "").toUpperCase() as OiUnderlying;
    if (!sel) return base;
    // Cash strip omits MCX — keep the user's chip visible/selected (e.g. CRUDEOIL).
    if (base.some((i) => String(i.underlying).toUpperCase() === sel)) return base;
    return [emptyStripItem(sel), ...base];
  }, [items, selected]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      {displayItems.map((item) => {
        const u = String(item.underlying).toUpperCase() as OiUnderlying;
        const active = u === selected;
        const hhiTxt = item.hhi != null ? item.hhi.toFixed(2) : "—";
        const bandTxt = stripBandLabel(item.band);
        const stale = item.source === "history" || item.source === "error";
        return (
          <button
            key={u}
            type="button"
            disabled={!onSelect}
            onClick={() => {
              // Explicit user gesture only — strip poll / refresh must not reach here.
              if (!onSelect || u === selected) return;
              onSelect(u);
            }}
            title={
              item.error
                ? `${u}: ${item.error}`
                : item.expiry
                  ? `${u} · ${item.expiry}${stale ? " · cached" : ""}`
                  : u
            }
            className={`min-w-[8.5rem] rounded-md border px-3 py-2 text-left transition-colors disabled:cursor-default ${
              active
                ? "border-primary/50 bg-primary/10 ring-1 ring-primary/30"
                : "border-border/70 bg-background/60 hover:border-primary/30 hover:bg-muted/40"
            } ${stale && !active ? "opacity-80" : ""}`}
          >
            <p className="font-mono text-sm font-semibold tabular-nums tracking-tight">
              {u}{" "}
              <span className="text-foreground/90">{hhiTxt}</span>
              <span className="ml-1 text-[10px] font-sans font-normal text-muted-foreground">
                {bandTxt}
              </span>
            </p>
            {stale && item.hhi == null ? (
              <p className="text-[10px] text-muted-foreground">
                {loading ? "loading…" : "unavailable"}
              </p>
            ) : null}
          </button>
        );
      })}
      {loading && items.length > 0 ? (
        <span className="text-[10px] text-muted-foreground">updating…</span>
      ) : null}
    </div>
  );
}

export function ConcentrationBoard({
  snap,
  selectedUnderlying,
  onSelectUnderlying,
  summaryRefreshToken,
}: {
  snap: GammaSnapshot;
  selectedUnderlying?: OiUnderlying;
  onSelectUnderlying?: (u: OiUnderlying) => void;
  /** Bump (e.g. parent Refresh) to re-fetch the multi-index strip. */
  summaryRefreshToken?: number;
}) {
  const conc = snap.concentration;
  const [topN, setTopN] = useState<TopNOption>(5);
  const activeUnderlying = selectedUnderlying ?? snap.underlying;

  const allContributors = useMemo(() => {
    const fromApi = conc?.top_contributors;
    if (fromApi && fromApi.length > 0) return fromApi;
    return contributorsFromStrikes(snap.strikes ?? []);
  }, [conc?.top_contributors, snap.strikes]);

  const contributors = useMemo(
    () => allContributors.slice(0, topN),
    [allContributors, topN],
  );

  const callHhi = useMemo(() => {
    if (conc?.call_hhi != null) return conc.call_hhi;
    return sideHhiFromStrikes(snap.strikes ?? [], "call");
  }, [conc?.call_hhi, snap.strikes]);

  const putHhi = useMemo(() => {
    if (conc?.put_hhi != null) return conc.put_hhi;
    return sideHhiFromStrikes(snap.strikes ?? [], "put");
  }, [conc?.put_hhi, snap.strikes]);

  const cliffStrike = useMemo(() => cliffFromSnapshot(snap), [snap]);

  const densityRows = useMemo(() => {
    return [...(snap.strikes ?? [])]
      .map((r) => ({
        strike: r.strike,
        net_gex: r.net_gex,
        abs_gex: Math.abs(r.net_gex || 0),
        side_bias: strikeSideBias(r),
      }))
      .sort((a, b) => b.strike - a.strike);
  }, [snap.strikes]);

  const maxAbs = useMemo(
    () => Math.max(1, ...densityRows.map((r) => r.abs_gex)),
    [densityRows],
  );

  const sparkData = useMemo((): HhiSparkPoint[] => {
    const rows = (snap.history ?? [])
      .filter((p) => p.hhi != null)
      .map((p) => {
        const ms = historyToMs(p.t, p.ts_ms);
        if (ms == null) return null;
        return {
          ms,
          label: formatHhMm(ms),
          hhi: Number(p.hhi),
          flipHhi: null as number | null,
        };
      })
      .filter((p): p is HhiSparkPoint => p != null)
      .sort((a, b) => a.ms - b.ms);
    return rows;
  }, [snap.history]);

  const intradayPct = useMemo(() => {
    if (conc?.hhi_percentile_intraday != null) return conc.hhi_percentile_intraday;
    if (conc?.hhi == null || sparkData.length === 0) return null;
    const cur = conc.hhi;
    const nLe = sparkData.filter((p) => p.hhi <= cur + 1e-12).length;
    return Math.round((1000 * nLe) / sparkData.length) / 10;
  }, [conc?.hhi, conc?.hhi_percentile_intraday, sparkData]);

  const sessionMean = useMemo(() => {
    if (conc?.hhi_session_mean != null) return conc.hhi_session_mean;
    if (!sparkData.length) return null;
    return sparkData.reduce((s, p) => s + p.hhi, 0) / sparkData.length;
  }, [conc?.hhi_session_mean, sparkData]);

  const hhiFlips = useMemo(() => {
    if (sessionMean == null || sparkData.length < 2) return [] as HhiFlip[];
    return detectHhiFlips(sparkData, sessionMean);
  }, [sparkData, sessionMean]);

  const sparkChartData = useMemo(() => {
    if (!hhiFlips.length) return sparkData;
    const flipMs = new Set(hhiFlips.map((f) => f.ms));
    return sparkData.map((p) => ({
      ...p,
      flipHhi: flipMs.has(p.ms) ? p.hhi : null,
    }));
  }, [sparkData, hhiFlips]);

  const recentFlips = useMemo(
    () => hhiFlips.slice(-MAX_FLIP_LIST),
    [hhiFlips],
  );

  return (
    <div className="flex flex-col gap-4">
      <IndexConcentrationStrip
        selected={activeUnderlying}
        onSelect={onSelectUnderlying}
        refreshToken={summaryRefreshToken}
      />

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Chip
          label="Net GEX"
          value={`${gexCrore(snap.total_gex)} ₹Cr`}
          hint={snap.gamma_regime === "positive" ? "positive γ" : "negative γ"}
        />
        <Chip label="Spot" value={fmt(snap.spot, 2)} hint={`ATM ${fmt(snap.atm_strike)}`} />
        <Chip
          label="Pin"
          value={fmt(conc?.pin_strike)}
          hint={
            conc?.pin_share != null
              ? `${(conc.pin_share * 100).toFixed(0)}% share${
                  conc.pin_stable === true ? " · stable" : conc.pin_stable === false ? " · moving" : ""
                }`
              : undefined
          }
        />
        <Chip
          label="Cliff"
          value={fmt(cliffStrike)}
          hint="Flip if in window · else breakout-side wall (not a second pin)"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        {/* Left: HHI + narrative */}
        <Card className="lg:col-span-3">
          <CardHeader className="py-3">
            <CardTitle className="text-sm">Concentration</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <HhiGauge hhi={conc?.hhi ?? null} band={conc?.band ?? null} />
            {conc?.hhi_percentile_30d != null ? (
              <p className="font-mono text-sm tabular-nums text-foreground">
                {ordinal(conc.hhi_percentile_30d)}
                <span className="ml-1 text-[11px] font-sans font-normal text-muted-foreground">
                  · last 30 days
                </span>
              </p>
            ) : null}
            <GiniPanel gini={conc?.gini} quadrant={conc?.shape_quadrant} />
            <div className="space-y-1.5 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">
                {snap.market_read?.regime_line ?? `${snap.gamma_regime} gamma`}
              </p>
              <p>{snap.market_read?.shape_line ?? "—"}</p>
              <p>{snap.market_read?.vol_line ?? "—"}</p>
              <p className="text-[10px]">
                Top1 {(conc?.top1_share != null ? `${(conc.top1_share * 100).toFixed(0)}%` : "—")}
                {" · "}
                Eff strikes {conc?.effective_strikes ?? "—"}
              </p>
            </div>
          </CardContent>
        </Card>

        {/* Center: strike density */}
        <Card className="lg:col-span-5">
          <CardHeader className="py-3">
            <CardTitle className="text-sm">Strike density (|GEX|)</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-2 flex flex-wrap gap-3 text-[15px] font-bold text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CE_COLOR }} /> CE
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: PE_COLOR }} /> PE
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-3" style={{ background: SPOT_LINE }} /> Spot
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-3" style={{ background: PIN_LINE }} /> Pin
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-0.5 w-3" style={{ background: CLIFF_LINE }} /> Cliff
              </span>
            </div>
            <div className="max-h-[420px] space-y-0.5 overflow-y-auto pr-1">
              {densityRows.map((r) => {
                const w = (r.abs_gex / maxAbs) * 100;
                const isSpot = Math.abs(r.strike - snap.spot) < 1e-6;
                const isPin = conc?.pin_strike != null && Math.abs(r.strike - conc.pin_strike) < 1e-6;
                const isCliff =
                  cliffStrike != null && Math.abs(r.strike - cliffStrike) < 1e-6;
                return (
                  <div key={r.strike} className="relative flex items-center gap-2 text-[11px] font-mono">
                    <span
                      className={`w-14 shrink-0 text-right tabular-nums ${
                        isPin || isCliff ? "font-semibold text-foreground" : "text-muted-foreground"
                      }`}
                    >
                      {fmt(r.strike)}
                    </span>
                    <div className="relative h-3 flex-1 rounded-sm bg-muted/50">
                      <div
                        className="absolute inset-y-0 left-0 rounded-sm"
                        style={{
                          width: `${w}%`,
                          background: sideBiasColor(r.side_bias),
                          opacity: 0.85,
                        }}
                      />
                      {isSpot ? (
                        <div
                          className="absolute inset-y-0 w-0.5"
                          style={{ left: "0%", background: SPOT_LINE, boxShadow: `0 0 0 1px ${SPOT_LINE}` }}
                          title="Spot"
                        />
                      ) : null}
                    </div>
                    <span className="w-10 shrink-0 text-right text-[9px] text-muted-foreground">
                      {isPin ? "PIN" : isCliff ? "CLF" : ""}
                    </span>
                  </div>
                );
              })}
            </div>
            {/* Reference level markers below list */}
            <div className="mt-3 flex flex-wrap gap-2 text-[15px] font-bold font-mono text-muted-foreground">
              <span style={{ color: SPOT_LINE }}>S {fmt(snap.spot, 0)}</span>
              {conc?.pin_strike != null ? (
                <span style={{ color: PIN_LINE }}>P {fmt(conc.pin_strike)}</span>
              ) : null}
              {cliffStrike != null ? (
                <span style={{ color: CLIFF_LINE }}>C {fmt(cliffStrike)}</span>
              ) : null}
            </div>
          </CardContent>
        </Card>

        {/* Right: contributors + call/put HHI + spark */}
        <div className="flex flex-col gap-4 lg:col-span-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2 py-3">
              <CardTitle className="text-sm">What builds the HHI</CardTitle>
              <Select
                value={String(topN)}
                onValueChange={(v) => setTopN(Number(v) as TopNOption)}
              >
                <SelectTrigger className="h-7 w-[4.5rem] text-xs" aria-label="Top strikes">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TOP_N_OPTIONS.map((n) => (
                    <SelectItem key={n} value={String(n)}>
                      Top {n}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </CardHeader>
            <CardContent className="space-y-2">
              {contributors.length ? (
                <ResponsiveContainer width="100%" height={Math.max(140, contributors.length * 36)}>
                  <BarChart
                    data={contributors.map((c) => ({
                      ...c,
                      label: String(c.strike),
                      share_pct: c.share * 100,
                    }))}
                    layout="vertical"
                    margin={{ top: 4, right: 12, left: 8, bottom: 4 }}
                  >
                    <XAxis type="number" domain={[0, "auto"]} tickFormatter={(v) => `${v}%`} fontSize={10} />
                    <YAxis type="category" dataKey="label" width={52} fontSize={10} />
                    <Tooltip
                      formatter={(v: number) => [`${v.toFixed(1)}%`, "Share"]}
                      labelFormatter={(_, payload) => {
                        const row = payload?.[0]?.payload;
                        return row
                          ? `${row.strike} · ${row.side_bias} · ${gexCrore(row.net_gex)} Cr`
                          : "";
                      }}
                    />
                    <Bar dataKey="share_pct" radius={[0, 3, 3, 0]}>
                      {contributors.map((c) => (
                        <Cell key={c.strike} fill={sideBiasColor(c.side_bias)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <p className="text-xs text-muted-foreground">No contributor mass yet.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-3">
              <CardTitle className="text-sm font-bold">Call / Put HHI</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <div
                className="rounded-md border px-3 py-2"
                style={{ borderColor: `${CE_COLOR}66` }}
              >
                <p className="text-[15px] font-bold uppercase" style={{ color: CE_COLOR }}>
                  Call HHI
                </p>
                <p className="font-mono text-[23px] font-bold tabular-nums leading-tight">
                  {callHhi != null ? callHhi.toFixed(2) : "—"}
                </p>
              </div>
              <div
                className="rounded-md border px-3 py-2"
                style={{ borderColor: `${PE_COLOR}66` }}
              >
                <p className="text-[15px] font-bold uppercase" style={{ color: PE_COLOR }}>
                  Put HHI
                </p>
                <p className="font-mono text-[23px] font-bold tabular-nums leading-tight">
                  {putHhi != null ? putHhi.toFixed(2) : "—"}
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2 py-3">
              <CardTitle className="text-sm">Intraday HHI</CardTitle>
              <Badge variant="outline" className="text-[10px] font-normal">
                intraday rank
              </Badge>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="flex flex-wrap gap-3 text-xs">
                <span>
                  Rank{" "}
                  <span className="font-mono font-semibold">
                    {intradayPct != null ? ordinal(intradayPct) : "—"}
                  </span>
                  <span className="ml-1 text-muted-foreground">today&apos;s ticks</span>
                </span>
                <span className="text-muted-foreground">
                  Session mean{" "}
                  <span className="font-mono">
                    {sessionMean != null ? sessionMean.toFixed(2) : "—"}
                  </span>
                </span>
              </div>
              {sparkChartData.length > 1 ? (
                <ResponsiveContainer width="100%" height={96}>
                  <LineChart
                    data={sparkChartData}
                    margin={{ top: 6, right: 6, left: 0, bottom: 2 }}
                  >
                    <YAxis domain={["auto", "auto"]} hide />
                    <XAxis
                      dataKey="ms"
                      type="number"
                      domain={["dataMin", "dataMax"]}
                      tickFormatter={(ms: number) => formatHhMm(ms)}
                      tick={{ fontSize: 9, fill: "currentColor" }}
                      tickCount={4}
                      minTickGap={36}
                      axisLine={false}
                      tickLine={false}
                      height={18}
                    />
                    <Tooltip
                      isAnimationActive={false}
                      labelFormatter={(ms: number) => formatHhMm(Number(ms))}
                      contentStyle={{ fontSize: 11 }}
                      formatter={(v: number) => [Number(v).toFixed(3), "HHI"]}
                    />
                    {sessionMean != null ? (
                      <ReferenceLine y={sessionMean} stroke="#94a3b8" strokeDasharray="3 3" />
                    ) : null}
                    <Line
                      type="monotone"
                      dataKey="hhi"
                      stroke={HHI_LINE}
                      strokeWidth={1.5}
                      dot={false}
                      isAnimationActive={false}
                      name="hhi"
                    />
                    <Line
                      type="linear"
                      dataKey="flipHhi"
                      stroke="none"
                      legendType="none"
                      tooltipType="none"
                      isAnimationActive={false}
                      connectNulls={false}
                      dot={{
                        r: 3.5,
                        fill: HHI_FLIP_DOT,
                        stroke: "#fff",
                        strokeWidth: 1,
                      }}
                      activeDot={false}
                      name="flip"
                    />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  Need a few session ticks for the spark.
                </p>
              )}
              {recentFlips.length > 0 ? (
                <p className="font-mono text-[10px] leading-relaxed text-muted-foreground">
                  {recentFlips.map((f, i) => (
                    <span key={`${f.ms}-${f.dir}`}>
                      {i > 0 ? " · " : ""}
                      <span className="text-foreground/80">{f.label}</span>{" "}
                      {f.dir === "above" ? "↑ above" : "↓ below"} mean
                    </span>
                  ))}
                  {hhiFlips.length > MAX_FLIP_LIST ? (
                    <span className="text-muted-foreground/80">
                      {" "}
                      · +{hhiFlips.length - MAX_FLIP_LIST} earlier
                    </span>
                  ) : null}
                </p>
              ) : sparkChartData.length > 1 ? (
                <p className="text-[10px] text-muted-foreground">No mean crosses yet today.</p>
              ) : null}
              <p className="text-[10px] text-muted-foreground">
                Percentile vs today&apos;s poll ticks only — not cross-session.
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2 py-3">
              <CardTitle className="text-sm">30-session HHI</CardTitle>
              <Badge variant="outline" className="text-[10px] font-normal">
                last 30 days
              </Badge>
            </CardHeader>
            <CardContent className="space-y-1.5">
              <p className="font-mono text-2xl font-semibold tabular-nums tracking-tight">
                {conc?.hhi_percentile_30d != null
                  ? ordinal(conc.hhi_percentile_30d)
                  : "—"}
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  · last 30 days
                </span>
              </p>
              <p className="text-[10px] text-muted-foreground">
                {conc?.hhi_session_count != null && conc.hhi_session_count > 0
                  ? `Rank among ${conc.hhi_session_count} trading-day session${
                      conc.hhi_session_count === 1 ? "" : "s"
                    } (day-end HHI)`
                  : "Builds as day-end HHI is persisted across sessions"}
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

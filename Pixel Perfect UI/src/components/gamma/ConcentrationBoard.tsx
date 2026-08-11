import { useCallback, useEffect, useMemo, useState } from "react";
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api } from "@/lib/api";
import type {
  GammaConcentrationBand,
  GammaConcentrationSummary,
  GammaConcentrationSummaryItem,
  GammaMassBasis,
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
import { GammaLadder } from "@/components/gamma/concentration/GammaLadder";
import { HhiBuilders, type TopNOption } from "@/components/gamma/concentration/HhiBuilders";
import { HhiHero } from "@/components/gamma/concentration/HhiHero";
import { HhiSessionsChart } from "@/components/gamma/concentration/HhiSessionsChart";
import { OiChangePanel } from "@/components/gamma/concentration/OiChangePanel";
import { SideHhiCards } from "@/components/gamma/concentration/SideHhiCards";
import {
  CLIFF_LINE,
  PIN_LINE,
  SPOT_LINE,
  bandLabel,
  bandTone,
  fmt,
  ordinal,
} from "@/components/gamma/concentration/shared";

const DEFAULT_STRIP: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];
const STRIP_POLL_MS = 90_000;
const HHI_LINE = "#0f766e";
const HHI_FLIP_DOT = "#d97706";
const MAX_FLIP_LIST = 4;

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

/**
 * Client-side HHI for one side; mirrors the backend fallback chain. Gross by
 * construction (per-side absolute masses), same as `call_hhi` / `put_hhi`.
 */
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

/**
 * Fallback contributors when the API sends none. Gross basis (|CE γ| + |PE γ|)
 * to match the backend default — a net basis cancels balanced strikes to zero.
 */
function contributorsFromStrikes(strikes: GammaStrikeRow[]): GammaTopContributor[] {
  const rows = strikes.map((r) => {
    const ce = Math.abs(Number(r.ce_gex ?? 0) || 0);
    const pe = Math.abs(Number(r.pe_gex ?? 0) || 0);
    const net = Number(r.net_gex ?? 0) || 0;
    const gross = ce + pe;
    const mass = gross > 0 ? gross : Math.abs(net);
    let side_bias: GammaTopContributor["side_bias"] = "mixed";
    if (ce > pe * 1.05) side_bias = "call";
    else if (pe > ce * 1.05) side_bias = "put";
    return { strike: r.strike, mass, net_gex: net, gross_gex: gross, side_bias };
  });
  let total = rows.reduce((s, r) => s + r.mass, 0);
  if (total <= 0) {
    for (const r of rows) r.mass = Math.abs(Number(r.net_gex) || 0);
    total = rows.reduce((s, r) => s + r.mass, 0);
  }
  if (total <= 0) return [];
  return rows
    .map((r) => {
      const share = r.mass / total;
      return {
        strike: r.strike,
        share: Math.round(share * 10000) / 10000,
        share_sq: Math.round(share * share * 1e6) / 1e6,
        net_gex: r.net_gex,
        gross_gex: r.gross_gex,
        side_bias: r.side_bias,
      };
    })
    .sort((a, b) => b.share - a.share);
}

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

function quadrantTone(quadrant: string | null | undefined): string {
  if (!quadrant) return "border-border bg-muted text-muted-foreground";
  if (quadrant.startsWith("unequal")) {
    return "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-200";
  }
  return "border-sky-500/40 bg-sky-500/10 text-sky-800 dark:text-sky-200";
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
    const base = items.length ? items : DEFAULT_STRIP.map((u) => emptyStripItem(u));
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
        const hhiTxt = item.hhi != null ? item.hhi.toFixed(3) : "—";
        const bandTxt = item.hhi != null ? bandLabel(item.band, item.band_label) : "—";
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
              {u} <span className="text-foreground/90">{hhiTxt}</span>
              <span className="ml-1 font-sans text-[10px] font-normal text-muted-foreground">
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

function StructurePanel({
  snap,
  cliffStrike,
}: {
  snap: GammaSnapshot;
  cliffStrike: number | null;
}) {
  const conc = snap.concentration;
  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-sm">Structure &amp; shape</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-3 gap-3">
          <div>
            <p className="text-[10px] uppercase tracking-[0.12em]" style={{ color: SPOT_LINE }}>
              Spot
            </p>
            <p className="font-mono text-lg font-semibold tabular-nums">{fmt(snap.spot, 0)}</p>
            <p className="text-[10px] text-muted-foreground">ATM {fmt(snap.atm_strike)}</p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.12em]" style={{ color: PIN_LINE }}>
              Pin
            </p>
            <p className="font-mono text-lg font-semibold tabular-nums">
              {fmt(conc?.pin_strike)}
            </p>
            <p className="text-[10px] text-muted-foreground">
              {conc?.pin_share != null
                ? `${(conc.pin_share * 100).toFixed(0)}% share${
                    conc.pin_stable === true
                      ? " · stable"
                      : conc.pin_stable === false
                        ? " · moving"
                        : ""
                  }`
                : "—"}
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.12em]" style={{ color: CLIFF_LINE }}>
              Cliff
            </p>
            <p className="font-mono text-lg font-semibold tabular-nums">{fmt(cliffStrike)}</p>
            <p className="text-[10px] text-muted-foreground">flip, else breakout wall</p>
          </div>
        </div>

        <div className="flex items-end justify-between gap-2 border-t border-border/60 pt-3">
          <div>
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Gini</p>
            <p className="font-mono text-2xl font-semibold tabular-nums">
              {conc?.gini != null ? conc.gini.toFixed(2) : "—"}
            </p>
            <p className="text-[10px] text-muted-foreground">
              inequality of strike γ, not concentration
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            {conc?.shape_quadrant ? (
              <Badge variant="outline" className={quadrantTone(conc.shape_quadrant)}>
                {conc.shape_quadrant}
              </Badge>
            ) : null}
            <Badge variant="outline" className={bandTone(conc?.band)}>
              {bandLabel(conc?.band, conc?.band_label)}
            </Badge>
          </div>
        </div>

        <div className="space-y-1.5 border-t border-border/60 pt-3 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">
            {snap.market_read?.regime_line ?? `${snap.gamma_regime} gamma`}
          </p>
          <p>{snap.market_read?.shape_line ?? "—"}</p>
          <p>{snap.market_read?.vol_line ?? "—"}</p>
          <p className="text-[10px]">
            Top1 {conc?.top1_share != null ? `${(conc.top1_share * 100).toFixed(0)}%` : "—"}
            {" · "}
            Top5 {conc?.top5_share != null ? `${(conc.top5_share * 100).toFixed(0)}%` : "—"}
            {" · "}
            Eff strikes {conc?.effective_strikes ?? "—"}
            {conc?.hhi_net != null && conc?.mass_basis !== "net"
              ? ` · net-basis HHI ${conc.hhi_net.toFixed(3)}`
              : ""}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function IntradayHhiPanel({ snap }: { snap: GammaSnapshot }) {
  const conc = snap.concentration;

  const sparkData = useMemo((): HhiSparkPoint[] => {
    return (snap.history ?? [])
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
    return sparkData.map((p) => ({ ...p, flipHhi: flipMs.has(p.ms) ? p.hhi : null }));
  }, [sparkData, hhiFlips]);

  const recentFlips = useMemo(() => hhiFlips.slice(-MAX_FLIP_LIST), [hhiFlips]);

  return (
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
              {sessionMean != null ? sessionMean.toFixed(3) : "—"}
            </span>
          </span>
        </div>
        {sparkChartData.length > 1 ? (
          <ResponsiveContainer width="100%" height={96}>
            <LineChart data={sparkChartData} margin={{ top: 6, right: 6, left: 0, bottom: 2 }}>
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
                dot={{ r: 3.5, fill: HHI_FLIP_DOT, stroke: "#fff", strokeWidth: 1 }}
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
          Rank is the share of today&apos;s ticks at or below the current reading, current tick
          included — not a cross-session percentile.
        </p>
      </CardContent>
    </Card>
  );
}

function MassBasisSelect({
  value,
  onChange,
}: {
  value: GammaMassBasis;
  onChange: (b: GammaMassBasis) => void;
}) {
  return (
    <div className="ml-auto flex items-center gap-2">
      <span className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
        γ mass
      </span>
      <Select value={value} onValueChange={(v) => onChange(v as GammaMassBasis)}>
        <SelectTrigger className="h-7 w-[9.5rem] text-xs" aria-label="HHI mass basis">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="gross">Gross · |CE|+|PE|</SelectItem>
          <SelectItem value="net">Net · |CE+PE|</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}

export function ConcentrationBoard({
  snap,
  selectedUnderlying,
  onSelectUnderlying,
  summaryRefreshToken,
  massBasis,
  onMassBasisChange,
}: {
  snap: GammaSnapshot;
  selectedUnderlying?: OiUnderlying;
  onSelectUnderlying?: (u: OiUnderlying) => void;
  /** Bump (e.g. parent Refresh) to re-fetch the multi-index strip. */
  summaryRefreshToken?: number;
  /** Per-strike mass the HHI is built from. Changing it refetches the snapshot. */
  massBasis?: GammaMassBasis;
  onMassBasisChange?: (b: GammaMassBasis) => void;
}) {
  const conc = snap.concentration;
  const [topN, setTopN] = useState<TopNOption>(5);
  const activeUnderlying = selectedUnderlying ?? snap.underlying;

  const allContributors = useMemo(() => {
    const fromApi = conc?.top_contributors;
    if (fromApi && fromApi.length > 0) return fromApi;
    return contributorsFromStrikes(snap.strikes ?? []);
  }, [conc?.top_contributors, snap.strikes]);

  const callHhi = useMemo(() => {
    if (conc?.call_hhi != null) return conc.call_hhi;
    return sideHhiFromStrikes(snap.strikes ?? [], "call");
  }, [conc?.call_hhi, snap.strikes]);

  const putHhi = useMemo(() => {
    if (conc?.put_hhi != null) return conc.put_hhi;
    return sideHhiFromStrikes(snap.strikes ?? [], "put");
  }, [conc?.put_hhi, snap.strikes]);

  const cliffStrike = useMemo(() => cliffFromSnapshot(snap), [snap]);

  const bandForSide = (v: number | null): GammaConcentrationBand | null => {
    if (v == null) return null;
    const hi = conc?.band_cut_compressed ?? 0.18;
    const lo = conc?.band_cut_balanced ?? 0.08;
    if (v >= hi) return "concentrated";
    if (v >= lo) return "mixed";
    return "diffuse";
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <IndexConcentrationStrip
          selected={activeUnderlying}
          onSelect={onSelectUnderlying}
          refreshToken={summaryRefreshToken}
        />
        {onMassBasisChange ? (
          <MassBasisSelect
            value={(massBasis ?? conc?.mass_basis ?? "gross") as GammaMassBasis}
            onChange={onMassBasisChange}
          />
        ) : null}
      </div>

      <HhiHero snap={snap} conc={conc} />

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-6">
          <GammaLadder snap={snap} conc={conc} cliffStrike={cliffStrike} />
        </div>
        <div className="flex flex-col gap-4 lg:col-span-6">
          <HhiSessionsChart conc={conc} />
          <HhiBuilders
            contributors={allContributors}
            conc={conc}
            topN={topN}
            onTopNChange={setTopN}
          />
          <SideHhiCards
            callHhi={callHhi}
            putHhi={putHhi}
            callBand={conc?.call_band ?? bandForSide(callHhi)}
            putBand={conc?.put_band ?? bandForSide(putHhi)}
          />
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="lg:col-span-7">
          <OiChangePanel snap={snap} />
        </div>
        <div className="flex flex-col gap-4 lg:col-span-5">
          <StructurePanel snap={snap} cliffStrike={cliffStrike} />
          <IntradayHhiPanel snap={snap} />
        </div>
      </div>
    </div>
  );
}

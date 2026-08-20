import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import type {
  OiUnderlying,
  VolumeFootprintContract,
  VolumeFootprintLevels,
  VolumeProfileSnapshot,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export const Route = createFileRoute("/volume-footprint")({
  component: VolumeFootprintPage,
});

const UNDERLYINGS: OiUnderlying[] = [
  "NIFTY",
  "BANKNIFTY",
  "SENSEX",
  "CRUDEOIL",
  "CRUDEOILM",
  "NATURALGAS",
];

const BUY = "#14b8a6";
const SELL = "#f43f5e";
const POC_LINE = "#6366f1";
const VA_FILL = "#6366f1";

/** GEX level colours — same convention as the gamma desk, so levels read alike. */
const SPOT_LINE = "#0891b2";
const CALL_WALL = "#ef4444";
const PUT_WALL = "#22c55e";
const FLIP_LINE = "#db2777";
const PIN_LINE = "#ca8a04";
const POS_GAMMA = "#14b8a6";
const NEG_GAMMA = "#f43f5e";

type LevelDef = { key: string; label: string; value: number | null | undefined; color: string; dash?: string };

/**
 * The seven GEX levels, in the order they should win a label-collision fight:
 * spot first (where price is), then the structural levels.
 */
function levelDefs(lv: VolumeFootprintLevels | null | undefined): LevelDef[] {
  if (!lv?.available) return [];
  return [
    { key: "spot", label: "Spot", value: lv.spot, color: SPOT_LINE },
    { key: "call_wall", label: "Call wall", value: lv.call_wall, color: CALL_WALL, dash: "5 3" },
    { key: "put_wall", label: "Put wall", value: lv.put_wall, color: PUT_WALL, dash: "5 3" },
    { key: "flip", label: "Flip", value: lv.flip, color: FLIP_LINE, dash: "6 3" },
    {
      key: "pin",
      // An `atm` pin is a placeholder, not a gamma pin — say which it is.
      label: lv.pin_source === "dominant" ? "Pin" : `Pin (${lv.pin_source ?? "?"})`,
      value: lv.pin,
      color: PIN_LINE,
      dash: "2 2",
    },
    { key: "pos_peak", label: "+γ peak", value: lv.pos_gamma_peak, color: POS_GAMMA, dash: "1 3" },
    { key: "neg_peak", label: "−γ peak", value: lv.neg_gamma_peak, color: NEG_GAMMA, dash: "1 3" },
  ].filter((d) => d.value != null && Number.isFinite(d.value as number));
}

const REASON_COPY: Record<string, string> = {
  too_few_bars: "Too few session bars yet — a profile built from the opening minutes would be noise.",
  no_session_bars: "No futures candles for this session yet — weekend, holiday, or pre-open.",
  fetch_failed: "Could not fetch session candles. See log/errors.jsonl.",
  future_unresolved: "Front-month future could not be resolved — refresh instruments after login.",
  unknown_underlying: "No configuration for this underlying.",
  engine_error: "The footprint engine could not build a profile. See log/errors.jsonl.",
};

function fmt(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

function Stat({
  label,
  value,
  hint,
  color,
}: {
  label: string;
  value: string;
  hint?: string;
  color?: string;
}) {
  return (
    <Card>
      <CardContent className="py-3.5">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </div>
        <div
          className="font-mono text-2xl font-bold tabular-nums leading-tight"
          style={color ? { color } : undefined}
        >
          {value}
        </div>
        {hint ? (
          <div className="mt-0.5 text-[11px] font-medium text-muted-foreground">{hint}</div>
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * Horizontal volume profile: buy mass left of centre, sell mass right, value
 * area shaded, POC marked. Drawn as inline SVG — the curve is already sampled
 * server-side, so there is nothing to re-derive here.
 */
function ProfileChart({
  snap,
  levels,
}: {
  snap: VolumeProfileSnapshot;
  levels: VolumeFootprintLevels | null | undefined;
}) {
  const curve = snap.curve ?? [];
  const geom = useMemo(() => {
    if (!curve.length) return null;
    const lo = Math.min(...curve.map((c) => c.price));
    const hi = Math.max(...curve.map((c) => c.price));
    const maxSide = Math.max(1e-9, ...curve.map((c) => Math.max(c.buy, c.sell)));
    return { lo, hi, maxSide };
  }, [curve]);

  if (!geom || curve.length < 2) {
    return <p className="text-xs text-muted-foreground">Not enough profile samples to draw.</p>;
  }

  const W = 640;
  const H = Math.max(320, Math.min(720, curve.length * 5));
  const PAD_T = 14;
  const PAD_B = 22;
  const CX = W / 2;
  const HALF = W / 2 - 70;

  const yFor = (price: number) =>
    PAD_T + ((geom.hi - price) / Math.max(geom.hi - geom.lo, 1e-9)) * (H - PAD_T - PAD_B);

  const buyPath = curve
    .map((c) => `${(CX - (c.buy / geom.maxSide) * HALF).toFixed(1)},${yFor(c.price).toFixed(1)}`)
    .join(" L ");
  const sellPath = curve
    .map((c) => `${(CX + (c.sell / geom.maxSide) * HALF).toFixed(1)},${yFor(c.price).toFixed(1)}`)
    .join(" L ");

  const yPoc = snap.poc != null ? yFor(snap.poc) : null;
  const yVah = snap.vah != null ? yFor(snap.vah) : null;
  const yVal = snap.val != null ? yFor(snap.val) : null;

  // Levels outside the session's traded range are NOT clamped to the edge — a
  // wall pinned to the top of the chart reads as "price got there", which it
  // did not. They are listed under the chart instead.
  const defs = levelDefs(levels);
  const inRange = defs.filter(
    (d) => (d.value as number) >= geom.lo && (d.value as number) <= geom.hi,
  );
  const outOfRange = defs.filter(
    (d) => (d.value as number) < geom.lo || (d.value as number) > geom.hi,
  );

  // Stagger labels that would collide, so seven levels stay legible.
  let lastY = -Infinity;
  let stagger = 0;
  const placed = [...inRange]
    .sort((a, b) => (b.value as number) - (a.value as number))
    .map((d) => {
      const y = yFor(d.value as number);
      stagger = y - lastY < 11 ? (stagger + 1) % 3 : 0;
      lastY = y;
      return { ...d, y, x: 6 + stagger * 78 };
    });

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Session volume profile">
      {yVah != null && yVal != null ? (
        <rect
          x={0}
          y={Math.min(yVah, yVal)}
          width={W}
          height={Math.abs(yVal - yVah)}
          fill={VA_FILL}
          opacity={0.07}
        />
      ) : null}

      <path d={`M ${CX},${PAD_T} L ${buyPath} L ${CX},${H - PAD_B} Z`} fill={BUY} opacity={0.55} />
      <path d={`M ${CX},${PAD_T} L ${sellPath} L ${CX},${H - PAD_B} Z`} fill={SELL} opacity={0.55} />

      <line
        x1={CX}
        x2={CX}
        y1={PAD_T}
        y2={H - PAD_B}
        stroke="currentColor"
        className="text-border"
        strokeWidth={1}
      />

      {placed.map((d) => (
        <g key={d.key}>
          <line
            x1={0}
            x2={W}
            y1={d.y}
            y2={d.y}
            stroke={d.color}
            strokeWidth={1.2}
            strokeDasharray={d.dash}
            opacity={0.85}
          />
          <text
            x={d.x}
            y={d.y - 3}
            fontSize={9}
            fill={d.color}
            fontFamily="ui-monospace, monospace"
          >
            {d.label} {fmt(d.value, 0)}
          </text>
        </g>
      ))}

      {yPoc != null ? (
        <>
          <line
            x1={0}
            x2={W}
            y1={yPoc}
            y2={yPoc}
            stroke={POC_LINE}
            strokeWidth={2}
            strokeDasharray="4 3"
          />
          <text
            x={W - 4}
            y={yPoc - 4}
            textAnchor="end"
            fontSize={10}
            fill={POC_LINE}
            fontFamily="ui-monospace, monospace"
          >
            POC {fmt(snap.poc, 0)}
          </text>
        </>
      ) : null}

      {[
        [yVah, snap.vah, "VAH"],
        [yVal, snap.val, "VAL"],
      ].map(([y, price, label]) =>
        y == null ? null : (
          <text
            key={String(label)}
            x={4}
            y={(y as number) - 3}
            fontSize={9}
            fill="currentColor"
            className="text-muted-foreground"
            fontFamily="ui-monospace, monospace"
          >
            {label} {fmt(price as number, 0)}
          </text>
        ),
      )}

      <text x={CX - HALF} y={H - 6} fontSize={9} fill={BUY} fontFamily="ui-monospace, monospace">
        buy
      </text>
      <text
        x={CX + HALF}
        y={H - 6}
        textAnchor="end"
        fontSize={9}
        fill={SELL}
        fontFamily="ui-monospace, monospace"
      >
        sell
      </text>
      </svg>
      {outOfRange.length ? (
        <p className="mt-1 text-[10px] text-muted-foreground">
          Outside today&apos;s traded range:{" "}
          {outOfRange.map((d, i) => (
            <span key={d.key}>
              {i > 0 ? " · " : ""}
              <span style={{ color: d.color }}>
                {d.label} {fmt(d.value, 0)}
              </span>
            </span>
          ))}
        </p>
      ) : null}
    </>
  );
}

function VolumeFootprintPage() {
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [contracts, setContracts] = useState<VolumeFootprintContract[]>([]);
  // "" = front month, so the page works before contracts have loaded.
  const [expiry, setExpiry] = useState<string>("");
  const [snap, setSnap] = useState<VolumeProfileSnapshot | null>(null);
  const [levels, setLevels] = useState<VolumeFootprintLevels | null>(null);
  const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(true);
  const reqId = useRef(0);

  // Contract list is per-underlying and changes only at expiry, so it is not
  // part of the poll.
  useEffect(() => {
    let cancelled = false;
    setExpiry("");
    api
      .get<{ contracts: VolumeFootprintContract[] }>(
        `/volume-footprint/contracts?underlying=${underlying}`,
        { silent: true },
      )
      .then((d) => {
        if (!cancelled) setContracts(d.contracts ?? []);
      })
      .catch(() => {
        if (!cancelled) setContracts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [underlying]);

  const fetchSnap = useCallback(async () => {
    const id = ++reqId.current;
    setLoading(true);
    const q = expiry ? `&expiry=${expiry}` : "";
    // Profile and levels in parallel: the chart must still draw if the option
    // chain is unavailable, and vice versa.
    const [profileRes, levelsRes] = await Promise.allSettled([
      api.get<VolumeProfileSnapshot>(`/volume-footprint/snapshot?underlying=${underlying}${q}`),
      api.get<VolumeFootprintLevels>(`/volume-footprint/levels?underlying=${underlying}`, {
        silent: true,
      }),
    ]);
    if (id !== reqId.current) return;
    setSnap(profileRes.status === "fulfilled" ? profileRes.value : null);
    setLevels(levelsRes.status === "fulfilled" ? levelsRes.value : null);
    setLoading(false);
  }, [underlying, expiry]);

  useEffect(() => {
    void fetchSnap();
  }, [fetchSnap]);

  useEffect(() => {
    if (!auto) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnap();
    }, 60_000);
    return () => clearInterval(t);
  }, [auto, fetchSnap]);

  const tiltColor = snap?.tilt_pp == null ? undefined : snap.tilt_pp > 0 ? BUY : SELL;

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-lg font-semibold">3ST Algo Desk — Volume Footprint</h1>
          <p className="text-xs text-muted-foreground">
            Session volume profile reconstructed from OHLCV geometry: POC, value area, balance.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Select value={underlying} onValueChange={(v) => setUnderlying(v as OiUnderlying)}>
            <SelectTrigger className="h-8 w-[9rem] text-xs" aria-label="Underlying">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {UNDERLYINGS.map((u) => (
                <SelectItem key={u} value={u}>
                  {u}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {contracts.length > 1 ? (
            <Select value={expiry} onValueChange={setExpiry}>
              <SelectTrigger className="h-8 w-[11rem] text-xs" aria-label="Futures contract">
                <SelectValue placeholder="Front month" />
              </SelectTrigger>
              <SelectContent>
                {contracts.map((c) => (
                  <SelectItem key={c.expiry} value={c.expiry}>
                    {c.label ?? "—"} · {c.tradingsymbol}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
          <Button size="sm" variant="outline" onClick={() => void fetchSnap()} disabled={loading}>
            <RefreshCw className={`mr-1 h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button size="sm" variant="outline" onClick={() => setAuto((a) => !a)}>
            {auto ? <Pause className="mr-1 h-3.5 w-3.5" /> : <Play className="mr-1 h-3.5 w-3.5" />}
            {auto ? "Pause" : "Resume"}
          </Button>
        </div>
      </div>

      {!snap ? (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            {loading ? "Loading…" : "No data. Check the Kite session and try Refresh."}
          </CardContent>
        </Card>
      ) : !snap.available ? (
        <Card>
          <CardContent className="space-y-1 py-6">
            <p className="text-sm text-foreground">
              {REASON_COPY[snap.reason ?? ""] ?? "Profile unavailable."}
            </p>
            <p className="text-xs text-muted-foreground">
              {snap.bars} bars so far · needs more before a profile is worth reading.
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="POC"
              value={fmt(snap.poc, 0)}
              hint={`${snap.bars} bars · ${snap.price_axis} axis`}
              color={POC_LINE}
            />
            <Stat
              label="Value area"
              value={`${fmt(snap.val, 0)}–${fmt(snap.vah, 0)}`}
              hint={`${fmt(snap.value_area_pts, 0)} pts wide · 70% of volume`}
            />
            <Stat
              label="Tilt"
              value={
                snap.tilt_pp != null
                  ? `${snap.tilt_pp > 0 ? "+" : ""}${snap.tilt_pp.toFixed(1)}pp`
                  : "—"
              }
              hint={snap.balance_verdict ?? undefined}
              color={tiltColor}
            />
            <Stat
              label="Overlap"
              value={snap.overlap_pct != null ? snap.overlap_pct.toFixed(0) : "—"}
              hint="≥75 = balanced / rotational"
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-12">
            <Card className="lg:col-span-8">
              <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
                <div>
                  <CardTitle className="text-sm">Session volume profile</CardTitle>
                  <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
                    buy left · sell right · shaded band = value area
                  </p>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <Badge variant="outline" className="text-[10px] font-normal">
                    {snap.engine}
                  </Badge>
                  {snap.contract?.tradingsymbol ? (
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {snap.contract.tradingsymbol}
                    </span>
                  ) : null}
                </div>
              </CardHeader>
              <CardContent>
                <ProfileChart snap={snap} levels={levels} />
                {levels?.available ? (
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
                    {levelDefs(levels).map((d) => (
                      <span key={d.key} className="inline-flex items-center gap-1">
                        <span
                          className="inline-block h-0.5 w-3"
                          style={{ background: d.color }}
                        />
                        {d.label}
                      </span>
                    ))}
                    <span className="inline-flex items-center gap-1">
                      <span className="inline-block h-0.5 w-3" style={{ background: POC_LINE }} />
                      POC
                    </span>
                  </div>
                ) : (
                  <p className="mt-2 text-[10px] text-muted-foreground">
                    GEX levels unavailable
                    {levels?.reason ? ` (${levels.reason})` : ""} — profile shown alone.
                  </p>
                )}
              </CardContent>
            </Card>

            <Card className="lg:col-span-4">
              <CardHeader className="py-3">
                <CardTitle className="text-sm">How to read this</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-xs text-muted-foreground">
                {/* The two caveats the vendored engine's own README insists on. */}
                <p>
                  <span className="font-medium text-foreground">The split is a model.</span> Buy and
                  sell volume are inferred from where each candle closed in its range, not measured
                  from an aggressor feed — Kite serves no per-tick data here. Read POC and the value
                  area as <em>structure</em>, not as verified flow.
                </p>
                <p>
                  <span className="font-medium text-foreground">RES checks the arithmetic.</span>{" "}
                  {snap.residual_label} at {snap.residual_ppm ?? "—"} PPM says the drawing faithfully
                  represents the volume behind it. It says nothing about whether the model matches
                  what actually traded.
                </p>
                <p>
                  {snap.price_axis === "future"
                    ? "Options here are written on the future, so volume and strikes already share one price axis — no basis correction is applied or needed."
                    : `Volume comes from the front-month future and has been shifted onto the index axis bar by bar${
                        snap.basis?.median != null
                          ? ` (median basis ${snap.basis.median.toFixed(0)})`
                          : ""
                      }${
                        snap.basis?.matched_bars != null
                          ? `, matched on ${snap.basis.matched_bars} of ${snap.bars} bars`
                          : ""
                      }.`}
                </p>
                <div className="flex flex-wrap gap-2 border-t border-border/60 pt-2 font-mono text-[10px]">
                  <span>bars {snap.bars}</span>
                  <span>tick {snap.mintick}</span>
                  {snap.compute_ms != null ? <span>compute {snap.compute_ms}ms</span> : null}
                  {snap.fut_symbol ? <span>{snap.fut_symbol}</span> : null}
                </div>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

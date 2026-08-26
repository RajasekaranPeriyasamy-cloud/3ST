import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import type {
  OiUnderlying,
  VolumeFootprintContract,
  VolumeFootprintLevels,
  VolumeFootprintOiLadder,
  VolumeFootprintTiltHistory,
  VolumeProfileSnapshot,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { OiLadderCard } from "@/components/volume/OiLadderCard";
import { TiltHistoryCard } from "@/components/volume/TiltHistoryCard";
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

/** Above this the price grid stops helping and starts hiding the profile. */
const MAX_GRID_LINES = 14;

/** Vertical room one label needs at fontSize 9. */
const LABEL_LINE_H = 11;

/** Monospace advance width at fontSize 9, near enough to reserve space with. */
const CHAR_W = 5.4;

/**
 * Width reserved at each edge for the stacked label columns.
 *
 * A peak tag drawn outward from a tip near the edge lands on top of the column
 * label — that is how "-15pp" ended up rendering through "POC 24,278". Tags that
 * would enter this gutter are flipped to point inward instead, which keeps them
 * at their own price rather than nudging them off it.
 */
const GUTTER_W = 92;

/**
 * Push overlapping labels apart down the chart — the same stacking the OI ladder
 * uses when two readings share a row.
 *
 * Ties are broken by `prio` so the anchor label of a column keeps its true
 * position and the others move around it: POC does not shift because a sell peak
 * landed on the same price.
 *
 * Returns each item with `ty`, the y its *text* should use. Callers keep drawing
 * the rule or marker at the original `y` — a line dragged along with its label
 * would misreport the price.
 */
function stackLabels<T extends { y: number; prio: number }>(items: T[]): (T & { ty: number })[] {
  const sorted = [...items].sort((a, b) => a.y - b.y || a.prio - b.prio);
  let prev = -Infinity;
  return sorted.map((it) => {
    const ty = Math.max(it.y, prev + LABEL_LINE_H);
    prev = ty;
    return { ...it, ty };
  });
}
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

/**
 * Overlay switches, above the chart.
 *
 * Doubles as the legend — a swatch that does nothing is wasted space, and this
 * is also the honest fix for label crowding: on a busy session the operator
 * turns off what they are not reading rather than the chart guessing which of
 * eleven overlays matters today.
 *
 * The choice is per-browser and survives a reload; it is a display preference,
 * so a private window or cleared storage simply starts with everything on.
 */
function OverlayToggles({
  defs,
  hidden,
  onToggle,
  onAll,
}: {
  defs: { key: string; label: string; color: string }[];
  hidden: Set<string>;
  onToggle: (key: string) => void;
  onAll: (show: boolean) => void;
}) {
  const anyHidden = defs.some((d) => hidden.has(d.key));
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      {defs.map((d) => {
        const off = hidden.has(d.key);
        return (
          <button
            key={d.key}
            type="button"
            aria-pressed={!off}
            onClick={() => onToggle(d.key)}
            title={off ? `Show ${d.label}` : `Hide ${d.label}`}
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition-colors ${
              off
                ? "border-border/60 text-muted-foreground/60"
                : "border-border bg-muted/40 text-foreground"
            }`}
          >
            <span
              className="inline-block h-0.5 w-3 rounded-full"
              style={{ background: d.color, opacity: off ? 0.3 : 1 }}
            />
            <span className={off ? "line-through" : undefined}>{d.label}</span>
          </button>
        );
      })}
      <button
        type="button"
        onClick={() => onAll(anyHidden)}
        className="ml-1 rounded-full border border-dashed border-border px-2 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
      >
        {anyHidden ? "show all" : "hide all"}
      </button>
    </div>
  );
}

/** localStorage key for the overlay switches. */
const OVERLAY_PREF_KEY = "3st.volume-footprint.hidden-overlays";

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
  hidden,
}: {
  snap: VolumeProfileSnapshot;
  levels: VolumeFootprintLevels | null | undefined;
  /** Overlay keys the operator has switched off. */
  hidden: Set<string>;
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

  // Price-axis gridlines on the strike lattice (NIFTY 50, SENSEX/BANKNIFTY 100,
  // NATURALGAS 5), so a line is a strike and the profile reads against the ladder.
  //
  // Coarsened when the session range would produce too many to read: a wide
  // CRUDEOIL day at a 50-point step would draw a solid block, which is worse
  // than no grid at all. Steps up through 2x / 5x / 10x / 20x until it fits.
  const gridLines = (() => {
    if (hidden.has("grid")) return [];
    const base = snap.grid_step ?? 0;
    if (!base || base <= 0) return [];
    const span = geom.hi - geom.lo;
    let step = base;
    for (const mult of [1, 2, 5, 10, 20, 50]) {
      step = base * mult;
      if (span / step <= MAX_GRID_LINES) break;
    }
    if (span / step > MAX_GRID_LINES) return [];
    const out: { price: number; y: number }[] = [];
    for (let p = Math.ceil(geom.lo / step) * step; p <= geom.hi; p += step) {
      out.push({ price: p, y: yFor(p) });
    }
    return out;
  })();

  // Each side's own peak. Distinct from POC, which is the peak of the *combined*
  // profile — on a one-sided session the three sit at different prices, and that
  // gap is the reading.
  const showPeaks = !hidden.has("peaks");
  const buyPeak = showPeaks ? snap.buy_peak ?? null : null;
  const sellPeak = showPeaks ? snap.sell_peak ?? null : null;
  const peakX = (side: "buy" | "sell", density: number) => {
    const w = (density / geom.maxSide) * HALF;
    return side === "buy" ? CX - w : CX + w;
  };

  const yPoc = snap.poc != null && !hidden.has("poc") ? yFor(snap.poc) : null;
  const yVah = snap.vah != null ? yFor(snap.vah) : null;
  const yVal = snap.val != null ? yFor(snap.val) : null;

  // Levels outside the session's traded range are NOT clamped to the edge — a
  // wall pinned to the top of the chart reads as "price got there", which it
  // did not. They are listed under the chart instead.
  const defs = levelDefs(levels).filter((d) => !hidden.has(d.key));
  const inRange = defs.filter(
    (d) => (d.value as number) >= geom.lo && (d.value as number) <= geom.hi,
  );
  const outOfRange = defs.filter(
    (d) => (d.value as number) < geom.lo || (d.value as number) > geom.hi,
  );

  // Labels are stacked into two columns — buy-side facts on the left, sell-side
  // on the right — rather than staggered horizontally. The old stagger offset a
  // colliding label by 78px, which is narrower than a label like
  // "Call wall 24,300" actually renders, so two levels on the same price ran
  // together as one string. Stacking cannot fail that way.
  //
  // The rule and the marker stay at the true price; only the text moves. A label
  // nudged down is a small imprecision, but a line moved to match it would claim
  // price reached somewhere it did not.
  const leftLabels = stackLabels([
    ...inRange.map((d) => ({
      key: d.key,
      y: yFor(d.value as number),
      text: `${d.label} ${fmt(d.value, 0)}`,
      color: d.color,
      prio: 1,
    })),
    ...(snap.vah != null
      ? [{ key: "VAH", y: yFor(snap.vah), text: `VAH ${fmt(snap.vah, 0)}`, color: null, prio: 2 }]
      : []),
    ...(snap.val != null
      ? [{ key: "VAL", y: yFor(snap.val), text: `VAL ${fmt(snap.val, 0)}`, color: null, prio: 2 }]
      : []),
    ...(buyPeak
      ? [
          {
            key: "buy-peak",
            y: yFor(buyPeak.price),
            text: `buy peak ${fmt(buyPeak.price, 0)}`,
            color: BUY,
            prio: 0,
          },
        ]
      : []),
  ]);

  const rightLabels = stackLabels([
    ...(snap.poc != null && !hidden.has("poc")
      ? [{ key: "POC", y: yFor(snap.poc), text: `POC ${fmt(snap.poc, 0)}`, color: POC_LINE, prio: 0 }]
      : []),
    ...(sellPeak
      ? [
          {
            key: "sell-peak",
            y: yFor(sellPeak.price),
            text: `sell peak ${fmt(sellPeak.price, 0)}`,
            color: SELL,
            prio: 1,
          },
        ]
      : []),
  ]);

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

      {gridLines.map((g) => (
        <g key={`grid-${g.price}`}>
          <line
            x1={0}
            x2={W}
            y1={g.y}
            y2={g.y}
            stroke="currentColor"
            className="text-muted-foreground"
            strokeWidth={0.5}
            strokeDasharray="1 4"
            opacity={0.35}
          />
          <text
            x={W - 2}
            y={g.y - 2}
            fontSize={8}
            textAnchor="end"
            fill="currentColor"
            className="text-muted-foreground"
            opacity={0.55}
            fontFamily="ui-monospace, monospace"
          >
            {g.price.toFixed(0)}
          </text>
        </g>
      ))}

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

      {inRange.map((d) => (
        <line
          key={d.key}
          x1={0}
          x2={W}
          y1={yFor(d.value as number)}
          y2={yFor(d.value as number)}
          stroke={d.color}
          strokeWidth={1.2}
          strokeDasharray={d.dash}
          opacity={0.85}
        />
      ))}

      {/* Left column: buy-side facts, stacked so two levels on one price read as
          two lines rather than one run-together string. */}
      {leftLabels.map((l) => (
        <text
          key={l.key}
          x={4}
          y={l.ty - 3}
          fontSize={9}
          fill={l.color ?? "currentColor"}
          className={l.color ? undefined : "text-muted-foreground"}
          fontFamily="ui-monospace, monospace"
        >
          {l.text}
        </text>
      ))}

      {yPoc != null ? (
        <line x1={0} x2={W} y1={yPoc} y2={yPoc} stroke={POC_LINE} strokeWidth={2} strokeDasharray="4 3" />
      ) : null}

      {/* Prominent peaks per side. The tallest keeps a stub back to the centre
          line; the rest are dots only, or the chart turns into a comb.

          The tag is the band tilt — how *this level* traded, which is not the
          session tilt: on 2026-08-26 NIFTY read -19pp overall while its 15:30
          peak read -31pp. A time is appended ONLY when that peak's middle-50%
          of volume fits inside 45 minutes. A profile peak is a price-domain
          feature and its volume usually accumulates across hours, so printing a
          formation time by default would be a fiction. The full window is in
          the tooltip either way. */}
      {(
        [
          ["buy", showPeaks ? snap.buy_peaks ?? [] : [], BUY] as const,
          ["sell", showPeaks ? snap.sell_peaks ?? [] : [], SELL] as const,
        ] as const
      ).map(([side, peaks, color]) =>
        peaks.map((pk, i) => {
          const x = peakX(side, pk.density);
          const y = yFor(pk.price);
          const tag =
            pk.band_tilt_pp == null
              ? ""
              : `${pk.band_tilt_pp > 0 ? "+" : ""}${pk.band_tilt_pp.toFixed(0)}pp` +
                (pk.concentrated && pk.q1 ? ` ${pk.q1}` : "");
          // Point the tag away from centre by default; flip it inward when that
          // would put it in the label column's gutter.
          const tagW = tag.length * CHAR_W;
          const outward = side === "buy" ? x - 5 - tagW : x + 5 + tagW;
          const collides = side === "buy" ? outward < GUTTER_W : outward > W - GUTTER_W;
          const tagX = side === "buy" ? (collides ? x + 5 : x - 5) : collides ? x - 5 : x + 5;
          const tagAnchor =
            side === "buy" ? (collides ? "start" : "end") : collides ? "end" : "start";
          return (
            <g key={`pk-${side}-${pk.price}`} opacity={i === 0 ? 1 : 0.75}>
              <title>
                {`${side} peak ${pk.price.toFixed(0)}
` +
                  `band ${pk.band_lo.toFixed(0)}-${pk.band_hi.toFixed(0)} · prominence ${pk.prominence_pct.toFixed(0)}%
` +
                  `band tilt ${pk.band_tilt_pp ?? "—"}pp (fitted mixture)
` +
                  `flow tilt ${pk.flow_tilt_pp ?? "—"}pp (${pk.bar_count} bars that traded it)
` +
                  `built ${pk.first ?? "?"}–${pk.last ?? "?"}, half of it ${pk.q1 ?? "?"}–${pk.q3 ?? "?"}` +
                  (pk.concentrated ? " · concentrated" : " · spread across the session")}
              </title>
              {i === 0 ? (
                <line
                  x1={CX}
                  x2={x}
                  y1={y}
                  y2={y}
                  stroke={color}
                  strokeWidth={1}
                  strokeDasharray="2 2"
                  opacity={0.9}
                />
              ) : null}
              <circle cx={x} cy={y} r={i === 0 ? 2.5 : 2} fill={color} />
              {tag ? (
                <text
                  x={tagX}
                  y={y - 4}
                  textAnchor={tagAnchor}
                  fontSize={8}
                  fill={color}
                  fontFamily="ui-monospace, monospace"
                >
                  {tag}
                </text>
              ) : null}
            </g>
          );
        }),
      )}

      {/* Right column: sell-side facts and POC, stacked the same way. */}
      {rightLabels.map((l) => (
        <text
          key={l.key}
          x={W - 4}
          y={l.ty - 3}
          textAnchor="end"
          fontSize={l.key === "POC" ? 10 : 9}
          fill={l.color ?? "currentColor"}
          fontFamily="ui-monospace, monospace"
        >
          {l.text}
        </text>
      ))}


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
  const [ladder, setLadder] = useState<VolumeFootprintOiLadder | null>(null);
  const [tiltHist, setTiltHist] = useState<VolumeFootprintTiltHistory | null>(null);
  // Overlay visibility, remembered per browser. Wrapped because a private
  // window or blocked site data throws on access rather than returning null.
  const [hidden, setHidden] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(OVERLAY_PREF_KEY);
      return raw ? new Set<string>(JSON.parse(raw)) : new Set<string>();
    } catch {
      return new Set<string>();
    }
  });

  // Persisting in an effect, not inside the setter: React batches state updates,
  // so a setter that closed over `hidden` made three quick clicks all read the
  // same stale set and overwrite each other — only the last key survived.
  useEffect(() => {
    try {
      localStorage.setItem(OVERLAY_PREF_KEY, JSON.stringify([...hidden]));
    } catch {
      /* display preference only — losing it is not worth surfacing */
    }
  }, [hidden]);

  const toggleOverlay = useCallback((key: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
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
    // All three in parallel: the chart must still draw if the option chain is
    // unavailable, and the OI ladder must still draw if the session is too thin
    // to shape a profile. Levels and the ladder share one snapshot server-side,
    // so this is still a single option-chain pull.
    const [profileRes, levelsRes, ladderRes, tiltRes] = await Promise.allSettled([
      api.get<VolumeProfileSnapshot>(`/volume-footprint/snapshot?underlying=${underlying}${q}`),
      api.get<VolumeFootprintLevels>(`/volume-footprint/levels?underlying=${underlying}`, {
        silent: true,
      }),
      api.get<VolumeFootprintOiLadder>(`/volume-footprint/oi-ladder?underlying=${underlying}`, {
        silent: true,
      }),
      // Sampled for NIFTY/SENSEX only; a 4xx here must not blank the page.
      api.get<VolumeFootprintTiltHistory>(
        `/volume-footprint/tilt-history?underlying=${underlying}`,
        { silent: true },
      ),
    ]);
    if (id !== reqId.current) return;
    setSnap(profileRes.status === "fulfilled" ? profileRes.value : null);
    setLevels(levelsRes.status === "fulfilled" ? levelsRes.value : null);
    setLadder(ladderRes.status === "fulfilled" ? ladderRes.value : null);
    setTiltHist(tiltRes.status === "fulfilled" ? tiltRes.value : null);
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
                {/* Switches live above the chart: they are the legend, and the
                    operator decides what is on screen before reading it. */}
                <OverlayToggles
                  defs={[
                    ...levelDefs(levels),
                    { key: "poc", label: "POC", color: POC_LINE },
                    { key: "peaks", label: "Peaks", color: BUY },
                    { key: "grid", label: "Grid", color: "#94a3b8" },
                  ].map((d) => ({ key: d.key, label: d.label, color: d.color }))}
                  hidden={hidden}
                  onToggle={toggleOverlay}
                  onAll={(show) =>
                    setHidden(
                      show
                        ? new Set<string>()
                        : new Set<string>([
                            ...levelDefs(levels).map((d) => d.key),
                            "poc",
                            "peaks",
                            "grid",
                          ]),
                    )
                  }
                />
                <ProfileChart snap={snap} levels={levels} hidden={hidden} />
                {!levels?.available ? (
                  <p className="mt-2 text-[10px] text-muted-foreground">
                    GEX levels unavailable
                    {levels?.reason ? ` (${levels.reason})` : ""} — profile shown alone.
                  </p>
                ) : null}
              </CardContent>
            </Card>

            <OiLadderCard ladder={ladder} className="lg:col-span-4" />

            <TiltHistoryCard hist={tiltHist} className="lg:col-span-12" />

            <Card className="lg:col-span-12">
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

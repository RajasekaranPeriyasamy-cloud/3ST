import { useMemo, useState } from "react";

import type { GammaSnapshot } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CE_COLOR, PE_COLOR, compactOi, fmt } from "./shared";

const TOP_N_OPTIONS = [10, 15, 20, 30] as const;
type TopNOption = (typeof TOP_N_OPTIONS)[number];

type Mover = {
  key: string;
  strike: number;
  side: "CE" | "PE";
  doi: number;
  action: string;
  color: string;
};

/**
 * ΔOI sign → book action. Rising OI on a leg is fresh writing (short), falling OI
 * is unwind (covering) — the same convention as the OI Tracker desk.
 *
 * Bars are coloured by **side** (CE red / PE green), matching `sideBiasColor` and
 * the rest of the gamma desk. Writing vs unwind is carried by the sign of the
 * value and the action label, not by colour.
 */
function moverAction(side: "CE" | "PE", doi: number): string {
  if (side === "CE") return doi >= 0 ? "call writing" : "call unwind";
  return doi >= 0 ? "put writing" : "put unwind";
}

export function OiChangePanel({ snap }: { snap: GammaSnapshot }) {
  const [topN, setTopN] = useState<TopNOption>(15);

  const { movers, pcr, dayDoi, hasBaseline } = useMemo(() => {
    const rows = snap.strikes ?? [];
    let ceOi = 0;
    let peOi = 0;
    let net = 0;
    let seenBaseline = false;
    const out: Mover[] = [];
    for (const r of rows) {
      ceOi += Number(r.ce_oi ?? 0) || 0;
      peOi += Number(r.pe_oi ?? 0) || 0;
      for (const side of ["CE", "PE"] as const) {
        const raw = side === "CE" ? r.ce_doi : r.pe_doi;
        if (raw == null) continue;
        seenBaseline = true;
        const doi = Number(raw) || 0;
        net += doi;
        if (doi === 0) continue;
        out.push({
          key: `${r.strike}-${side}`,
          strike: r.strike,
          side,
          doi,
          action: moverAction(side, doi),
          color: side === "CE" ? CE_COLOR : PE_COLOR,
        });
      }
    }
    out.sort((a, b) => Math.abs(b.doi) - Math.abs(a.doi));
    return {
      movers: out,
      pcr: ceOi > 0 ? peOi / ceOi : null,
      dayDoi: seenBaseline ? net : null,
      hasBaseline: seenBaseline,
    };
  }, [snap.strikes]);

  const shown = useMemo(() => movers.slice(0, topN), [movers, topN]);
  const maxAbs = useMemo(
    () => Math.max(1, ...shown.map((m) => Math.abs(m.doi))),
    [shown],
  );

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-sm">OI change · top movers</CardTitle>
          <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
            {snap.oi_baseline_note ?? "ΔOI vs session baseline"}
          </p>
        </div>
        <Select value={String(topN)} onValueChange={(v) => setTopN(Number(v) as TopNOption)}>
          <SelectTrigger className="h-7 w-[4.5rem] text-xs" aria-label="Top movers">
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
      <CardContent className="space-y-4">
        <div className="grid grid-cols-3 gap-4 border-b border-border/60 pb-4">
          <div>
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              ATM strike
            </p>
            <p className="font-mono text-2xl font-semibold tabular-nums">
              {fmt(snap.atm_strike)}
            </p>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              spot {fmt(snap.spot, 0)}
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Put / call
            </p>
            <p className="font-mono text-2xl font-semibold tabular-nums">
              {pcr != null ? pcr.toFixed(2) : "—"}
            </p>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              {pcr == null ? "—" : pcr < 1 ? "call-heavy" : "put-heavy"}
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
              Day ΔOI
            </p>
            {/* Aggregate across both sides, so it takes no side colour — green/red
                now mean CE/PE in the list below, and the caption carries direction. */}
            <p className="font-mono text-2xl font-semibold tabular-nums">
              {compactOi(dayDoi)}
            </p>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              {dayDoi == null ? "no baseline" : dayDoi >= 0 ? "net build" : "net unwind"}
            </p>
          </div>
        </div>

        {!hasBaseline ? (
          <p className="text-xs text-muted-foreground">
            ΔOI needs a session-open or previous-close baseline — none captured yet.
          </p>
        ) : shown.length === 0 ? (
          <p className="text-xs text-muted-foreground">No OI change on the window yet.</p>
        ) : (
          <div className="space-y-1">
            <div className="flex flex-wrap gap-3 pb-1 text-[10px] text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-sm" style={{ background: CE_COLOR }} />
                call (CE)
              </span>
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-2 w-2 rounded-sm" style={{ background: PE_COLOR }} />
                put (PE)
              </span>
              <span>+ writing · − unwind</span>
            </div>
            {shown.map((m) => (
              <div key={m.key} className="flex items-center gap-2 text-[11px]">
                <span className="w-[5.5rem] shrink-0 font-mono tabular-nums font-semibold">
                  {fmt(m.strike)} {m.side}
                </span>
                <div className="relative h-2.5 flex-1 overflow-hidden rounded-sm bg-muted/60">
                  <div
                    className="absolute inset-y-0 left-0 rounded-sm"
                    style={{
                      width: `${(Math.abs(m.doi) / maxAbs) * 100}%`,
                      background: m.color,
                    }}
                  />
                </div>
                <span
                  className="w-16 shrink-0 text-right font-mono tabular-nums"
                  style={{ color: m.color }}
                >
                  {compactOi(m.doi)}
                </span>
                <span className="w-[6.5rem] shrink-0 text-right text-[9px] uppercase tracking-[0.08em] text-muted-foreground">
                  {m.action}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

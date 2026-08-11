import { useMemo } from "react";

import type { GammaConcentration, GammaTopContributor } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fmt, gexCrore, sideBiasColor } from "./shared";

export const TOP_N_OPTIONS = [5, 10, 15, 20, 25] as const;
export type TopNOption = (typeof TOP_N_OPTIONS)[number];

export function HhiBuilders({
  contributors,
  conc,
  topN,
  onTopNChange,
}: {
  contributors: GammaTopContributor[];
  conc: GammaConcentration | null | undefined;
  topN: TopNOption;
  onTopNChange: (n: TopNOption) => void;
}) {
  const rows = useMemo(() => contributors.slice(0, topN), [contributors, topN]);
  const maxShare = useMemo(
    () => Math.max(0.0001, ...rows.map((r) => r.share ?? 0)),
    [rows],
  );
  const cumShare = useMemo(
    () => rows.reduce((s, r) => s + (r.share ?? 0), 0),
    [rows],
  );

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2 py-3">
        <CardTitle className="text-sm">What builds the HHI</CardTitle>
        <Select value={String(topN)} onValueChange={(v) => onTopNChange(Number(v) as TopNOption)}>
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
        {rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">No contributor mass yet.</p>
        ) : (
          <>
            <div className="space-y-1.5">
              {rows.map((r, i) => {
                const pct = (r.share ?? 0) * 100;
                return (
                  <div
                    key={r.strike}
                    className="flex items-center gap-2 text-[11px]"
                    title={`${fmt(r.strike)} · ${r.side_bias} · net ${gexCrore(r.net_gex)} Cr · contributes ${(
                      r.share_sq ?? (r.share ?? 0) ** 2
                    ).toFixed(3)} to HHI`}
                  >
                    <span className="w-6 shrink-0 font-mono tabular-nums text-muted-foreground">
                      #{i + 1}
                    </span>
                    <span className="w-14 shrink-0 font-mono tabular-nums font-semibold">
                      {fmt(r.strike)}
                    </span>
                    <div className="relative h-2.5 flex-1 overflow-hidden rounded-sm bg-muted/60">
                      <div
                        className="absolute inset-y-0 left-0 rounded-sm"
                        style={{
                          width: `${((r.share ?? 0) / maxShare) * 100}%`,
                          background: sideBiasColor(r.side_bias),
                        }}
                      />
                    </div>
                    <span
                      className="w-12 shrink-0 text-right font-mono tabular-nums"
                      style={{ color: sideBiasColor(r.side_bias) }}
                    >
                      {pct.toFixed(1)}%
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="pt-1 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              Top {rows.length} strikes hold {(cumShare * 100).toFixed(0)}% of dealer gamma
              {conc?.effective_strikes != null
                ? ` · ${conc.effective_strikes} effective strikes`
                : ""}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

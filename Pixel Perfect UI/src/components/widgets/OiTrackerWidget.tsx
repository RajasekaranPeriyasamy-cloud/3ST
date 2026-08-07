import { useMemo } from "react";

import { useAnalyticsDesk } from "@/context/AnalyticsDeskContext";
import type { OiChangeBoardEntry, OiMoversSnapshot } from "@/lib/types";
import {
  Table,
  TableBody,
  TableCell,
  TableRow,
} from "@/components/ui/table";
import { WidgetShell } from "./WidgetShell";
import { useWidgetPoll } from "./useWidgetPoll";

function formatCompactChg(value: number | null | undefined): string {
  if (value == null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1000) {
    const k = abs / 1000;
    return `${sign}${k.toFixed(k >= 100 ? 0 : 1)}K`;
  }
  return `${sign}${abs.toLocaleString()}`;
}

function formatPctParen(value: number | null | undefined): string {
  if (value == null) return "";
  const sign = value > 0 ? "+" : "";
  return `(${sign}${value.toFixed(0)}%)`;
}

function boardHasRows(set: {
  increase_abs: OiChangeBoardEntry[];
  increase_pct: OiChangeBoardEntry[];
  decrease_abs: OiChangeBoardEntry[];
  decrease_pct: OiChangeBoardEntry[];
}): boolean {
  return (
    set.increase_abs.length +
      set.increase_pct.length +
      set.decrease_abs.length +
      set.decrease_pct.length >
    0
  );
}

function MiniSideRows({
  label,
  rows,
  tone,
  caption,
}: {
  label: "CE" | "PE";
  rows: OiChangeBoardEntry[];
  tone: "increase" | "decrease";
  caption: string;
}) {
  const barClass = tone === "increase" ? "bg-emerald-500/80" : "bg-rose-500/80";
  const rowBg = tone === "increase" ? "bg-emerald-100/50" : "bg-rose-100/50";
  const chgTone = tone === "increase" ? "text-emerald-700" : "text-rose-700";
  const sideTone = label === "CE" ? "text-amber-700" : "text-sky-700";

  return (
    <div className="space-y-0.5">
      <p className={`text-[9px] font-bold uppercase tracking-wider ${sideTone}`}>{label}</p>
      <Table>
        <TableBody>
          {rows.slice(0, 3).map((row) => (
            <TableRow key={`${caption}-${label}-${row.contract}`} className={rowBg}>
              <TableCell className="px-1.5 py-1 font-mono text-[10px] leading-tight">
                {row.contract}
              </TableCell>
              <TableCell className="px-1.5 py-1 text-right">
                <div className={`font-mono text-[10px] font-semibold ${chgTone}`}>
                  {formatCompactChg(row.abs_chg)}{" "}
                  <span className="font-normal opacity-80">{formatPctParen(row.pct_chg)}</span>
                </div>
                <div className="mt-0.5 h-1 w-full overflow-hidden rounded-sm bg-muted/60">
                  <div
                    className={`h-full rounded-sm ${barClass}`}
                    style={{ width: `${Math.min(100, Math.max(0, row.bar_pct))}%` }}
                  />
                </div>
              </TableCell>
            </TableRow>
          ))}
          {!rows.length ? (
            <TableRow>
              <TableCell colSpan={2} className="px-1.5 py-1 text-[10px] text-muted-foreground">
                No moves
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>
    </div>
  );
}

function MiniBoard({
  caption,
  rows,
  tone,
}: {
  caption: string;
  rows: OiChangeBoardEntry[];
  tone: "increase" | "decrease";
}) {
  const ceRows = rows.filter((r) => r.option_type === "CE");
  const peRows = rows.filter((r) => r.option_type === "PE");

  return (
    <div className="space-y-1.5">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {caption}
      </p>
      <MiniSideRows label="CE" rows={ceRows} tone={tone} caption={caption} />
      <MiniSideRows label="PE" rows={peRows} tone={tone} caption={caption} />
    </div>
  );
}

export function OiTrackerWidget() {
  const { underlying, expiry } = useAnalyticsDesk();
  const url = expiry
    ? `/oi-movers/snapshot?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}`
    : null;
  const { data, loading, error, authError } = useWidgetPoll<OiMoversSnapshot>(url);

  const set = useMemo(() => {
    const boards = data?.change_boards;
    if (!boards) return undefined;
    return boards.session ?? Object.values(boards)[0];
  }, [data]);
  const showChangeBoards = Boolean(set && boardHasRows(set));

  return (
    <WidgetShell
      title="OI Movers"
      fullRoute="/oi-movers"
      loading={loading}
      authError={authError}
      error={error}
      meta={
        data ? (
          <>
            Spot {data.spot.toFixed(1)} · ATM {data.atm_strike} · PCR{" "}
            {data.pcr?.chain_oi != null ? data.pcr.chain_oi.toFixed(2) : "—"}
          </>
        ) : null
      }
    >
      {error && !data ? (
        <p className="py-8 text-center text-xs text-destructive">{error}</p>
      ) : !data ? (
        <p className="py-8 text-center text-xs text-muted-foreground">
          {loading ? "Loading…" : expiry ? "No OI movers data" : "Select an expiry"}
        </p>
      ) : (
        <div className="space-y-2">
          <p className="text-[11px] text-muted-foreground">
            Change = Curr − Open/PD · {underlying}
          </p>

          {showChangeBoards && set ? (
            <div className="grid gap-2 sm:grid-cols-2">
              <div className="rounded-md border border-emerald-500/30 bg-emerald-50/50 p-2">
                <p className="mb-1 text-[11px] font-semibold text-emerald-700">Increase</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  <MiniBoard caption="Abs" rows={set.increase_abs} tone="increase" />
                  <MiniBoard caption="%" rows={set.increase_pct} tone="increase" />
                </div>
              </div>
              <div className="rounded-md border border-rose-500/30 bg-rose-50/50 p-2">
                <p className="mb-1 text-[11px] font-semibold text-rose-700">Decrease</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  <MiniBoard caption="Abs" rows={set.decrease_abs} tone="decrease" />
                  <MiniBoard caption="%" rows={set.decrease_pct} tone="decrease" />
                </div>
              </div>
            </div>
          ) : (
            <p className="py-6 text-center text-[11px] text-muted-foreground">
              No movers yet (need open or prior-day OI)
            </p>
          )}
        </div>
      )}
    </WidgetShell>
  );
}

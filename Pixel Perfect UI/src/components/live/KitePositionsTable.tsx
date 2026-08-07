import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import type { DeskPosition, DeskPositionGroup } from "@/lib/types";
import type { LtpTick } from "@/hooks/useLtpFeed";
import { cn } from "@/lib/utils";
import { Checkbox } from "@/components/ui/checkbox";

function fmtNum(n: number, digits = 2) {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtPct(n: number) {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function PnlCell({ value }: { value: number }) {
  const positive = value >= 0;
  return (
    <span
      className={cn(
        "inline-block min-w-[5.5rem] rounded px-2 py-0.5 text-right font-mono text-sm tabular-nums",
        positive ? "bg-bull/15 text-bull" : "bg-bear/15 text-bear",
      )}
    >
      {fmtNum(value)}
    </span>
  );
}

function ProductBadge({ product }: { product: string }) {
  return (
    <span className="inline-flex rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-300">
      {product}
    </span>
  );
}

function PositionRow({
  row,
  selected,
  onToggle,
  tick,
}: {
  row: DeskPosition;
  selected: boolean;
  onToggle: () => void;
  tick?: LtpTick;
}) {
  const qtyClass = row.quantity >= 0 ? "text-sky-400" : "text-sky-300";
  // Prefer the live WS tick for a smooth mark-to-market between 5s polls.
  const live = tick != null;
  const lastPrice = live ? tick!.price : row.last_price;
  const pnl =
    live && row.average_price
      ? (lastPrice - row.average_price) * row.quantity
      : row.pnl;
  const changePct =
    live && row.average_price
      ? ((lastPrice - row.average_price) / row.average_price) * 100
      : row.change_pct;
  const chgPositive = changePct >= 0;

  return (
    <tr className="border-b border-border/60 hover:bg-muted/20">
      <td className="w-8 px-2 py-2.5">
        <Checkbox checked={selected} onCheckedChange={onToggle} aria-label="Select position" />
      </td>
      <td className="px-2 py-2.5">
        <ProductBadge product={row.product} />
      </td>
      <td className="px-3 py-2.5 font-mono text-sm">{row.instrument}</td>
      <td className={cn("px-3 py-2.5 text-right font-mono text-sm tabular-nums", qtyClass)}>
        {row.quantity.toLocaleString()}
      </td>
      <td className="px-3 py-2.5 text-right font-mono text-sm tabular-nums text-muted-foreground">
        {fmtNum(row.average_price)}
      </td>
      <td className="px-3 py-2.5 text-right font-mono text-sm tabular-nums">
        <span className="inline-flex items-center justify-end gap-1">
          {live && tick!.fresh && (
            <span
              className="h-1.5 w-1.5 rounded-full bg-bull"
              title={`Live · ${tick!.age_sec.toFixed(1)}s`}
            />
          )}
          {fmtNum(lastPrice)}
        </span>
      </td>
      <td className="px-3 py-2.5 text-right">
        <PnlCell value={pnl} />
      </td>
      <td
        className={cn(
          "px-3 py-2.5 text-right font-mono text-sm tabular-nums",
          chgPositive ? "text-bull" : "text-bear",
        )}
      >
        {fmtPct(changePct)}
      </td>
    </tr>
  );
}

function GroupBlock({
  group,
  expanded,
  onToggle,
  selected,
  onSelectRow,
  liveLtp,
}: {
  group: DeskPositionGroup;
  expanded: boolean;
  onToggle: () => void;
  selected: Set<string>;
  onSelectRow: (key: string) => void;
  liveLtp?: Record<string, LtpTick>;
}) {
  const groupPositive = group.total_pnl >= 0;
  return (
    <>
      <tr
        className="cursor-pointer border-b border-border bg-muted/30 hover:bg-muted/40"
        onClick={onToggle}
      >
        <td className="px-2 py-2" colSpan={2}>
          {expanded ? (
            <ChevronDown className="inline h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="inline h-4 w-4 text-muted-foreground" />
          )}
        </td>
        <td className="px-3 py-2 text-sm font-medium text-muted-foreground" colSpan={4}>
          {group.label}
        </td>
        <td className="px-3 py-2 text-right">
          <PnlCell value={group.total_pnl} />
        </td>
        <td
          className={cn(
            "px-3 py-2 text-right font-mono text-xs tabular-nums",
            groupPositive ? "text-bull" : "text-bear",
          )}
        >
          —
        </td>
      </tr>
      {expanded &&
        group.positions.map((row) => {
          const key = `${row.exchange}:${row.tradingsymbol}`;
          return (
            <PositionRow
              key={key}
              row={row}
              selected={selected.has(key)}
              onToggle={() => onSelectRow(key)}
              tick={liveLtp?.[key]}
            />
          );
        })}
    </>
  );
}

export function KitePositionsTable({
  groups,
  empty = "No open positions",
  liveLtp,
}: {
  groups: DeskPositionGroup[];
  empty?: string;
  liveLtp?: Record<string, LtpTick>;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());

  function toggleGroup(key: string) {
    setExpanded((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }));
  }

  function toggleRow(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  if (!groups.length) {
    return (
      <div className="rounded-md border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
        {empty}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card/40">
      <table className="w-full min-w-[820px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
            <th className="w-8 px-2 py-2" />
            <th className="px-2 py-2 text-left font-medium">Product</th>
            <th className="px-3 py-2 text-left font-medium">Instrument</th>
            <th className="px-3 py-2 text-right font-medium">Qty.</th>
            <th className="px-3 py-2 text-right font-medium">Avg.</th>
            <th className="px-3 py-2 text-right font-medium">LTP</th>
            <th className="px-3 py-2 text-right font-medium">P&amp;L</th>
            <th className="px-3 py-2 text-right font-medium">Chg.</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((group) => (
            <GroupBlock
              key={group.key}
              group={group}
              expanded={expanded[group.key] ?? true}
              onToggle={() => toggleGroup(group.key)}
              selected={selected}
              onSelectRow={toggleRow}
              liveLtp={liveLtp}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function formatDeskPnl(value: number) {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${fmtNum(value)}`;
}

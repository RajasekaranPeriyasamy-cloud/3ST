import { AlertTriangle } from "lucide-react";

import type { OptArbXCell, OptArbXSheet } from "@/lib/types";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function money(value: number | null | undefined) {
  if (value == null) return "—";
  return value.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

/**
 * One cell of the grid. The number shown is **net of charges** — a raw spread
 * that looks green at 300 rupees is a loss once a round trip on both legs is
 * paid for, and that difference is the whole reason this desk exists.
 */
function Cell({ cell }: { cell: OptArbXCell | null }) {
  if (!cell) {
    return <TableCell className="text-right font-mono text-xs text-muted-foreground">—</TableCell>;
  }
  return (
    <TableCell
      className={`text-right font-mono text-xs ${
        cell.passes
          ? "bg-emerald-500/20 font-semibold text-emerald-700 dark:text-emerald-300"
          : cell.net < 0
            ? "text-muted-foreground"
            : ""
      }`}
      title={`gross ₹${money(cell.gross)} · charges ₹${money(cell.cost)} · ${cell.max_lots} lot(s) of depth`}
    >
      {money(cell.net)}
    </TableCell>
  );
}

export function BigMiniSheet({ sheet }: { sheet: OptArbXSheet }) {
  // The expiry-level flag, not the pair-level one: a pair can be carry in the
  // front month and a true arbitrage further out, and this grid is showing one
  // specific expiry.
  const clean = sheet.clean;
  return (
    <Card className={clean ? "" : "border-amber-500/40"}>
      <CardHeader className="pb-2">
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          <span className="uppercase tracking-wide">
            {sheet.pair.big} vs {sheet.pair.mini}
          </span>
          <Badge variant={sheet.option_type === "CE" ? "destructive" : "default"}>
            {sheet.option_type}
          </Badge>
          <Badge variant={clean ? "default" : "secondary"}>
            {clean ? "Tier A" : "Tier B — carry"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs">
          <span className="text-muted-foreground">Forward basis</span>
          <span
            className={`font-mono text-base font-semibold ${
              clean ? "" : "text-amber-600 dark:text-amber-400"
            }`}
          >
            {sheet.basis.value == null ? "—" : money(sheet.basis.value)}
          </span>
          <span className="text-muted-foreground">per {sheet.basis.unit}</span>
          <span className="text-muted-foreground">
            · 1 {sheet.pair.big} lot = {sheet.ratio} {sheet.pair.mini} lots · {sheet.lots} lot(s)
          </span>
        </div>

        <p className={`text-xs ${clean ? "text-muted-foreground" : "text-amber-600 dark:text-amber-400"}`}>
          {clean ? null : <AlertTriangle className="mr-1 inline h-3 w-3" />}
          {sheet.basis.note}
        </p>

        <div className="flex flex-wrap gap-x-4 text-xs text-muted-foreground">
          <span>
            {sheet.pair.big} expiry <span className="font-mono">{sheet.expiry.big ?? "—"}</span>
          </span>
          <span>
            {sheet.pair.mini} expiry <span className="font-mono">{sheet.expiry.mini ?? "—"}</span>
          </span>
          {!sheet.expiry.matched && (
            <span className="text-amber-600 dark:text-amber-400">
              — different contract months, the columns are not the same claim
            </span>
          )}
        </div>

        {sheet.skipped ? (
          <p className="py-6 text-center text-sm text-muted-foreground">{sheet.skipped}</p>
        ) : (
          <div className="max-h-[520px] overflow-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Strike</TableHead>
                  <TableHead className="text-right">BUY</TableHead>
                  <TableHead className="text-right">SELL</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sheet.rows.map((row) => (
                  <TableRow
                    key={row.strike}
                    className={row.strike === sheet.atm_strike ? "bg-amber-500/20" : ""}
                  >
                    <TableCell className="font-mono text-xs font-medium">{row.strike}</TableCell>
                    <Cell cell={row.buy} />
                    <Cell cell={row.sell} />
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        <p className="text-[10px] text-muted-foreground">
          Cells are net of charges. BUY = buy {sheet.pair.big} / sell {sheet.pair.mini}; SELL is the
          reverse. Both priced at the side of the book you would hit, so they are not mirror
          images — the gap between them is the round trip. Hover a cell for gross, charges and
          available depth.
        </p>
      </CardContent>
    </Card>
  );
}

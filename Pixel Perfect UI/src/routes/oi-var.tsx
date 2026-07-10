import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { Pause, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import type {
  OiUnderlying,
  OiVarConfig,
  OiVarFooter,
  OiVarRow,
  OiVarSnapshot,
} from "@/lib/types";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export const Route = createFileRoute("/oi-var")({
  component: OiVarPage,
});

const UNDERLYINGS: OiUnderlying[] = ["NIFTY", "BANKNIFTY", "SENSEX"];

type TableKind = "top_oi" | "top_chg" | "bottom_chg";

function moneynessClass(label: string): string {
  if (label === "ITMCE" || label === "OTMPE") return "text-emerald-600 dark:text-emerald-400";
  return "text-red-600 dark:text-red-400";
}

function heatAlpha(value: number | null | undefined, rows: OiVarRow[], field: keyof OiVarRow, kind: TableKind): number {
  if (value == null) return 0;
  const vals = rows
    .map((r) => r[field] as number | null)
    .filter((v): v is number => v != null);
  if (!vals.length) return 0;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  if (max === min) return 0.35;
  const t = (Math.abs(value) - Math.min(...vals.map(Math.abs))) / (Math.max(...vals.map(Math.abs)) - Math.min(...vals.map(Math.abs)) || 1);
  const clamped = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  if (kind === "bottom_chg") return clamped * 0.55;
  return clamped * 0.5;
}

function heatStyle(
  value: number | null | undefined,
  rows: OiVarRow[],
  field: keyof OiVarRow,
  kind: TableKind,
): CSSProperties {
  if (value == null) return {};
  const alpha = heatAlpha(value, rows, field, kind);
  if (kind === "bottom_chg" && value < 0) {
    return { backgroundColor: `rgba(239, 68, 68, ${alpha})` };
  }
  if (kind === "top_chg" && value > 0) {
    return { backgroundColor: `rgba(34, 197, 94, ${alpha})` };
  }
  if (kind === "top_oi") {
    return { backgroundColor: `rgba(59, 130, 246, ${alpha})` };
  }
  if (value < 0) return { backgroundColor: `rgba(239, 68, 68, ${alpha})` };
  return { backgroundColor: `rgba(34, 197, 94, ${alpha})` };
}

function fmtCr(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(2);
}

function fmtPrice(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toFixed(2);
}

function VarTable({
  title,
  rows,
  footer,
  kind,
}: {
  title: string;
  rows: OiVarRow[];
  footer: OiVarFooter;
  kind: TableKind;
}) {
  const heatField: keyof OiVarRow = kind === "top_oi" ? "var_cr" : "var_chg_cr";

  return (
    <Card className="overflow-hidden">
      <CardHeader className="py-3">
        <CardTitle className="text-sm font-semibold tracking-wide">{title}</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="text-xs">
                <TableHead className="w-[72px]">Strike</TableHead>
                <TableHead className="w-[72px]">Money</TableHead>
                <TableHead className="text-right">VWAP</TableHead>
                <TableHead className="text-right">LTP</TableHead>
                <TableHead className="text-right">VAR (Cr)</TableHead>
                <TableHead className="text-right">VAR CHNG</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-6 text-center text-sm text-muted-foreground">
                    No data
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((row) => (
                  <TableRow key={`${row.symbol}-${row.strike}`} className="text-xs font-mono">
                    <TableCell>{row.strike}</TableCell>
                    <TableCell className={moneynessClass(row.moneyness)}>{row.moneyness}</TableCell>
                    <TableCell
                      className="text-right"
                      title={row.vwap_fallback ? "VWAP unavailable — using LTP" : undefined}
                    >
                      {fmtPrice(row.vwap)}
                      {row.vwap_fallback ? "*" : ""}
                    </TableCell>
                    <TableCell className="text-right">{fmtPrice(row.ltp)}</TableCell>
                    <TableCell className="text-right" style={heatStyle(row.var_cr, rows, "var_cr", kind)}>
                      {fmtCr(row.var_cr)}
                    </TableCell>
                    <TableCell className="text-right" style={heatStyle(row.var_chg_cr, rows, heatField, kind)}>
                      {fmtCr(row.var_chg_cr)}
                    </TableCell>
                  </TableRow>
                ))
              )}
              {rows.length > 0 ? (
                <TableRow className="border-t-2 bg-muted/40 text-xs font-mono font-semibold">
                  <TableCell colSpan={4}>Total (top {rows.length})</TableCell>
                  <TableCell className="text-right">{fmtCr(footer.var_cr_total)}</TableCell>
                  <TableCell className="text-right">{fmtCr(footer.var_chg_total)}</TableCell>
                </TableRow>
              ) : null}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

function SideColumn({
  side,
  tables,
  topN,
}: {
  side: "CE" | "PE";
  tables: OiVarSnapshot["calls"];
  topN: number;
}) {
  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-center text-sm font-bold tracking-widest text-muted-foreground">{side}</h2>
      <VarTable
        title={`TOP ${topN} OI`}
        rows={tables.top_oi}
        footer={tables.footer.top_oi}
        kind="top_oi"
      />
      <VarTable
        title={`TOP ${topN} CHNG IN OI`}
        rows={tables.top_chg}
        footer={tables.footer.top_chg}
        kind="top_chg"
      />
      <VarTable
        title={`BOTTOM ${topN} CHNG IN OI`}
        rows={tables.bottom_chg}
        footer={tables.footer.bottom_chg}
        kind="bottom_chg"
      />
    </div>
  );
}

function OiVarPage() {
  const [config, setConfig] = useState<OiVarConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [refreshSec, setRefreshSec] = useState(60);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [snapshot, setSnapshot] = useState<OiVarSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [warming, setWarming] = useState(false);
  const [authError, setAuthError] = useState(false);

  useEffect(() => {
    api.get<OiVarConfig>("/oi-var/config", { silent: true }).then((c) => {
      setConfig(c);
      setRefreshSec(c.refresh_seconds);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    api
      .get<{ expiries: string[] }>(`/options/expiries?underlying=${underlying}`, { silent: true })
      .then((r) => {
        const list = r.expiries ?? [];
        setExpiries(list);
        if (list.length && !expiry) {
          const today = new Date().toISOString().slice(0, 10);
          const nearest = list.find((e) => e >= today) ?? list[list.length - 1];
          setExpiry(nearest);
        }
      })
      .catch(() => setExpiries([]));
  }, [underlying]);

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setAuthError(false);
    const slowTimer = window.setTimeout(() => setWarming(true), 4000);
    try {
      const q = new URLSearchParams({ underlying });
      if (expiry) q.set("expiry", expiry);
      const data = await api.get<OiVarSnapshot>(`/oi-var/snapshot?${q}`);
      setSnapshot(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) {
        setAuthError(true);
      }
    } finally {
      window.clearTimeout(slowTimer);
      setWarming(false);
      setLoading(false);
    }
  }, [underlying, expiry]);

  useEffect(() => {
    if (!expiry) return;
    void fetchSnapshot();
  }, [underlying, expiry]);

  useEffect(() => {
    if (!autoRefresh || !expiry || authError) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnapshot();
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, refreshSec, expiry, authError, fetchSnapshot]);

  const topN = snapshot?.top_n ?? config?.top_n ?? 10;

  const metaLine = useMemo(() => {
    if (!snapshot) return null;
    return (
      <>
        Spot <span className="font-mono font-semibold text-foreground">{snapshot.spot.toFixed(2)}</span>
        {" · "}
        {snapshot.chain_legs_quoted}/{snapshot.chain_legs_total} legs quoted
        {" · "}
        EOD baseline {snapshot.baseline_date}
        {" · "}
        Updated {new Date(snapshot.updated_at).toLocaleTimeString()}
      </>
    );
  }, [snapshot]);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 pb-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">3ST Algo Desk — OI VAR Live</h1>
        <p className="text-sm text-muted-foreground">
          Full-chain Top/Bottom 10 by VAR (Cr) and EOD OI change. Requires Kite login.
        </p>
        {metaLine ? <p className="mt-1 text-xs text-muted-foreground">{metaLine}</p> : null}
      </header>

      {authError && (
        <Card className="border-destructive/50">
          <CardContent className="py-4 text-sm">
            Kite session required.{" "}
            <Link to="/login" className="text-primary underline">
              Log in
            </Link>
          </CardContent>
        </Card>
      )}

      {warming && loading ? (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardContent className="py-3 text-sm text-amber-800 dark:text-amber-200">
            Warming EOD OI baseline cache — first load of the day may take 30–90s…
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Settings</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label>Underlying</Label>
            <Select
              value={underlying}
              onValueChange={(v) => {
                setUnderlying(v as OiUnderlying);
                setExpiry("");
              }}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {UNDERLYINGS.map((u) => (
                  <SelectItem key={u} value={u}>{u}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Expiry</Label>
            <Select value={expiry || undefined} onValueChange={setExpiry}>
              <SelectTrigger><SelectValue placeholder="Select expiry" /></SelectTrigger>
              <SelectContent>
                {expiries.map((e) => (
                  <SelectItem key={e} value={e}>{e}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Refresh (sec)</Label>
            <Select value={String(refreshSec)} onValueChange={(v) => setRefreshSec(Number(v))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {[30, 60, 90, 120].map((n) => (
                  <SelectItem key={n} value={String(n)}>{n}s</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-2 text-sm md:col-span-3">
            <Checkbox checked={autoRefresh} onCheckedChange={(v) => setAutoRefresh(Boolean(v))} />
            Auto-refresh
          </label>
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Button onClick={() => void fetchSnapshot()} disabled={loading || !expiry}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {loading ? "Loading…" : "Refresh now"}
        </Button>
        <Button variant="outline" onClick={() => setAutoRefresh((v) => !v)}>
          {autoRefresh ? <Pause className="mr-2 h-4 w-4" /> : <Play className="mr-2 h-4 w-4" />}
          {autoRefresh ? "Pause auto-refresh" : "Resume auto-refresh"}
        </Button>
      </div>

      {snapshot ? (
        <div className="grid gap-6 lg:grid-cols-2">
          <SideColumn side="CE" tables={snapshot.calls} topN={topN} />
          <SideColumn side="PE" tables={snapshot.puts} topN={topN} />
        </div>
      ) : (
        !loading && (
          <p className="text-sm text-muted-foreground">Select an expiry and refresh to load VAR tables.</p>
        )
      )}

      <p className="text-[10px] text-muted-foreground">
        * VWAP fallback to LTP when session average is unavailable. VAR (Cr) = OI × LTP / 1e7.
        Rankings use LTP-based VAR; VAR CHNG vs previous session EOD OI.
      </p>
    </div>
  );
}

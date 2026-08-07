import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import { ArrowDown, ArrowUp, LayoutGrid, Maximize2, Pause, Play, RefreshCw } from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import type {
  OiUnderlying,
  OiVarConfig,
  OiVarFlowRegimeSide,
  OiVarFooter,
  OiVarRow,
  OiVarSnapshot,
} from "@/lib/types";
import { cn } from "@/lib/utils";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
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

type TableKind = "top_var" | "top_dvar_up" | "top_dvar_dn";
type ArrowDir = "up" | "down" | "flat";
type DvarMode = "oi_mark" | "true";

const FLOW_LABELS: Record<string, string> = {
  long_build: "Long build",
  short_build: "Short build",
  short_cover: "Short cover",
  long_unwind: "Long unwind",
  flat: "Flat",
  unknown: "—",
};

function FlowRegimeCard({ side }: { side: OiVarFlowRegimeSide }) {
  const tone =
    side.regime === "long"
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300"
      : side.regime === "short"
        ? "border-rose-500/40 bg-rose-500/10 text-rose-800 dark:text-rose-300"
        : "border-border bg-muted/30 text-muted-foreground";
  const c = side.counts ?? {};
  return (
    <div className={`rounded-md border px-3 py-2 ${tone}`}>
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-semibold">
          {side.side} · {side.regime.toUpperCase()}
        </p>
        <p className="font-mono text-xs">score {side.score.toLocaleString()}</p>
      </div>
      <p className="mt-1 text-[11px] opacity-80">
        Long {c.long_build ?? 0} build / {c.short_cover ?? 0} cover · Short {c.short_build ?? 0} build /{" "}
        {c.long_unwind ?? 0} unwind
      </p>
    </div>
  );
}

function moneynessClass(label: string): string {
  if (label === "ITMCE" || label === "OTMPE") return "text-emerald-600 dark:text-emerald-400";
  return "text-red-600 dark:text-red-400";
}

function heatAlpha(value: number | null | undefined, rows: OiVarRow[], field: keyof OiVarRow, kind: TableKind): number {
  if (value == null) return 0;
  const vals = rows.map((r) => r[field] as number | null).filter((v): v is number => v != null);
  if (!vals.length) return 0;
  const absMax = Math.max(...vals.map(Math.abs));
  const absMin = Math.min(...vals.map(Math.abs));
  if (absMax === absMin) return 0.35;
  const t = (Math.abs(value) - absMin) / (absMax - absMin || 1);
  const clamped = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
  return kind === "top_dvar_dn" ? clamped * 0.55 : clamped * 0.5;
}

function heatStyle(
  value: number | null | undefined,
  rows: OiVarRow[],
  field: keyof OiVarRow,
  kind: TableKind,
): CSSProperties {
  if (value == null) return {};
  const alpha = heatAlpha(value, rows, field, kind);
  if (kind === "top_dvar_dn" && value < 0) return { backgroundColor: `rgba(239, 68, 68, ${alpha})` };
  if (kind === "top_dvar_up" && value > 0) return { backgroundColor: `rgba(34, 197, 94, ${alpha})` };
  if (kind === "top_var") return { backgroundColor: `rgba(59, 130, 246, ${alpha})` };
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

function fmtOi(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString();
}

function arrowFromValue(value: number | null | undefined): ArrowDir {
  if (value == null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

function ArrowValue({
  value,
  arrow,
  format = fmtCr,
}: {
  value: number | null | undefined;
  arrow?: ArrowDir | null;
  format?: (v: number | null | undefined) => string;
}) {
  const dir = arrow ?? arrowFromValue(value);
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const tone =
    dir === "up"
      ? "text-emerald-600 dark:text-emerald-400"
      : dir === "down"
        ? "text-red-600 dark:text-red-400"
        : "text-foreground";
  const Icon = dir === "up" ? ArrowUp : dir === "down" ? ArrowDown : null;
  return (
    <span className={cn("inline-flex items-center justify-end gap-0.5 font-mono", tone)} title={dir}>
      {Icon ? <Icon className="h-3 w-3 shrink-0" aria-hidden="true" /> : null}
      {format(value)}
    </span>
  );
}

function StatCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "pos" | "neg" | "muted";
}) {
  const color =
    tone === "pos"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "neg"
        ? "text-red-600 dark:text-red-400"
        : "text-foreground";
  return (
    <Card>
      <CardContent className="py-3">
        <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
        <div className={`font-mono text-lg font-semibold ${color}`}>{value}</div>
        {hint ? <div className="text-[10px] text-muted-foreground">{hint}</div> : null}
      </CardContent>
    </Card>
  );
}

function VarTable({
  title,
  rows,
  footer,
  kind,
  atm,
}: {
  title: string;
  rows: OiVarRow[];
  footer: OiVarFooter;
  kind: TableKind;
  atm?: number | null;
}) {
  const heatField: keyof OiVarRow = kind === "top_var" ? "var_cr" : "var_chg_cr";

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
                <TableHead>Strike</TableHead>
                <TableHead>Money</TableHead>
                <TableHead className="text-right">OI</TableHead>
                <TableHead className="text-right">ΔOI</TableHead>
                <TableHead className="text-right">LTP</TableHead>
                <TableHead className="text-right">VAR</TableHead>
                <TableHead className="text-right">ΔVAR</TableHead>
                <TableHead className="text-right">%</TableHead>
                <TableHead>Flow</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="py-6 text-center text-sm text-muted-foreground">
                    No data
                  </TableCell>
                </TableRow>
              ) : (
                rows.map((row) => (
                  <TableRow key={`${row.symbol}-${row.strike}`} className="text-xs font-mono">
                    <TableCell
                      className={cn(
                        row.strike === atm && "font-bold text-primary",
                        row.near_call_wall && "bg-emerald-500/10",
                        row.near_put_wall && "bg-red-500/10",
                        row.near_flip && "ring-1 ring-violet-400/60",
                      )}
                    >
                      {row.strike}
                      {row.near_call_wall ? " ⬆" : ""}
                      {row.near_put_wall ? " ⬇" : ""}
                      {row.near_flip ? " ✦" : ""}
                    </TableCell>
                    <TableCell className={moneynessClass(row.moneyness)}>{row.moneyness}</TableCell>
                    <TableCell className="text-right">{fmtOi(row.oi)}</TableCell>
                    <TableCell className="text-right">
                      <ArrowValue value={row.delta_oi} format={(v) => (v == null ? "—" : fmtOi(v))} />
                    </TableCell>
                    <TableCell className="text-right">
                      <ArrowValue value={row.ltp} arrow={row.ltp_arrow} format={fmtPrice} />
                    </TableCell>
                    <TableCell className="text-right" style={heatStyle(row.var_cr, rows, "var_cr", kind)}>
                      <ArrowValue value={row.var_cr} arrow={row.var_arrow} />
                    </TableCell>
                    <TableCell className="text-right" style={heatStyle(row.var_chg_cr, rows, heatField, kind)}>
                      <ArrowValue value={row.var_chg_cr} arrow={row.var_chg_arrow} />
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {row.pct_side_var != null ? `${row.pct_side_var.toFixed(1)}%` : "—"}
                    </TableCell>
                    <TableCell className="text-[10px] text-muted-foreground">
                      {FLOW_LABELS[row.flow_tag ?? "unknown"] ?? row.flow_tag}
                    </TableCell>
                  </TableRow>
                ))
              )}
              {rows.length > 0 ? (
                <TableRow className="border-t-2 bg-muted/40 text-xs font-mono font-semibold">
                  <TableCell colSpan={5}>Total (top {rows.length})</TableCell>
                  <TableCell className="text-right">{fmtCr(footer.var_cr_total)}</TableCell>
                  <TableCell className="text-right">
                    <ArrowValue value={footer.var_chg_total} />
                  </TableCell>
                  <TableCell colSpan={2} />
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
  atm,
}: {
  side: "CE" | "PE";
  tables: OiVarSnapshot["calls"];
  topN: number;
  atm?: number | null;
}) {
  const topVar = tables.top_var ?? tables.top_oi;
  const up = tables.top_dvar_up ?? tables.top_chg;
  const dn = tables.top_dvar_dn ?? tables.bottom_chg;
  const fTop = tables.footer.top_var ?? tables.footer.top_oi;
  const fUp = tables.footer.top_dvar_up ?? tables.footer.top_chg;
  const fDn = tables.footer.top_dvar_dn ?? tables.footer.bottom_chg;

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-center text-sm font-bold tracking-widest text-muted-foreground">{side}</h2>
      <VarTable title={`${side} · TOP ${topN} VAR`} rows={topVar} footer={fTop} kind="top_var" atm={atm} />
      <VarTable title={`${side} · TOP ${topN} ↑ ΔVAR`} rows={up} footer={fUp} kind="top_dvar_up" atm={atm} />
      <VarTable title={`${side} · TOP ${topN} ↓ ΔVAR`} rows={dn} footer={fDn} kind="top_dvar_dn" atm={atm} />
    </div>
  );
}

function VarProfileChart({ snap }: { snap: OiVarSnapshot }) {
  const data = snap.var_profile ?? [];
  if (!data.length) return null;
  return (
    <ResponsiveContainer width="100%" height={260}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 10 }} />
        <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => Number(v).toFixed(0)} />
        <Tooltip />
        <ReferenceLine x={snap.spot} stroke="#3b82f6" strokeWidth={1.5} />
        {snap.atm_strike != null ? (
          <ReferenceLine x={snap.atm_strike} stroke="#94a3b8" strokeDasharray="3 3" />
        ) : null}
        <Bar dataKey="ce_var" name="CE VAR" fill="#22c55e" stackId="v" />
        <Bar dataKey="pe_var" name="PE VAR" fill="#ef4444" stackId="v" />
        <Line type="monotone" dataKey="net_dvar" name="Net ΔVAR" stroke="#a855f7" strokeWidth={2} dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function HistoryChart({ snap }: { snap: OiVarSnapshot }) {
  const data = useMemo(
    () =>
      (snap.history ?? []).map((h) => {
        const ms = h.ts_ms ?? (h.t ? new Date(h.t).getTime() : NaN);
        return {
          ts: Number.isFinite(ms) ? ms : 0,
          ce: h.ce_var_total,
          pe: h.pe_var_total,
          net: h.net_dvar,
        };
      }),
    [snap.history],
  );
  if (data.length < 2) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        History builds while the desk refreshes during market hours (cash to 15:40 / MCX to 23:30).
      </p>
    );
  }
  return (
    <div className="space-y-1">
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
          <XAxis
            dataKey="ts"
            type="number"
            domain={["dataMin", "dataMax"]}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) =>
              new Date(Number(v)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
            }
          />
          <YAxis tick={{ fontSize: 10 }} />
          <Tooltip
            labelFormatter={(v) =>
              new Date(Number(v)).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })
            }
          />
          <ReferenceLine y={0} stroke="#94a3b8" />
          <Line type="monotone" dataKey="ce" name="CE VAR" stroke="#22c55e" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="pe" name="PE VAR" stroke="#ef4444" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="net" name="Net ΔVAR" stroke="#a855f7" strokeWidth={1.5} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="text-[10px] text-muted-foreground">
        Stops at session close — no post-close flat extension.
      </p>
    </div>
  );
}

function OiVarPage() {
  const [config, setConfig] = useState<OiVarConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const { expiries, loading: expiriesLoading } = useOptionExpiries(underlying);
  const [refreshSec, setRefreshSec] = useState(60);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [dvarMode, setDvarMode] = useState<DvarMode>("oi_mark");
  const [multiExpiry, setMultiExpiry] = useState(false);
  const [gammaContext, setGammaContext] = useState(false);
  const [snapshot, setSnapshot] = useState<OiVarSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [warming, setWarming] = useState(false);
  const [authError, setAuthError] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<OiVarConfig>("/oi-var/config", { silent: true })
      .then((c) => {
        setConfig(c);
        setRefreshSec(c.refresh_seconds);
        if (c.dvar_mode) setDvarMode(c.dvar_mode);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!expiries.length) return;
    setExpiry((current) => {
      if (current && expiries.includes(current)) return current;
      return pickNearestExpiry(expiries) ?? "";
    });
  }, [expiries, underlying]);

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setAuthError(false);
    setFetchError(null);
    const slowTimer = window.setTimeout(() => setWarming(true), 4000);
    try {
      const q = new URLSearchParams({
        underlying,
        dvar_mode: dvarMode,
        multi_expiry: multiExpiry ? "true" : "false",
        gamma_context: gammaContext ? "true" : "false",
      });
      if (expiry) q.set("expiry", expiry);
      const data = await api.get<OiVarSnapshot>(`/oi-var/snapshot?${q}`);
      setSnapshot(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) {
        setAuthError(true);
      } else {
        setFetchError(msg);
      }
      setSnapshot(null);
    } finally {
      window.clearTimeout(slowTimer);
      setWarming(false);
      setLoading(false);
    }
  }, [underlying, expiry, dvarMode, multiExpiry, gammaContext]);

  useEffect(() => {
    if (!expiry) return;
    void fetchSnapshot();
  }, [underlying, expiry, dvarMode, multiExpiry, gammaContext]);

  useEffect(() => {
    if (!autoRefresh || !expiry || authError) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnapshot();
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, refreshSec, expiry, authError, fetchSnapshot]);

  const topN = snapshot?.top_n ?? config?.top_n ?? 10;
  // Prefer API summary; fall back so boards still render on older API processes
  const derivedSummary = useMemo(() => {
    if (snapshot?.summary) return snapshot.summary;
    if (!snapshot) return null;
    const ceRows = snapshot.calls?.top_oi ?? [];
    const peRows = snapshot.puts?.top_oi ?? [];
    const sumVar = (rows: OiVarRow[]) => {
      const vals = rows.map((r) => r.var_cr).filter((v): v is number => v != null);
      return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
    };
    const sumChg = (rows: OiVarRow[]) => {
      const vals = rows.map((r) => r.var_chg_cr).filter((v): v is number => v != null);
      return vals.length ? vals.reduce((a, b) => a + b, 0) : null;
    };
    const ceTot = sumVar(ceRows);
    const peTot = sumVar(peRows);
    const ceD = sumChg(snapshot.calls?.top_chg ?? []);
    const peD = sumChg(snapshot.puts?.top_chg ?? []);
    return {
      ce_var_total: ceTot,
      pe_var_total: peTot,
      pcr_var: ceTot && peTot != null && ceTot > 0 ? Math.round((peTot / ceTot) * 1000) / 1000 : null,
      ce_dvar_total: ceD,
      pe_dvar_total: peD,
      net_dvar: ceD != null || peD != null ? (ceD ?? 0) + (peD ?? 0) : null,
      concentration: { ce_top_share_pct: null, pe_top_share_pct: null },
    };
  }, [snapshot]);

  const metaLine = useMemo(() => {
    if (!snapshot) return null;
    return (
      <>
        Spot <span className="font-mono font-semibold text-foreground">{snapshot.spot.toFixed(2)}</span>
        {snapshot.atm_strike != null ? <> · ATM {snapshot.atm_strike}</> : null}
        {" · "}
        {snapshot.chain_legs_quoted}/{snapshot.chain_legs_total} legs
        {" · "}EOD {snapshot.baseline_date}
        {snapshot.session_open_at ? <> · Session open set</> : null}
        {" · "}
        Updated {new Date(snapshot.updated_at).toLocaleTimeString()}
      </>
    );
  }, [snapshot]);

  return (
    <div className="mx-auto flex max-w-[1500px] flex-col gap-6 pb-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">3ST Algo Desk — OI VAR Live</h1>
        <p className="text-sm text-muted-foreground">
          Top-N VAR / ↑ΔVAR / ↓ΔVAR in ₹ Cr — CE vs PE boards with flow tags and concentration.
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

      {fetchError ? (
        <Card className="border-destructive/50">
          <CardContent className="py-3 text-sm text-destructive">{fetchError}</CardContent>
        </Card>
      ) : null}

      {warming && loading ? (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardContent className="py-3 text-sm text-amber-800 dark:text-amber-200">
            Warming EOD OI baseline / extras — first load may take 30–90s…
          </CardContent>
        </Card>
      ) : null}

      {(snapshot?.alerts?.length ?? 0) > 0 ? (
        <div className="flex flex-wrap gap-2">
          {snapshot!.alerts!.map((a, i) => (
            <Badge
              key={`${a.type}-${i}`}
              variant="outline"
              className={
                a.severity === "alert"
                  ? "border-red-500/60 text-red-600 dark:text-red-400"
                  : "border-amber-500/60"
              }
            >
              {a.message}
            </Badge>
          ))}
        </div>
      ) : null}

      {snapshot?.flow_regime ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Session flow regime (vs open) · shift detector</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <FlowRegimeCard side={snapshot.flow_regime.ce} />
              <FlowRegimeCard side={snapshot.flow_regime.pe} />
            </div>
            {(snapshot.flow_shifts?.length ?? 0) > 0 ? (
              <div className="flex flex-wrap gap-2">
                {snapshot.flow_shifts!.map((s, i) => (
                  <Badge
                    key={`${s.side}-${s.t}-${i}`}
                    variant="outline"
                    className={
                      s.to_regime === "long"
                        ? "border-emerald-500/50 text-emerald-700 dark:text-emerald-300"
                        : "border-rose-500/50 text-rose-700 dark:text-rose-300"
                    }
                  >
                    {s.t
                      ? new Date(s.t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                      : "—"}{" "}
                    · {s.label ?? s.message}
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Shifts appear after CE/PE regime flips hold for 2+ ticks (e.g. CE short→long, PE long→short
                around a spot reversal). Keep the desk refreshing through the session.
              </p>
            )}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Settings</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="flex flex-col gap-1.5">
            <Label>Underlying</Label>
            <Select
              value={underlying}
              onValueChange={(v) => {
                setUnderlying(v as OiUnderlying);
                setExpiry("");
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(config?.underlyings ?? UNDERLYINGS).map((u) => (
                  <SelectItem key={u} value={u}>
                    {u}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Expiry</Label>
            <Select
              value={expiries.length && expiry && expiries.includes(expiry) ? expiry : undefined}
              onValueChange={setExpiry}
            >
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    expiriesLoading ? "Loading…" : expiries.length ? "Select expiry" : "No expiries"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {expiries.map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>ΔVAR mode</Label>
            <Select value={dvarMode} onValueChange={(v) => setDvarMode(v as DvarMode)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="oi_mark">OI-mark (ΔOI × LTP)</SelectItem>
                <SelectItem value="true">True ΔVAR (vs baseline)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Refresh (sec)</Label>
            <Select value={String(refreshSec)} onValueChange={(v) => setRefreshSec(Number(v))}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[30, 60, 90, 120].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}s
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={autoRefresh} onCheckedChange={(v) => setAutoRefresh(Boolean(v))} />
            Auto-refresh
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={multiExpiry} onCheckedChange={(v) => setMultiExpiry(Boolean(v))} />
            Multi-expiry (slower)
          </label>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <Checkbox checked={gammaContext} onCheckedChange={(v) => setGammaContext(Boolean(v))} />
            Gamma walls / flip badges (slower)
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
        <Button variant="secondary" asChild>
          <Link
            to="/widget-desk"
            search={{
              focus: "oi-var",
              full: true,
              underlying,
              expiry: expiry || undefined,
            }}
          >
            <Maximize2 className="mr-2 h-4 w-4" />
            Full view (Widget Desk)
          </Link>
        </Button>
        <Button variant="outline" asChild>
          <Link to="/widget-desk" search={{ focus: "oi-var", underlying, expiry: expiry || undefined }}>
            <LayoutGrid className="mr-2 h-4 w-4" />
            Add to Widget Desk
          </Link>
        </Button>
      </div>

      {snapshot ? (
        <>
          {derivedSummary ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="CE VAR" value={`${fmtCr(derivedSummary.ce_var_total)} Cr`} hint={`Top-10 share ${derivedSummary.concentration?.ce_top_share_pct ?? "—"}%`} tone="pos" />
            <StatCard label="PE VAR" value={`${fmtCr(derivedSummary.pe_var_total)} Cr`} hint={`Top-10 share ${derivedSummary.concentration?.pe_top_share_pct ?? "—"}%`} tone="neg" />
            <StatCard label="PCR-VAR" value={derivedSummary.pcr_var != null ? derivedSummary.pcr_var.toFixed(3) : "—"} hint="PE VAR / CE VAR" tone="muted" />
            <StatCard
              label="Net ΔVAR"
              value={`${fmtCr(derivedSummary.net_dvar)} Cr`}
              hint={`CE ${fmtCr(derivedSummary.ce_dvar_total)} · PE ${fmtCr(derivedSummary.pe_dvar_total)} · mode ${snapshot.dvar_mode ?? dvarMode}`}
              tone={(derivedSummary.net_dvar ?? 0) >= 0 ? "pos" : "neg"}
            />
          </div>
          ) : null}

          {snapshot.gamma_context?.available ? (
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge variant="outline" className="border-emerald-500/50">
                Call wall {snapshot.gamma_context.call_wall ?? "—"}
              </Badge>
              <Badge variant="outline" className="border-red-500/50">
                Put wall {snapshot.gamma_context.put_wall ?? "—"}
              </Badge>
              <Badge variant="outline" className="border-violet-500/50">
                Flip {snapshot.gamma_context.flip_level ?? "—"}
              </Badge>
              <Link to="/gamma-density" className="text-primary underline">
                Open Gamma Density →
              </Link>
            </div>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">VAR profile (CE/PE stack) · Net ΔVAR</CardTitle>
              </CardHeader>
              <CardContent>
                <VarProfileChart snap={snapshot} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Intraday CE/PE VAR · Net ΔVAR</CardTitle>
              </CardHeader>
              <CardContent>
                <HistoryChart snap={snapshot} />
              </CardContent>
            </Card>
          </div>

          {(snapshot.multi_expiry?.length ?? 0) > 0 ? (
            <Card>
              <CardHeader className="py-3">
                <CardTitle className="text-sm">Multi-expiry VAR</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow className="text-xs">
                      <TableHead>Expiry</TableHead>
                      <TableHead className="text-right">CE VAR</TableHead>
                      <TableHead className="text-right">PE VAR</TableHead>
                      <TableHead className="text-right">Top CE</TableHead>
                      <TableHead className="text-right">Top PE</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {snapshot.multi_expiry!.map((m) => (
                      <TableRow key={m.expiry} className="text-xs font-mono">
                        <TableCell>{m.expiry}</TableCell>
                        <TableCell className="text-right">{fmtCr(m.ce_var_total)}</TableCell>
                        <TableCell className="text-right">{fmtCr(m.pe_var_total)}</TableCell>
                        <TableCell className="text-right">
                          {m.top_ce_strike ?? "—"} ({fmtCr(m.top_ce_var)})
                        </TableCell>
                        <TableCell className="text-right">
                          {m.top_pe_strike ?? "—"} ({fmtCr(m.top_pe_var)})
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : null}

          <div className="grid gap-6 lg:grid-cols-2">
            <SideColumn side="CE" tables={snapshot.calls} topN={topN} atm={snapshot.atm_strike} />
            <SideColumn side="PE" tables={snapshot.puts} topN={topN} atm={snapshot.atm_strike} />
          </div>

          <p className="text-[10px] text-muted-foreground">
            VAR (Cr) = OI × mark / 1e7. LTP arrow vs session open; ΔVAR arrow vs ΔOI / ΔVAR sign.
            Session flow regime weights CE/PE tags vs open (Long = build+cover, Short = build shorts+unwind).
            Flow shifts fire when CE/PE long↔short flip holds 2+ ticks — e.g. CE short→long & PE long→short
            around a 10:10-style reversal. Concentration = Top-N share of side total VAR.
          </p>
        </>
      ) : (
        !loading && (
          <p className="text-sm text-muted-foreground">Select an expiry and refresh to load VAR tables.</p>
        )
      )}
    </div>
  );
}

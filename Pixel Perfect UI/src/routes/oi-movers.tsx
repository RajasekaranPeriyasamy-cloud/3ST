import { createFileRoute, Link } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeftRight, Pause, Play, RefreshCw } from "lucide-react";

import { api } from "@/lib/api";
import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import type {
  OiChangeBoardEntry,
  OiMoversConfig,
  OiMoversSnapshot,
  OiUnderlying,
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

export const Route = createFileRoute("/oi-movers")({
  component: OiMoversPage,
});

const UNDERLYINGS: OiUnderlying[] = [
  "NIFTY",
  "BANKNIFTY",
  "SENSEX",
  "CRUDEOIL",
  "CRUDEOILM",
  "NATURALGAS",
];

function formatOiLocale(value: number | null | undefined): string {
  if (value == null) return "N/A";
  return value.toLocaleString();
}

function formatCompactChg(value: number | null | undefined): string {
  if (value == null) return "N/A";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1000) {
    const k = abs / 1000;
    const digits = k >= 100 ? 0 : 1;
    return `${sign}${k.toFixed(digits)}K`;
  }
  return `${sign}${abs.toLocaleString()}`;
}

function formatPctParen(value: number | null | undefined): string {
  if (value == null) return "";
  const sign = value > 0 ? "+" : "";
  return `(${sign}${value.toFixed(0)}%)`;
}

function OiMoversPage() {
  const [config, setConfig] = useState<OiMoversConfig | null>(null);
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState<string>("");
  const { expiries, loading: expiriesLoading } = useOptionExpiries(underlying);
  const [optionsCount, setOptionsCount] = useState(5);
  const [refreshSec, setRefreshSec] = useState(60);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [snapshot, setSnapshot] = useState<OiMoversSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);

  useEffect(() => {
    api
      .get<OiMoversConfig>("/oi-movers/config", { silent: true })
      .then((c) => {
        setConfig(c);
        setOptionsCount(c.options_count);
        setRefreshSec(c.refresh_seconds);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!expiries.length) return;
    setExpiry((current) => {
      if (current && expiries.includes(current)) return current;
      return pickNearestExpiry(expiries, underlying) ?? "";
    });
  }, [expiries, underlying]);

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setAuthError(false);
    try {
      const q = new URLSearchParams({ underlying });
      if (expiry) q.set("expiry", expiry);
      q.set("options_count", String(optionsCount));
      const data = await api.get<OiMoversSnapshot>(`/oi-movers/snapshot?${q}`);
      setSnapshot(data);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("401") || msg.toLowerCase().includes("session")) {
        setAuthError(true);
      }
    } finally {
      setLoading(false);
    }
  }, [underlying, expiry, optionsCount]);

  useEffect(() => {
    if (!expiry) return;
    void fetchSnapshot();
  }, [underlying, expiry, optionsCount]);

  useEffect(() => {
    if (!autoRefresh || !expiry || authError) return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void fetchSnapshot();
    }, refreshSec * 1000);
    return () => clearInterval(id);
  }, [autoRefresh, refreshSec, expiry, authError, fetchSnapshot]);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">
          Highest OI Increase / Decrease
        </h1>
        <p className="text-sm text-muted-foreground">
          CE and PE ranked by OI change from session open (or previous-day close) to current.
        </p>
      </header>

      {authError && (
        <Card className="border-bear/50">
          <CardContent className="py-4 text-sm">
            Kite session required.{" "}
            <Link to="/login" className="text-primary underline">
              Log in
            </Link>
          </CardContent>
        </Card>
      )}

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
                    {u === "CRUDEOIL"
                      ? "CRUDEOIL (MCX)"
                      : u === "CRUDEOILM"
                        ? "CRUDEOILM (MCX)"
                        : u === "NATURALGAS"
                          ? "NATURALGAS (MCX)"
                          : u}
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
                    expiriesLoading
                      ? "Loading expiries…"
                      : expiries.length
                        ? "Select expiry"
                        : "No expiries"
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
            <Label>Strikes each side</Label>
            <Select
              value={String(optionsCount)}
              onValueChange={(v) => setOptionsCount(Number(v))}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[2, 3, 4, 5, 7, 10].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Refresh (sec)</Label>
            <Select
              value={String(refreshSec)}
              onValueChange={(v) => setRefreshSec(Number(v))}
            >
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
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <Checkbox
              checked={autoRefresh}
              onCheckedChange={(v) => setAutoRefresh(Boolean(v))}
            />
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

      {snapshot && (
        <>
          <p className="text-sm text-muted-foreground">
            <ArrowLeftRight className="mr-1 inline h-4 w-4" />
            {snapshot.underlying} · expiry {snapshot.expiry} · spot {snapshot.spot.toFixed(2)} · ATM{" "}
            {snapshot.atm_strike} · updated {new Date(snapshot.updated_at).toLocaleTimeString()}
            {snapshot.baseline ? (
              <>
                {" · "}
                Prev: open {snapshot.baseline.open_count} · prior-day close{" "}
                {snapshot.baseline.prev_close_count}
              </>
            ) : null}
            {snapshot.spot_warning ? ` · ${snapshot.spot_warning}` : ""}
          </p>

          <OiChangeBoardsSection
            underlying={snapshot.underlying}
            boards={snapshot.change_boards}
          />
        </>
      )}
    </div>
  );
}

function OiChangeBoardsSection({
  underlying,
  boards,
}: {
  underlying: string;
  boards?: OiMoversSnapshot["change_boards"];
}) {
  const set = boards?.session ?? (boards ? Object.values(boards)[0] : undefined);

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-base font-semibold tracking-tight">
          Highest OI Increase / Decrease · {underlying}
        </h2>
        <p className="text-xs text-muted-foreground">
          Change = Curr − Open/PD. Open/PD is session open OI (O), else previous-day closing OI (PD).
        </p>
      </div>

      {!set ? (
        <Card>
          <CardContent className="py-6 text-sm text-muted-foreground">
            No change-board data yet (need open or prior-day OI baseline).
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <ChangeBoardCard
            title={`Highest OI Increase · ${underlying}`}
            tone="increase"
            absoluteRows={set.increase_abs}
            percentRows={set.increase_pct}
          />
          <ChangeBoardCard
            title={`Highest OI Decrease · ${underlying}`}
            tone="decrease"
            absoluteRows={set.decrease_abs}
            percentRows={set.decrease_pct}
          />
        </div>
      )}
    </div>
  );
}

function ChangeBoardCard({
  title,
  tone,
  absoluteRows,
  percentRows,
}: {
  title: string;
  tone: "increase" | "decrease";
  absoluteRows: OiChangeBoardEntry[];
  percentRows: OiChangeBoardEntry[];
}) {
  const shell =
    tone === "increase"
      ? "border-emerald-500/40 bg-emerald-50/60 dark:bg-emerald-950/20"
      : "border-rose-500/40 bg-rose-50/60 dark:bg-rose-950/20";
  const heading =
    tone === "increase"
      ? "text-emerald-700 dark:text-emerald-400"
      : "text-rose-700 dark:text-rose-400";

  return (
    <Card className={shell}>
      <CardHeader className="pb-2">
        <CardTitle className={`text-sm ${heading}`}>{title}</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 sm:grid-cols-2">
        <ChangeBoardTable caption="Absolute" rows={absoluteRows} tone={tone} mode="abs" />
        <ChangeBoardTable caption="Percent" rows={percentRows} tone={tone} mode="pct" />
      </CardContent>
    </Card>
  );
}

function ChangeBoardSideBlock({
  label,
  rows,
  tone,
  mode,
}: {
  label: "CE" | "PE";
  rows: OiChangeBoardEntry[];
  tone: "increase" | "decrease";
  mode: "abs" | "pct";
}) {
  const barClass = tone === "increase" ? "bg-emerald-500/80" : "bg-rose-500/80";
  const rowBg =
    tone === "increase"
      ? "bg-emerald-100/50 dark:bg-emerald-900/20"
      : "bg-rose-100/50 dark:bg-rose-900/20";
  const chgTone =
    tone === "increase"
      ? "text-emerald-700 dark:text-emerald-400"
      : "text-rose-700 dark:text-rose-400";
  const sideTone =
    label === "CE" ? "text-amber-700 dark:text-amber-400" : "text-sky-700 dark:text-sky-400";

  return (
    <div className="space-y-1">
      <p className={`text-[10px] font-bold uppercase tracking-wider ${sideTone}`}>{label}</p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="h-7 px-2 text-[10px]">Contract</TableHead>
            <TableHead
              className="h-7 px-2 text-right text-[10px]"
              title="Session open OI, else previous-day closing OI"
            >
              Open/PD
            </TableHead>
            <TableHead className="h-7 px-2 text-right text-[10px]">Curr</TableHead>
            <TableHead className="h-7 px-2 text-right text-[10px]">Change</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={4} className="px-2 py-2 text-[11px] text-muted-foreground">
                No moves
              </TableCell>
            </TableRow>
          ) : (
            rows.map((row) => (
              <TableRow key={`${mode}-${label}-${row.contract}`} className={rowBg}>
                <TableCell className="px-2 py-1.5 font-mono text-[11px] leading-tight">
                  {row.contract}
                </TableCell>
                <TableCell
                  className="px-2 py-1.5 text-right font-mono text-[11px]"
                  title={
                    row.prev_oi_source === "open"
                      ? "Session open OI"
                      : row.prev_oi_source === "prev_close"
                        ? "Previous-day closing OI"
                        : undefined
                  }
                >
                  {formatOiLocale(row.prev_oi)}
                  {row.prev_oi_source === "open" ? (
                    <span className="ml-0.5 text-[9px] text-muted-foreground">O</span>
                  ) : row.prev_oi_source === "prev_close" ? (
                    <span className="ml-0.5 text-[9px] text-muted-foreground">PD</span>
                  ) : null}
                </TableCell>
                <TableCell className="px-2 py-1.5 text-right font-mono text-[11px]">
                  {formatOiLocale(row.curr_oi)}
                </TableCell>
                <TableCell className="px-2 py-1.5 text-right">
                  <div className={`font-mono text-[11px] font-semibold ${chgTone}`}>
                    {formatCompactChg(row.abs_chg)}{" "}
                    <span className="font-normal opacity-80">{formatPctParen(row.pct_chg)}</span>
                  </div>
                  <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-sm bg-muted/60">
                    <div
                      className={`h-full rounded-sm ${barClass}`}
                      style={{ width: `${Math.min(100, Math.max(0, row.bar_pct))}%` }}
                    />
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

function ChangeBoardTable({
  caption,
  rows,
  tone,
  mode,
}: {
  caption: string;
  rows: OiChangeBoardEntry[];
  tone: "increase" | "decrease";
  mode: "abs" | "pct";
}) {
  const ceRows = rows.filter((r) => r.option_type === "CE");
  const peRows = rows.filter((r) => r.option_type === "PE");

  return (
    <div className="space-y-3">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {caption}
      </p>
      <ChangeBoardSideBlock label="CE" rows={ceRows} tone={tone} mode={mode} />
      <ChangeBoardSideBlock label="PE" rows={peRows} tone={tone} mode={mode} />
    </div>
  );
}

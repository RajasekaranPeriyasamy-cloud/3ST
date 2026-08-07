import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { CandlestickChart, Loader2 } from "lucide-react";

import { StraddleWatchChart } from "@/components/straddle/StraddleWatchChart";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { pickNearestExpiry, useOptionExpiries } from "@/hooks/useOptionExpiries";
import { api } from "@/lib/api";
import type {
  OiUnderlying,
  StraddleWatchRange,
  StraddleWatchSnapshot,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/straddle-watch")({
  component: StraddleWatchPage,
});

const UNDERLYINGS: OiUnderlying[] = [
  "NIFTY",
  "BANKNIFTY",
  "SENSEX",
  "CRUDEOIL",
  "CRUDEOILM",
  "NATURALGAS",
];

const RANGES: StraddleWatchRange[] = ["1D", "5D", "30D"];

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function fmtExpiryLabel(iso: string): string {
  // 2026-08-11 -> 11AUG26
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  const day = String(d.getUTCDate()).padStart(2, "0");
  const mon = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" }).toUpperCase();
  const yy = String(d.getUTCFullYear()).slice(-2);
  return `${day}${mon}${yy}`;
}

function ChangeTone({
  value,
  pct,
}: {
  value: number | null | undefined;
  pct?: number | null;
}) {
  if (value == null) return <span className="text-slate-500">—</span>;
  const up = value >= 0;
  return (
    <span className={up ? "text-emerald-600" : "text-rose-600"}>
      {up ? "▲" : "▼"} {fmt(Math.abs(value))}
      {pct != null ? ` (${fmt(Math.abs(pct))}%)` : ""}
    </span>
  );
}

function StraddleWatchPage() {
  const [underlying, setUnderlying] = useState<OiUnderlying>("NIFTY");
  const [expiry, setExpiry] = useState("");
  const [callStrike, setCallStrike] = useState("");
  const [putStrike, setPutStrike] = useState("");
  const [range, setRange] = useState<StraddleWatchRange>("1D");
  const [strikes, setStrikes] = useState<number[]>([]);
  const [snapshot, setSnapshot] = useState<StraddleWatchSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [chainLoading, setChainLoading] = useState(false);
  const [authError, setAuthError] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { expiries, loading: expiriesLoading } = useOptionExpiries(underlying);

  useEffect(() => {
    if (!expiries.length) return;
    setExpiry((prev) => (prev && expiries.includes(prev) ? prev : pickNearestExpiry(expiries, underlying) || ""));
  }, [expiries, underlying]);

  useEffect(() => {
    if (!underlying || !expiry) {
      setStrikes([]);
      return;
    }
    let cancelled = false;
    setChainLoading(true);
    api
      .get<{ strikes?: Array<{ strike: number }> }>(
        `/options/chain?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}`,
        { silent: true },
      )
      .then((res) => {
        if (cancelled) return;
        const list = (res.strikes ?? []).map((s) => Number(s.strike)).filter((n) => !Number.isNaN(n));
        setStrikes(list);
        setCallStrike((prev) => {
          if (prev && list.includes(Number(prev))) return prev;
          // ATM-ish middle strike
          const mid = list[Math.floor(list.length / 2)];
          return mid != null ? String(mid) : "";
        });
        setPutStrike((prev) => {
          if (prev && list.includes(Number(prev))) return prev;
          const mid = list[Math.floor(list.length / 2)];
          return mid != null ? String(mid) : "";
        });
      })
      .catch(() => {
        if (!cancelled) setStrikes([]);
      })
      .finally(() => {
        if (!cancelled) setChainLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [underlying, expiry]);

  const canFetch = useMemo(
    () => Boolean(underlying && expiry && callStrike && putStrike),
    [underlying, expiry, callStrike, putStrike],
  );

  async function loadChart(nextRange: StraddleWatchRange = range) {
    if (!canFetch) return;
    setLoading(true);
    setError(null);
    setAuthError(false);
    try {
      const qs = new URLSearchParams({
        underlying,
        expiry,
        call_strike: callStrike,
        put_strike: putStrike,
        range: nextRange,
      });
      const snap = await api.get<StraddleWatchSnapshot>(`/straddle-watch/snapshot?${qs}`, {
        silent: true,
      });
      setSnapshot(snap);
      setRange(nextRange);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.toLowerCase().includes("session") || msg.includes("401")) {
        setAuthError(true);
      }
      setError(msg);
      setSnapshot(null);
    } finally {
      setLoading(false);
    }
  }

  const summary = snapshot?.summary;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 bg-[#f7f8fa] p-3 text-slate-800 md:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <CandlestickChart className="h-5 w-5 text-sky-700" />
          <h1 className="text-lg font-semibold tracking-tight">Straddle Watch</h1>
          <span className="text-xs text-slate-500">Latest · read-only</span>
        </div>
        {authError ? (
          <Link to="/login" className="text-sm text-sky-700 underline">
            Kite login required
          </Link>
        ) : null}
      </div>

      <div className="flex flex-col gap-3 rounded-sm border border-slate-200 bg-white px-3 py-3 shadow-sm lg:flex-row lg:items-start lg:justify-between">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex items-center gap-3 pb-1 text-sm">
            <label className="flex items-center gap-1.5">
              <input type="radio" checked readOnly className="accent-sky-600" />
              Latest
            </label>
            <label className="flex items-center gap-1.5 text-slate-400">
              <input type="radio" disabled className="accent-slate-400" />
              Historical
            </label>
          </div>

          <div className="space-y-1">
            <Label className="text-[11px] uppercase tracking-wide text-slate-500">Select Symbol</Label>
            <Select
              value={underlying}
              onValueChange={(v) => {
                setUnderlying(v as OiUnderlying);
                setExpiry("");
                setSnapshot(null);
              }}
            >
              <SelectTrigger className="h-9 w-[140px] bg-white">
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
          </div>

          <div className="space-y-1">
            <Label className="text-[11px] uppercase tracking-wide text-slate-500">Expiry</Label>
            <Select
              value={expiry}
              onValueChange={(v) => {
                setExpiry(v);
                setSnapshot(null);
              }}
              disabled={expiriesLoading || !expiries.length}
            >
              <SelectTrigger className="h-9 w-[130px] bg-white">
                <SelectValue placeholder={expiriesLoading ? "…" : "Expiry"} />
              </SelectTrigger>
              <SelectContent>
                {expiries.map((e) => (
                  <SelectItem key={e} value={e}>
                    {fmtExpiryLabel(e)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1">
            <Label className="text-[11px] uppercase tracking-wide text-slate-500">Call Strike</Label>
            <Select
              value={callStrike}
              onValueChange={setCallStrike}
              disabled={chainLoading || !strikes.length}
            >
              <SelectTrigger className="h-9 w-[120px] bg-white">
                <SelectValue placeholder="Call" />
              </SelectTrigger>
              <SelectContent>
                {strikes.map((s) => (
                  <SelectItem key={`c-${s}`} value={String(s)}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1">
            <Label className="text-[11px] uppercase tracking-wide text-slate-500">Put Strike</Label>
            <Select
              value={putStrike}
              onValueChange={setPutStrike}
              disabled={chainLoading || !strikes.length}
            >
              <SelectTrigger className="h-9 w-[120px] bg-white">
                <SelectValue placeholder="Put" />
              </SelectTrigger>
              <SelectContent>
                {strikes.map((s) => (
                  <SelectItem key={`p-${s}`} value={String(s)}>
                    {s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Button
            className="h-9 bg-sky-600 px-4 font-semibold tracking-wide text-white hover:bg-sky-700"
            disabled={!canFetch || loading}
            onClick={() => void loadChart(range)}
          >
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            SHOW CHART
          </Button>
        </div>

        <div className="min-w-[280px] space-y-0.5 text-right text-[12px] leading-relaxed lg:pt-1">
          <div className="font-medium">
            {summary?.fut_symbol ?? "—"}{" "}
            <span className="font-semibold">{fmt(summary?.fut_ltp)}</span>{" "}
            <ChangeTone value={summary?.fut_chg} pct={summary?.fut_chg_pct} />{" "}
            <span className="text-slate-500">({summary?.asof ?? "—"})</span>
          </div>
          <div>
            Fair Price <span className="font-semibold">{fmt(summary?.fair_price)}</span>{" "}
            <ChangeTone value={summary?.fair_chg} pct={summary?.fair_chg_pct} />{" "}
            Lot Size <span className="font-semibold">{summary?.lot_size ?? "—"}</span>
          </div>
          <div className="text-slate-700">
            IV: {fmt(summary?.iv)} &nbsp; IVR: {fmt(summary?.ivr)} &nbsp; IVP: {fmt(summary?.ivp)}{" "}
            &nbsp; Max Pain: {summary?.max_pain ?? "—"} &nbsp; PCR: {fmt(summary?.pcr)}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1 border-b border-slate-200">
        {RANGES.map((r) => (
          <button
            key={r}
            type="button"
            className={cn(
              "px-3 py-1.5 text-sm font-medium",
              range === r
                ? "border-b-2 border-sky-600 text-sky-700"
                : "text-slate-500 hover:text-slate-800",
            )}
            disabled={!canFetch || loading}
            onClick={() => void loadChart(r)}
          >
            {r}
          </button>
        ))}
      </div>

      {error ? (
        <div className="rounded-sm border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          {error}
        </div>
      ) : null}

      <StraddleWatchChart snapshot={snapshot} loading={loading} />
    </div>
  );
}

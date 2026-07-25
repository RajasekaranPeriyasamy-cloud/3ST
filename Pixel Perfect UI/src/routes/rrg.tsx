import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, RotateCcw, ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { api } from "@/lib/api";
import type { FpiConfluence, RrgConfig, RrgQuadrant, RrgSnapshot, RrgSymbolRow } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
import { Badge } from "@/components/ui/badge";

export const Route = createFileRoute("/rrg")({
  component: RrgPage,
});

const QUADRANT_LABEL: Record<string, string> = {
  leading: "Leading",
  weakening: "Weakening",
  lagging: "Lagging",
  improving: "Improving",
};

const QUADRANT_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  leading: "default",
  improving: "secondary",
  weakening: "outline",
  lagging: "destructive",
};

/** RRG rotation order: leading → improving → weakening → lagging */
const QUADRANT_ORDER: Record<RrgQuadrant, number> = {
  leading: 0,
  improving: 1,
  weakening: 2,
  lagging: 3,
};

type SortKey = "quadrant" | "symbol" | "rs" | "momentum" | "fpi" | "confluence";

type ConfluenceFilter = FpiConfluence | "all";

const CONFLUENCE_LABEL: Record<FpiConfluence, string> = {
  aligned: "Aligned",
  divergence: "Divergence",
  watch: "Watch",
  contrarian: "Contrarian",
  neutral: "Neutral",
  "n/a": "—",
};

const CONFLUENCE_ORDER: Record<FpiConfluence, number> = {
  aligned: 0,
  watch: 1,
  contrarian: 2,
  divergence: 3,
  neutral: 4,
  "n/a": 5,
};

const CONFLUENCE_VARIANT: Record<
  FpiConfluence,
  "default" | "secondary" | "destructive" | "outline"
> = {
  aligned: "default",
  watch: "secondary",
  contrarian: "outline",
  divergence: "destructive",
  neutral: "outline",
  "n/a": "outline",
};

function formatFpiCr(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function SortIcon({ active, dir }: { active: boolean; dir: "asc" | "desc" }) {
  if (!active) return <ArrowUpDown className="ml-1 inline h-3.5 w-3.5 opacity-40" />;
  return dir === "asc" ? (
    <ArrowUp className="ml-1 inline h-3.5 w-3.5" />
  ) : (
    <ArrowDown className="ml-1 inline h-3.5 w-3.5" />
  );
}

function sortRrgRows(
  rows: RrgSymbolRow[],
  sortBy: SortKey,
  sortDir: "asc" | "desc",
): RrgSymbolRow[] {
  const dir = sortDir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    let cmp = 0;
    switch (sortBy) {
      case "quadrant":
        cmp =
          (QUADRANT_ORDER[a.quadrant] ?? 99) - (QUADRANT_ORDER[b.quadrant] ?? 99);
        if (cmp === 0) cmp = a.label.localeCompare(b.label);
        break;
      case "symbol":
        cmp = a.label.localeCompare(b.label);
        break;
      case "rs":
        cmp = a.head.rs - b.head.rs;
        break;
      case "momentum":
        cmp = a.head.momentum - b.head.momentum;
        break;
      case "fpi":
        cmp = (a.fpi?.net_equity_inr ?? -Infinity) - (b.fpi?.net_equity_inr ?? -Infinity);
        break;
      case "confluence":
        cmp =
          (CONFLUENCE_ORDER[a.fpi?.confluence ?? "n/a"] ?? 99) -
          (CONFLUENCE_ORDER[b.fpi?.confluence ?? "n/a"] ?? 99);
        if (cmp === 0) cmp = a.label.localeCompare(b.label);
        break;
    }
    return cmp * dir;
  });
}

function RrgTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: { payload: { label: string; rs: number; momentum: number; date?: string } }[];
}) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="font-semibold">{p.label}</div>
      {p.date && <div className="text-muted-foreground">{p.date}</div>}
      <div>RS: {p.rs.toFixed(2)}</div>
      <div>Momentum: {p.momentum.toFixed(2)}</div>
    </div>
  );
}

function RrgPage() {
  const [config, setConfig] = useState<RrgConfig | null>(null);
  const [benchmark, setBenchmark] = useState("NIFTY50");
  const [symbolsText, setSymbolsText] = useState("");
  const [snapshot, setSnapshot] = useState<RrgSnapshot | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showTails, setShowTails] = useState(true);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState(false);
  const [initialLoadDone, setInitialLoadDone] = useState(false);
  const [sortBy, setSortBy] = useState<SortKey>("quadrant");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [quadrantFilter, setQuadrantFilter] = useState<RrgQuadrant | "all">("all");
  const [confluenceFilter, setConfluenceFilter] = useState<ConfluenceFilter>("all");
  const [fpiPeriod, setFpiPeriod] = useState("period2");
  const [fpiRefreshing, setFpiRefreshing] = useState(false);

  const loadSnapshot = useCallback(
    async (overrides?: {
      benchmark?: string;
      symbolsText?: string;
      fpiPeriod?: string;
    }) => {
      const bm = overrides?.benchmark ?? benchmark;
      const text = overrides?.symbolsText ?? symbolsText;
      const fp = overrides?.fpiPeriod ?? fpiPeriod;
      const syms = text
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (!syms.length) {
        toast.error("Add at least one symbol or choose a preset");
        return;
      }
      setLoading(true);
      setAuthError(false);
      try {
        const q = new URLSearchParams({
          benchmark: bm,
          symbols: syms.join(","),
          include_fpi: "true",
          fpi_period: fp,
        });
        const data = await api.get<RrgSnapshot>(`/rrg/snapshot?${q.toString()}`);
        setSnapshot(data);
        setSelected(data.symbols[0]?.symbol ?? null);
        if (data.errors?.length) {
          toast.warning(`${data.errors.length} symbol(s) skipped — see table`);
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : "RRG load failed";
        if (/login|session|authenticated|api_key|access_token/i.test(msg)) setAuthError(true);
        toast.error(msg);
      } finally {
        setLoading(false);
      }
    },
    [benchmark, symbolsText, fpiPeriod],
  );

  useEffect(() => {
    let cancelled = false;
    api
      .get<RrgConfig>("/rrg/config", { silent: true })
      .then((c) => {
        if (cancelled) return;
        setConfig(c);
        const preset =
          c.presets.find((p) => p.id === "sector_rotation") ?? c.presets[0];
        if (!preset) return;
        const bm = String(preset.benchmark);
        const syms = preset.symbols.join(",");
        setBenchmark(bm);
        setSymbolsText(syms);
        const fp = c.fpi?.default_period ?? "period2";
        setFpiPeriod(fp);
        void loadSnapshot({ benchmark: bm, symbolsText: syms, fpiPeriod: fp });
        setInitialLoadDone(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // Initial sector-rotation load once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const headPoints = useMemo(
    () =>
      (snapshot?.symbols ?? []).map((s) => ({
        label: s.label,
        symbol: s.symbol,
        rs: s.head.rs,
        momentum: s.head.momentum,
        date: s.head.date,
        fill: s.color,
      })),
    [snapshot],
  );

  const selectedRow: RrgSymbolRow | undefined = snapshot?.symbols.find(
    (s) => s.symbol === selected,
  );

  const sortedSymbols = useMemo(() => {
    const rows = snapshot?.symbols ?? [];
    let filtered =
      quadrantFilter === "all"
        ? rows
        : rows.filter((r) => r.quadrant === quadrantFilter);
    if (confluenceFilter !== "all") {
      filtered = filtered.filter((r) => r.fpi?.confluence === confluenceFilter);
    }
    return sortRrgRows(filtered, sortBy, sortDir);
  }, [snapshot?.symbols, sortBy, sortDir, quadrantFilter, confluenceFilter]);

  function toggleSort(key: SortKey) {
    if (sortBy === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(key);
      setSortDir(key === "symbol" || key === "quadrant" || key === "confluence" ? "asc" : "desc");
    }
  }

  async function refreshFpiData() {
    setFpiRefreshing(true);
    try {
      await api.post("/rrg/fpi/refresh");
      toast.success("FPI data refreshed from NSDL");
      await loadSnapshot();
    } catch {
      /* handled */
    } finally {
      setFpiRefreshing(false);
    }
  }

  const fpiMeta = snapshot?.fpi;
  const fpiPeriodLabel =
    fpiPeriod === "period1"
      ? fpiMeta?.period1_label
      : fpiPeriod === "month_total"
        ? "Month total"
        : fpiMeta?.period2_label;

  const bounds = snapshot?.bounds ?? { x_min: 94, x_max: 106, y_min: 94, y_max: 106 };

  function applyPreset(id: string) {
    const preset = config?.presets.find((p) => p.id === id);
    if (!preset) return;
    const bm = String(preset.benchmark);
    const syms = preset.symbols.join(",");
    setBenchmark(bm);
    setSymbolsText(syms);
    void loadSnapshot({ benchmark: bm, symbolsText: syms });
  }

  const regime = snapshot?.regime;

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Relative Rotation Graph</h1>
          <p className="text-sm text-muted-foreground">
            Weekly sector rotation vs benchmark — RRG-Lite parity, Kite daily data
          </p>
        </div>
        <Button onClick={() => void loadSnapshot()} disabled={loading}>
          <RefreshCw className={cn("mr-2 h-4 w-4", loading && "animate-spin")} />
          Refresh
        </Button>
      </div>

      {regime && (
        <div className="flex flex-wrap gap-2">
          {(Object.keys(QUADRANT_LABEL) as Array<keyof typeof QUADRANT_LABEL>).map((q) => (
            <Badge key={q} variant={QUADRANT_VARIANT[q] ?? "outline"}>
              {QUADRANT_LABEL[q]}: {regime[q] ?? 0}
            </Badge>
          ))}
        </div>
      )}

      {fpiMeta?.ok && (
        <Card className={cn(fpiMeta.stale && "border-amber-500/40 bg-amber-500/5")}>
          <CardContent className="flex flex-wrap items-center justify-between gap-3 py-3">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>
                <strong className="text-foreground">FPI overlay</strong> — NSDL sector equity net
                {fpiMeta.as_of ? ` · as of ${fpiMeta.as_of}` : ""}
                {fpiPeriodLabel ? ` · ${fpiPeriodLabel}` : ""}
              </span>
              {fpiMeta.stale && (
                <Badge variant="outline" className="border-amber-500/50 text-amber-600">
                  Cached / seed data
                </Badge>
              )}
              <span>{fpiMeta.mapped_sectors ?? 0} sectors mapped to RRG</span>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              disabled={fpiRefreshing || loading}
              onClick={() => void refreshFpiData()}
            >
              <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", fpiRefreshing && "animate-spin")} />
              Refresh FPI
            </Button>
          </CardContent>
        </Card>
      )}

      {snapshot?.fpi?.ok === false && (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardContent className="py-3 text-xs text-muted-foreground">
            FPI overlay unavailable: {snapshot.fpi.error}
          </CardContent>
        </Card>
      )}

      {authError && (
        <Card className="border-amber-500/50 bg-amber-500/5">
          <CardContent className="py-3 text-sm">
            Kite login required — open Settings and complete auth, then refresh.
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Setup</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Benchmark</Label>
              <Select value={benchmark} onValueChange={setBenchmark}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(config?.benchmarks ?? [{ id: "NIFTY50", label: "NIFTY 50" }]).map((b) => (
                    <SelectItem key={b.id} value={b.id}>
                      {b.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Symbols (equities or sector ids)</Label>
              <Textarea
                rows={6}
                value={symbolsText}
                onChange={(e) => setSymbolsText(e.target.value)}
                placeholder="NIFTY_IT,NIFTY_PHARMA or RELIANCE,TCS"
                className="font-mono text-xs"
              />
              {config?.sectors?.length ? (
                <p className="text-xs text-muted-foreground">
                  {config.sectors.length} NSE sector indices configured — use preset or ids like{" "}
                  <code className="rounded bg-muted px-1">NIFTY_IT</code>
                </p>
              ) : null}
            </div>

            {config?.presets?.length ? (
              <div className="flex flex-wrap gap-2">
                {config.presets.map((p) => (
                  <Button
                    key={p.id}
                    variant={p.id === "sector_rotation" ? "default" : "outline"}
                    size="sm"
                    onClick={() => applyPreset(p.id)}
                  >
                    {p.label}
                  </Button>
                ))}
              </div>
            ) : null}

            <div className="flex gap-2">
              <Button className="flex-1" onClick={() => void loadSnapshot()} disabled={loading}>
                Run RRG
              </Button>
              <Button
                variant="outline"
                size="icon"
                title="Toggle all tails"
                onClick={() => setShowTails((v) => !v)}
              >
                <RotateCcw className="h-4 w-4" />
              </Button>
            </div>

            {snapshot && (
              <p className="text-xs text-muted-foreground">
                As of {snapshot.as_of} · {snapshot.symbols.length} plotted · window{" "}
                {snapshot.params.window} · period {snapshot.params.period}
              </p>
            )}
          </CardContent>
        </Card>

        <Card className="min-h-[480px]">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              {snapshot?.benchmark.label ?? "RRG Chart"}
            </CardTitle>
          </CardHeader>
          <CardContent className="h-[440px]">
            {!snapshot?.symbols.length ? (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                {loading || !initialLoadDone
                  ? "Loading weekly history from Kite…"
                  : "No data — run RRG"}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart margin={{ top: 12, right: 24, bottom: 12, left: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.25} />
                  <XAxis
                    type="number"
                    dataKey="rs"
                    domain={[bounds.x_min, bounds.x_max]}
                    name="RS Ratio"
                    tick={{ fontSize: 11 }}
                    label={{ value: "RS Ratio", position: "insideBottom", offset: -4, fontSize: 11 }}
                  />
                  <YAxis
                    type="number"
                    dataKey="momentum"
                    domain={[bounds.y_min, bounds.y_max]}
                    name="RS Momentum"
                    tick={{ fontSize: 11 }}
                    label={{
                      value: "RS Momentum",
                      angle: -90,
                      position: "insideLeft",
                      fontSize: 11,
                    }}
                  />
                  <ZAxis range={[80, 80]} />
                  <ReferenceLine x={100} stroke="#333" strokeDasharray="4 4" strokeWidth={0.6} />
                  <ReferenceLine y={100} stroke="#333" strokeDasharray="4 4" strokeWidth={0.6} />
                  <ReferenceArea
                    x1={Math.max(bounds.x_min, 93.5)}
                    x2={100}
                    y1={100}
                    y2={Math.min(bounds.y_max, 106.5)}
                    fill="#b1ebff"
                    fillOpacity={0.35}
                    ifOverflow="extendDomain"
                  />
                  <ReferenceArea
                    x1={100}
                    x2={Math.min(bounds.x_max, 106.5)}
                    y1={100}
                    y2={Math.min(bounds.y_max, 106.5)}
                    fill="#bdffc9"
                    fillOpacity={0.35}
                    ifOverflow="extendDomain"
                  />
                  <ReferenceArea
                    x1={100}
                    x2={Math.min(bounds.x_max, 106.5)}
                    y1={Math.max(bounds.y_min, 93.5)}
                    y2={100}
                    fill="#fff7b8"
                    fillOpacity={0.35}
                    ifOverflow="extendDomain"
                  />
                  <ReferenceArea
                    x1={Math.max(bounds.x_min, 93.5)}
                    x2={100}
                    y1={Math.max(bounds.y_min, 93.5)}
                    y2={100}
                    fill="#ffb9c6"
                    fillOpacity={0.35}
                    ifOverflow="extendDomain"
                  />
                  <Tooltip content={<RrgTooltip />} />
                  {showTails &&
                    snapshot.symbols.map((s) => (
                      <Line
                        key={`tail-${s.symbol}`}
                        data={s.tail.map((t) => ({
                          rs: t.rs,
                          momentum: t.momentum,
                          label: s.label,
                          date: t.date,
                        }))}
                        type="monotone"
                        dataKey="momentum"
                        stroke={s.color}
                        strokeWidth={selected && selected !== s.symbol ? 0.6 : selected === s.symbol ? 2.5 : 1.4}
                        strokeOpacity={selected && selected !== s.symbol ? 0.25 : 0.9}
                        dot={selected === s.symbol ? { r: 3, fill: s.color } : false}
                        isAnimationActive={false}
                      />
                    ))}
                  {selectedRow && showTails && (
                    <Line
                      data={selectedRow.tail.map((t) => ({
                        rs: t.rs,
                        momentum: t.momentum,
                        label: selectedRow.label,
                        date: t.date,
                      }))}
                      type="monotone"
                      dataKey="momentum"
                      stroke={selectedRow.color}
                      strokeWidth={2.5}
                      dot={{ r: 3, fill: selectedRow.color }}
                      isAnimationActive={false}
                    />
                  )}
                  <Scatter
                    name="Head"
                    data={headPoints}
                    fill="#8884d8"
                    shape={(props: {
                      cx?: number;
                      cy?: number;
                      payload?: { fill?: string; symbol?: string };
                    }) => {
                      const { cx = 0, cy = 0, payload } = props;
                      const active = !selected || selected === payload?.symbol;
                      return (
                        <circle
                          cx={cx}
                          cy={cy}
                          r={active ? 7 : 5}
                          fill={payload?.fill ?? "#333"}
                          stroke="#fff"
                          strokeWidth={1}
                          opacity={active ? 1 : 0.65}
                        />
                      );
                    }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 pb-2">
          <CardTitle className="text-base">Symbols</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex items-center gap-2">
              <Label htmlFor="rrg-sort" className="text-xs text-muted-foreground">
                Sort
              </Label>
              <Select value={sortBy} onValueChange={(v) => setSortBy(v as SortKey)}>
                <SelectTrigger id="rrg-sort" className="h-8 w-[130px] text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="quadrant">Quadrant</SelectItem>
                  <SelectItem value="symbol">Symbol</SelectItem>
                  <SelectItem value="rs">RS ratio</SelectItem>
                  <SelectItem value="momentum">Momentum</SelectItem>
                  <SelectItem value="fpi">FPI net (Cr)</SelectItem>
                  <SelectItem value="confluence">Confluence</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Select value={sortDir} onValueChange={(v) => setSortDir(v as "asc" | "desc")}>
              <SelectTrigger className="h-8 w-[110px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="asc">Ascending</SelectItem>
                <SelectItem value="desc">Descending</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={quadrantFilter}
              onValueChange={(v) => setQuadrantFilter(v as RrgQuadrant | "all")}
            >
              <SelectTrigger className="h-8 w-[140px] text-xs">
                <SelectValue placeholder="All quadrants" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All quadrants</SelectItem>
                {(Object.keys(QUADRANT_LABEL) as RrgQuadrant[]).map((q) => (
                  <SelectItem key={q} value={q}>
                    {QUADRANT_LABEL[q]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={confluenceFilter}
              onValueChange={(v) => setConfluenceFilter(v as ConfluenceFilter)}
            >
              <SelectTrigger className="h-8 w-[140px] text-xs">
                <SelectValue placeholder="Confluence" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All confluence</SelectItem>
                {(Object.keys(CONFLUENCE_LABEL) as FpiConfluence[]).map((c) => (
                  <SelectItem key={c} value={c}>
                    {CONFLUENCE_LABEL[c]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {config?.fpi?.periods?.length ? (
              <Select
                value={fpiPeriod}
                onValueChange={(v) => {
                  setFpiPeriod(v);
                  void loadSnapshot({ fpiPeriod: v });
                }}
              >
                <SelectTrigger className="h-8 w-[170px] text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {config.fpi.periods.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : null}
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>
                  <button
                    type="button"
                    className="inline-flex items-center font-medium hover:text-foreground"
                    onClick={() => toggleSort("symbol")}
                  >
                    Symbol
                    <SortIcon active={sortBy === "symbol"} dir={sortDir} />
                  </button>
                </TableHead>
                <TableHead>
                  <button
                    type="button"
                    className="inline-flex items-center font-medium hover:text-foreground"
                    onClick={() => toggleSort("quadrant")}
                  >
                    Quadrant
                    <SortIcon active={sortBy === "quadrant"} dir={sortDir} />
                  </button>
                </TableHead>
                <TableHead className="text-right">
                  <button
                    type="button"
                    className="ml-auto inline-flex items-center font-medium hover:text-foreground"
                    onClick={() => toggleSort("rs")}
                  >
                    RS
                    <SortIcon active={sortBy === "rs"} dir={sortDir} />
                  </button>
                </TableHead>
                <TableHead className="text-right">
                  <button
                    type="button"
                    className="ml-auto inline-flex items-center font-medium hover:text-foreground"
                    onClick={() => toggleSort("momentum")}
                  >
                    Momentum
                    <SortIcon active={sortBy === "momentum"} dir={sortDir} />
                  </button>
                </TableHead>
                <TableHead className="text-right">
                  <button
                    type="button"
                    className="ml-auto inline-flex items-center font-medium hover:text-foreground"
                    onClick={() => toggleSort("fpi")}
                  >
                    FPI net (Cr)
                    <SortIcon active={sortBy === "fpi"} dir={sortDir} />
                  </button>
                </TableHead>
                <TableHead>Flow</TableHead>
                <TableHead>
                  <button
                    type="button"
                    className="inline-flex items-center font-medium hover:text-foreground"
                    onClick={() => toggleSort("confluence")}
                  >
                    Confluence
                    <SortIcon active={sortBy === "confluence"} dir={sortDir} />
                  </button>
                </TableHead>
                <TableHead>Date</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedSymbols.map((row) => (
                <TableRow
                  key={row.symbol}
                  className={cn(
                    "cursor-pointer",
                    selected === row.symbol && "bg-muted/60",
                  )}
                  onClick={() => setSelected(row.symbol)}
                >
                  <TableCell className="font-medium">
                    <span className="mr-2 inline-block h-2.5 w-2.5 rounded-full" style={{ background: row.color }} />
                    {row.label}
                  </TableCell>
                  <TableCell>
                    <Badge variant={QUADRANT_VARIANT[row.quadrant] ?? "outline"}>
                      {QUADRANT_LABEL[row.quadrant] ?? row.quadrant}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">{row.head.rs.toFixed(2)}</TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {row.head.momentum.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {row.fpi ? (
                      <span
                        className={cn(
                          (row.fpi.net_equity_inr ?? 0) > 0 && "text-emerald-500",
                          (row.fpi.net_equity_inr ?? 0) < 0 && "text-red-400",
                        )}
                        title={row.fpi.fpi_sector}
                      >
                        {formatFpiCr(row.fpi.net_equity_inr)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </TableCell>
                  <TableCell className="text-xs">
                    {row.fpi?.flow ?? "—"}
                  </TableCell>
                  <TableCell>
                    {row.fpi?.confluence ? (
                      <Badge
                        variant={CONFLUENCE_VARIANT[row.fpi.confluence] ?? "outline"}
                        title={row.fpi.fpi_sector}
                      >
                        {CONFLUENCE_LABEL[row.fpi.confluence]}
                      </Badge>
                    ) : (
                      <span className="text-xs text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{row.head.date}</TableCell>
                </TableRow>
              ))}
              {!sortedSymbols.length && snapshot?.symbols.length ? (
                <TableRow>
                  <TableCell colSpan={8} className="text-center text-sm text-muted-foreground">
                    No symbols match the current filters.
                  </TableCell>
                </TableRow>
              ) : null}
              {(snapshot?.errors ?? []).map((err) => (
                <TableRow key={`err-${err.symbol}`} className="text-destructive">
                  <TableCell>{err.symbol}</TableCell>
                  <TableCell colSpan={7} className="text-xs">
                    {err.error}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

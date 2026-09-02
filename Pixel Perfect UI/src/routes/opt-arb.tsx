import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronRight, Pause, Play, RefreshCw } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "@/lib/api";
import type {
  OptArbConfig,
  OptArbExpiries,
  OptArbPairs,
  OptArbPayoff,
  OptArbRow,
  OptArbScan,
  OptArbSheet,
  OptArbXSheet,
} from "@/lib/types";

import { BigMiniSheet } from "@/components/opt-arb/BigMiniSheet";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

// Route path is /opt-arb, not /options-arbitrage: api.ui_static.is_api_path
// matches on startswith, and /options is already an API prefix — a hard browser
// load of a colliding path returns a JSON 404 instead of the app.
export const Route = createFileRoute("/opt-arb")({
  component: OptArbPage,
});

const UNDERLYINGS = [
  { name: "NIFTY", exchange: "NFO" },
  { name: "BANKNIFTY", exchange: "NFO" },
  { name: "SENSEX", exchange: "BFO" },
  { name: "CRUDEOIL", exchange: "MCX" },
  { name: "NATURALGAS", exchange: "MCX" },
];

function rupees(value: number | null | undefined) {
  if (value == null) return "—";
  return value.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

function describe(row: OptArbRow) {
  if (row.family === "xcontract") return row.direction ?? "—";
  if (row.family === "butterfly") {
    return `${row.option_type} ${row.strike} fly ±${row.width} · ${row.violation}`;
  }
  if (row.family === "vertical") {
    return `${row.option_type} ${row.lower_strike}/${row.upper_strike} · ${row.violation}`;
  }
  if (row.family === "box") {
    return `${row.lower_strike}/${row.upper_strike} · ${row.direction}`;
  }
  return row.id;
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "up" | "down" }) {
  return (
    <div className="rounded-md border bg-card px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={`font-mono text-sm ${
          tone === "up"
            ? "text-emerald-600 dark:text-emerald-400"
            : tone === "down"
              ? "text-destructive"
              : ""
        }`}
      >
        {value}
      </div>
    </div>
  );
}

/**
 * Expiry payoff. The curve comes from the backend (analysis/opt_arb/payoff.py)
 * rather than being recomputed here — breakevens and the unbounded-tail flag
 * are arithmetic worth having tests for.
 *
 * Two lines on purpose: the dashed one is the structure's own payoff, the solid
 * one is after charges. For a real arbitrage the solid line sits above zero
 * everywhere, and the gap between the two is what the desk exists to measure.
 */
function PayoffChart({ payoff }: { payoff: OptArbPayoff }) {
  const s = payoff.summary;
  if (!payoff.points.length) return null;
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
        <Stat label="Max profit" value={`₹${rupees(s.max_profit)}`} tone="up" />
        <Stat
          label="Max loss"
          value={`₹${rupees(s.max_loss)}`}
          tone={(s.max_loss ?? 0) < 0 ? "down" : "up"}
        />
        <Stat label="At current spot" value={`₹${rupees(s.profit_at_spot)}`} />
        <Stat
          label="Breakeven"
          value={s.breakevens?.length ? s.breakevens.join(" · ") : "none in range"}
        />
      </div>
      <div className="h-[260px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={payoff.points} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis
              dataKey="spot"
              type="number"
              domain={["dataMin", "dataMax"]}
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => v.toLocaleString("en-IN")}
            />
            <YAxis tick={{ fontSize: 11 }} width={72} tickFormatter={(v: number) => rupees(v)} />
            <Tooltip
              formatter={(value, name) => [`₹${rupees(Number(value))}`, name]}
              labelFormatter={(label) => `Underlying ${Number(label).toLocaleString("en-IN")}`}
            />
            <ReferenceLine y={0} className="stroke-muted-foreground" />
            {payoff.strikes.map((k) => (
              <ReferenceLine key={k} x={k} strokeDasharray="2 4" className="stroke-muted" />
            ))}
            {payoff.spot != null && (
              <ReferenceLine
                x={payoff.spot}
                strokeDasharray="4 4"
                className="stroke-muted-foreground"
                label={{ value: "spot", fontSize: 10, position: "top" }}
              />
            )}
            <Line
              type="linear"
              dataKey="gross"
              name="Before charges"
              stroke="hsl(var(--muted-foreground))"
              strokeDasharray="4 3"
              dot={false}
              isAnimationActive={false}
            />
            <Line
              type="linear"
              dataKey="net"
              name="After charges"
              stroke="hsl(var(--chart-1))"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function OptArbPage() {
  const [config, setConfig] = useState<OptArbConfig | null>(null);
  const [pairs, setPairs] = useState<OptArbPairs | null>(null);
  const [scan, setScan] = useState<OptArbScan | null>(null);
  const [sheet, setSheet] = useState<OptArbSheet | null>(null);
  const [expiries, setExpiries] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<OptArbRow | null>(null);

  const [minNet, setMinNet] = useState("0");
  const [lots, setLots] = useState("1");
  const [requireClean, setRequireClean] = useState(true);
  const [requireDepth, setRequireDepth] = useState(true);
  const [familyFilter, setFamilyFilter] = useState("ALL");

  const [xPair, setXPair] = useState("CRUDEOIL_CRUDEOILM");
  const [xThreshold, setXThreshold] = useState("0");
  const [xSheets, setXSheets] = useState<OptArbXSheet[] | null>(null);
  const [xLoading, setXLoading] = useState(false);

  const [sheetName, setSheetName] = useState("NIFTY");
  const [sheetExpiry, setSheetExpiry] = useState("");
  const [sheetType, setSheetType] = useState("CE");

  const sheetExchange =
    UNDERLYINGS.find((u) => u.name === sheetName)?.exchange ?? "NFO";

  useEffect(() => {
    api.get<OptArbConfig>("/oarb/config", { silent: true }).then(setConfig).catch(() => {});
    api.get<OptArbPairs>("/oarb/pairs", { silent: true }).then(setPairs).catch(() => {});
  }, []);

  useEffect(() => {
    api
      .get<OptArbExpiries>(
        `/oarb/expiries?name=${sheetName}&exchange=${sheetExchange}`,
        { silent: true },
      )
      .then((data) => {
        setExpiries(data.expiries);
        setSheetExpiry((current) =>
          current && data.expiries.includes(current) ? current : (data.expiries[0] ?? ""),
        );
      })
      .catch(() => setExpiries([]));
  }, [sheetName, sheetExchange]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        lots,
        min_net: minNet || "0",
        require_clean: String(requireClean),
        require_depth: String(requireDepth),
      });
      const data = await api.get<OptArbScan>(`/oarb/scan?${params}`, { silent: true });
      setScan(data);
      // Open the top row automatically. The detail panel used to require a click
      // with no affordance for it, so the legs and the payoff were effectively
      // invisible unless you already knew they were there.
      setSelected(data.rows[0] ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scan failed");
    } finally {
      setLoading(false);
    }
  }, [lots, minNet, requireClean, requireDepth]);

  // CE and PE together — the two sides of one pair are read against each other,
  // which is how the vendor worksheets lay it out and how the ATM row reads.
  const loadXSheets = useCallback(async () => {
    setXLoading(true);
    try {
      const sheets = await Promise.all(
        (["CE", "PE"] as const).map((side) =>
          api.get<OptArbXSheet>(
            `/oarb/xsheet?pair=${xPair}&option_type=${side}&lots=${lots}&threshold=${
              xThreshold || "0"
            }`,
            { silent: true },
          ),
        ),
      );
      setXSheets(sheets);
    } catch {
      setXSheets(null);
    } finally {
      setXLoading(false);
    }
  }, [xPair, lots, xThreshold]);

  const loadSheet = useCallback(async () => {
    if (!sheetExpiry) return;
    try {
      const params = new URLSearchParams({
        name: sheetName,
        exchange: sheetExchange,
        expiry: sheetExpiry,
        option_type: sheetType,
        lots,
      });
      setSheet(await api.get<OptArbSheet>(`/oarb/sheet?${params}`, { silent: true }));
    } catch {
      setSheet(null);
    }
  }, [sheetName, sheetExchange, sheetExpiry, sheetType, lots]);

  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(id);
  }, [auto, refresh]);

  const rows = useMemo(() => {
    const list = scan?.rows ?? [];
    return familyFilter === "ALL" ? list : list.filter((r) => r.family === familyFilter);
  }, [scan, familyFilter]);

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Options Arbitrage</h1>
        <p className="text-sm text-muted-foreground">
          Model-free violations priced at bid/ask and netted against charges — scan only,
          no orders. Charge card as of {config?.rates_asof ?? "—"}.
        </p>
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-4 pt-6">
          <div className="space-y-1">
            <Label>Min net ₹</Label>
            <Input
              className="w-[110px]"
              value={minNet}
              onChange={(e) => setMinNet(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label>Lots</Label>
            <Input className="w-[80px]" value={lots} onChange={(e) => setLots(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label>Family</Label>
            <Select value={familyFilter} onValueChange={setFamilyFilter}>
              <SelectTrigger className="w-[140px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">All</SelectItem>
                <SelectItem value="xcontract">Big vs mini</SelectItem>
                <SelectItem value="butterfly">Butterfly</SelectItem>
                <SelectItem value="vertical">Vertical</SelectItem>
                <SelectItem value="box">Box</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2 pb-2">
            <Checkbox
              id="clean"
              checked={requireClean}
              onCheckedChange={(c) => setRequireClean(Boolean(c))}
            />
            <Label htmlFor="clean" className="text-xs">
              Tier A only
            </Label>
          </div>
          <div className="flex items-center gap-2 pb-2">
            <Checkbox
              id="depth"
              checked={requireDepth}
              onCheckedChange={(c) => setRequireDepth(Boolean(c))}
            />
            <Label htmlFor="depth" className="text-xs">
              Fillable size only
            </Label>
          </div>
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
            <RefreshCw className={`mr-1 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Scan
          </Button>
          <Button variant={auto ? "default" : "outline"} size="sm" onClick={() => setAuto((a) => !a)}>
            {auto ? <Pause className="mr-1 h-4 w-4" /> : <Play className="mr-1 h-4 w-4" />}
            {auto ? "Polling 15s" : "Paused"}
          </Button>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive/50">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      <Tabs defaultValue="scan">
        <TabsList>
          <TabsTrigger value="scan">Opportunities</TabsTrigger>
          <TabsTrigger value="pairs">Big vs mini</TabsTrigger>
          <TabsTrigger value="sheet">Butterfly sheet</TabsTrigger>
        </TabsList>

        <TabsContent value="scan" className="mt-4 flex flex-col gap-4">
          {scan && (
            <p className="text-xs text-muted-foreground">
              {scan.counts.rows} rows ({scan.counts.tier_a} tier A, {scan.counts.tier_b} tier B) ·{" "}
              {scan.counts.instruments_quoted} instruments quoted · {scan.generated_at}
            </p>
          )}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Ranked by net edge after charges</CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tier</TableHead>
                    <TableHead>Family</TableHead>
                    <TableHead>Instrument</TableHead>
                    <TableHead>Structure</TableHead>
                    <TableHead className="text-right">Gross ₹</TableHead>
                    <TableHead className="text-right">Charges ₹</TableHead>
                    <TableHead className="text-right">Net ₹</TableHead>
                    <TableHead className="text-right">Lots</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow
                      key={row.id}
                      className={`cursor-pointer hover:bg-muted/60 ${
                        selected?.id === row.id ? "bg-muted" : ""
                      }`}
                      onClick={() => setSelected(row)}
                    >
                      <TableCell>
                        <Badge variant={row.tier === "A" ? "default" : "secondary"}>
                          {row.tier}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs">{row.family}</TableCell>
                      <TableCell className="font-medium">
                        {row.label ?? `${row.underlying} ${row.expiry ?? ""}`}
                      </TableCell>
                      <TableCell className="text-xs">{describe(row)}</TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {rupees(row.gross)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-muted-foreground">
                        {rupees(row.cost)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                        {rupees(row.net)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {row.max_lots ?? "—"}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          {row.warnings?.length ? (
                            <AlertTriangle className="h-4 w-4 text-amber-500" />
                          ) : null}
                          <ChevronRight className="h-4 w-4 text-muted-foreground" />
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                  {!rows.length && (
                    <TableRow>
                      <TableCell colSpan={9} className="py-10 text-center text-muted-foreground">
                        {loading
                          ? "Scanning…"
                          : scan
                            ? "No violation clears the charge floor. That is the normal state."
                            : "Press Scan."}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {selected && (
            <Card>
              <CardHeader>
                <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                  <span>Payoff at expiry — {describe(selected)}</span>
                  {selected.payoff?.summary.risk_free ? (
                    <Badge>
                      {selected.payoff.summary.flat ? "Flat · risk-free" : "Risk-free"}
                    </Badge>
                  ) : null}
                  {selected.payoff?.summary.unbounded_loss ? (
                    <Badge variant="destructive">Unbounded loss</Badge>
                  ) : null}
                </CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 overflow-x-auto">
                {selected.payoff ? <PayoffChart payoff={selected.payoff} /> : null}
                {selected.payoff?.assumptions.map((note) => (
                  <p key={note} className="text-xs text-muted-foreground">
                    {note}
                  </p>
                ))}
                {selected.warnings?.map((w) => (
                  <p key={w} className="text-xs text-amber-600 dark:text-amber-400">
                    ⚠ {w}
                  </p>
                ))}
                {selected.exercise?.applies && (
                  <p className="text-xs text-muted-foreground">
                    Exercise levy included: ₹{rupees(selected.exercise.stt)} —{" "}
                    {selected.exercise.reason}
                  </p>
                )}
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Side</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead className="text-right">Price</TableHead>
                      <TableHead className="text-right">Units</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {selected.legs?.map((leg, i) => (
                      <TableRow key={`${leg.tradingsymbol}-${i}`}>
                        <TableCell>
                          <Badge variant={leg.side === "BUY" ? "default" : "destructive"}>
                            {leg.side}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {leg.exchange}:{leg.tradingsymbol}
                        </TableCell>
                        <TableCell className="text-right font-mono text-xs">{leg.price}</TableCell>
                        <TableCell className="text-right font-mono text-xs">{leg.units}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="pairs" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardContent className="flex flex-wrap items-end gap-4 pt-6">
              <div className="space-y-1">
                <Label>Pair</Label>
                <Select value={xPair} onValueChange={setXPair}>
                  <SelectTrigger className="w-[220px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(pairs?.pairs ?? []).map((p) => (
                      <SelectItem key={p.key} value={p.key}>
                        {p.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Highlight above ₹</Label>
                <Input
                  className="w-[120px]"
                  value={xThreshold}
                  onChange={(e) => setXThreshold(e.target.value)}
                />
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void loadXSheets()}
                disabled={xLoading}
              >
                <RefreshCw className={`mr-1 h-4 w-4 ${xLoading ? "animate-spin" : ""}`} />
                Load grid
              </Button>
            </CardContent>
          </Card>

          {xSheets && (
            <div className="grid gap-4 xl:grid-cols-2">
              {xSheets.map((s) => (
                <BigMiniSheet key={`${s.pair.key}-${s.option_type}`} sheet={s} />
              ))}
            </div>
          )}

          <div className="grid gap-4 md:grid-cols-2">
            {pairs?.pairs.map((pair) => (
              <Card key={pair.key} className={pair.clean ? "" : "border-amber-500/40"}>
                <CardHeader className="pb-2">
                  <CardTitle className="flex items-center justify-between text-base">
                    {pair.label}
                    <Badge variant={pair.clean ? "default" : "secondary"}>
                      {pair.clean
                        ? pair.front_clean
                          ? "Tier A — arbitrage"
                          : "Tier A from " + (pair.clean_expiries[0] ?? "")
                        : "Tier B — carry"}
                    </Badge>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2 text-xs">
                  <p className="text-muted-foreground">{pair.reason}</p>
                  <div className="grid grid-cols-3 gap-2 font-mono">
                    <span />
                    <span className="text-muted-foreground">{pair.big}</span>
                    <span className="text-muted-foreground">{pair.mini}</span>
                    <span className="text-muted-foreground">Option expiry</span>
                    <span>{pair.front_expiry.big ?? "—"}</span>
                    <span>{pair.front_expiry.mini ?? "—"}</span>
                    <span className="text-muted-foreground">Refs future</span>
                    <span>{pair.referenced_future.big ?? "—"}</span>
                    <span>{pair.referenced_future.mini ?? "—"}</span>
                  </div>
                  <p className="text-muted-foreground">
                    1 {pair.big} lot = {pair.ratio} {pair.mini} lots · quoted per {pair.unit}
                  </p>
                  <p className="text-muted-foreground">
                    Tradable expiries:{" "}
                    {pair.clean_expiries.length ? (
                      <span className="font-mono">{pair.clean_expiries.join(", ")}</span>
                    ) : (
                      "none — no shared expiry lines up on the futures month"
                    )}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        <TabsContent value="sheet" className="mt-4 flex flex-col gap-4">
          <Card>
            <CardContent className="flex flex-wrap items-end gap-4 pt-6">
              <div className="space-y-1">
                <Label>Underlying</Label>
                <Select value={sheetName} onValueChange={setSheetName}>
                  <SelectTrigger className="w-[150px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {UNDERLYINGS.map((u) => (
                      <SelectItem key={u.name} value={u.name}>
                        {u.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Expiry</Label>
                <Select value={sheetExpiry} onValueChange={setSheetExpiry}>
                  <SelectTrigger className="w-[150px]">
                    <SelectValue />
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
              <div className="space-y-1">
                <Label>Side</Label>
                <Select value={sheetType} onValueChange={setSheetType}>
                  <SelectTrigger className="w-[90px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="CE">CE</SelectItem>
                    <SelectItem value="PE">PE</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button variant="outline" size="sm" onClick={() => void loadSheet()}>
                <RefreshCw className="mr-1 h-4 w-4" />
                Load sheet
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Combo butterfly — BUY at the wings' ask, SELL at their bid
              </CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              {sheet ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Strike</TableHead>
                      {sheet.widths.map((w) => (
                        <TableHead key={w} colSpan={2} className="text-center">
                          ± {w}
                        </TableHead>
                      ))}
                    </TableRow>
                    <TableRow>
                      <TableHead />
                      {sheet.widths.flatMap((w) => [
                        <TableHead key={`${w}-b`} className="text-right text-[10px]">
                          BUY
                        </TableHead>,
                        <TableHead key={`${w}-s`} className="text-right text-[10px]">
                          SELL
                        </TableHead>,
                      ])}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sheet.rows.map((row) => (
                      <TableRow key={row.strike}>
                        <TableCell className="font-mono text-xs font-medium">
                          {row.strike}
                        </TableCell>
                        {sheet.widths.flatMap((w) => {
                          const cell = row.cells[w];
                          const hit = Boolean(cell?.violation);
                          return [
                            <TableCell
                              key={`${row.strike}-${w}-b`}
                              className={`text-right font-mono text-xs ${
                                cell?.violation === "buy_below_zero"
                                  ? "bg-emerald-500/20 font-semibold"
                                  : ""
                              }`}
                            >
                              {cell?.buy ?? "—"}
                            </TableCell>,
                            <TableCell
                              key={`${row.strike}-${w}-s`}
                              className={`text-right font-mono text-xs ${
                                cell?.violation === "sell_above_width"
                                  ? "bg-emerald-500/20 font-semibold"
                                  : ""
                              } ${hit ? "" : "text-muted-foreground"}`}
                            >
                              {cell?.sell ?? "—"}
                            </TableCell>,
                          ];
                        })}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <p className="py-10 text-center text-muted-foreground">
                  Pick an underlying and expiry, then Load sheet.
                </p>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

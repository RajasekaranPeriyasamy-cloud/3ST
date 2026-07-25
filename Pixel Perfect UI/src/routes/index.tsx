import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ArrowRight, RefreshCw, Search } from "lucide-react";

import { api } from "@/lib/api";
import type {
  HealthResponse,
  InstrumentHit,
  RiskMode,
  Segment,
  Selection,
  SpreadPreview,
  SpreadTemplate,
  StMethod,
  SystemMode,
  Timeframe,
} from "@/lib/types";
import { useSelection } from "@/context/SelectionContext";
import { useWatchlist } from "@/context/WatchlistContext";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
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
import { Separator } from "@/components/ui/separator";
import { Checkbox } from "@/components/ui/checkbox";

export const Route = createFileRoute("/")({
  component: StockSelectionPage,
});

const TIMEFRAMES: Timeframe[] = ["1min", "3min", "5min", "15min", "30min", "60min"];
const TEMPLATES: { value: SpreadTemplate; label: string }[] = [
  { value: "bull_call", label: "Bull Call Spread" },
  { value: "bear_put", label: "Bear Put Spread" },
  { value: "bear_call", label: "Bear Call Spread" },
  { value: "bull_put", label: "Bull Put Spread" },
  { value: "iron_condor", label: "Iron Condor" },
];

function StockSelectionPage() {
  const navigate = useNavigate();
  const { selection, update, save, refresh } = useSelection();
  const { add: addToWatchlist } = useWatchlist();

  const [segment, setSegment] = useState<Segment>(selection.segment ?? "equity");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<InstrumentHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);

  useEffect(() => {
    api.get<HealthResponse>("/health", { silent: true }).then(setHealth).catch(() => {});
  }, []);

  // Debounced search
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) {
      setResults([]);
      setSearchError(null);
      return;
    }
    setSearching(true);
    setSearchError(null);
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await api.get<{ items?: InstrumentHit[] } | InstrumentHit[]>(
          `/instruments/search?q=${encodeURIComponent(query)}&segment=${segment}&limit=25`,
          { silent: true },
        );
        setResults(Array.isArray(r) ? r : (r.items ?? []));
      } catch (e) {
        setResults([]);
        setSearchError(
          e instanceof Error ? e.message : "Instrument search failed — is the API running?",
        );
      } finally {
        setSearching(false);
      }
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, segment]);

  const selectedInstrument = useMemo<InstrumentHit | null>(() => {
    if (!selection.instrument_token) return null;
    return {
      instrument_token: selection.instrument_token,
      exchange: selection.exchange ?? "",
      tradingsymbol: selection.tradingsymbol ?? "",
      name: selection.name ?? selection.tradingsymbol ?? "",
      segment: selection.segment,
      instrument_type: "",
      lot_size: 0,
    };
  }, [selection]);

  function pickInstrument(hit: InstrumentHit) {
    const patch: Partial<Selection> = {
      instrument_token: hit.instrument_token,
      exchange: hit.exchange,
      tradingsymbol: hit.tradingsymbol,
      name: hit.name,
      lot_size: hit.lot_size || 0,
      segment,
    };
    if (hit.exchange === "MCX" && segment === "option") {
      patch.product_type = "NRML";
    }
    update(patch);
    setQuery("");
    setResults([]);
  }

  async function onAddToWatchlist(next?: Partial<Selection>) {
    const merged: Selection = { ...selection, entry_mode: "manual", ...next };
    if (!merged.instrument_token) {
      toast.error("Pick an instrument first");
      return;
    }
    try {
      await addToWatchlist(merged);
      toast.success("Added to signal queue — see Dashboard");
    } catch {
      /* handled */
    }
  }

  async function onSendToLiveDesk() {
    const merged: Selection = { ...selection, entry_mode: "manual" };
    if (!merged.instrument_token) {
      toast.error("Pick an instrument first");
      return;
    }
    try {
      await save(merged);
      await addToWatchlist(merged);
      toast.success("Ready on Live Desk — set LIVE, ARM, then BUY or SELL");
      navigate({ to: "/live" });
    } catch {
      /* handled */
    }
  }

  async function onSave(next?: Partial<Selection>) {
    const merged: Selection = { ...selection, ...next };
    try {
      await save(merged);
      toast.success("Selection saved");
    } catch {
      // toast already shown by api
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Stock Selection</h1>
          <p className="text-sm text-muted-foreground">
            Pick an instrument, timeframe, and product mode before running a backtest or arming the live desk.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refresh()}>
          <RefreshCw className="mr-2 h-4 w-4" /> Reload
        </Button>
      </header>

      {/* Instrument search */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Instrument</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Tabs value={segment} onValueChange={(v) => setSegment(v as Segment)}>
            <TabsList>
              <TabsTrigger value="equity">Equity</TabsTrigger>
              <TabsTrigger value="future">Future</TabsTrigger>
              <TabsTrigger value="option">Options</TabsTrigger>
            </TabsList>
            <TabsContent value={segment} className="mt-4">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={`Search ${segment} — tradingsymbol or name`}
                  className="pl-9 font-mono"
                />
              </div>
              {searching && (
                <p className="mt-2 text-xs text-muted-foreground">Searching…</p>
              )}
              {searchError && !searching ? (
                <p className="mt-2 text-xs text-destructive">{searchError}</p>
              ) : null}
              {!searching && query.trim() && results.length === 0 && !searchError ? (
                <p className="mt-2 text-xs text-muted-foreground">No matches for “{query}”</p>
              ) : null}
              {results.length > 0 && (
                <div className="mt-3 max-h-72 overflow-auto rounded-md border border-border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Symbol</TableHead>
                        <TableHead>Name</TableHead>
                        <TableHead>Exch</TableHead>
                        <TableHead className="text-right">Lot</TableHead>
                        <TableHead className="text-right">Token</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {results.map((r) => (
                        <TableRow
                          key={r.instrument_token}
                          className="cursor-pointer hover:bg-accent/40"
                          onClick={() => pickInstrument(r)}
                        >
                          <TableCell className="font-mono">{r.tradingsymbol}</TableCell>
                          <TableCell className="text-muted-foreground">{r.name}</TableCell>
                          <TableCell>{r.exchange}</TableCell>
                          <TableCell className="text-right">{r.lot_size || "—"}</TableCell>
                          <TableCell className="text-right font-mono text-xs text-muted-foreground">
                            {r.instrument_token}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}

              {selectedInstrument && (
                <div className="mt-4 rounded-md border border-border bg-card p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-col">
                      <span className="font-mono text-sm font-semibold">
                        {selectedInstrument.tradingsymbol}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {selectedInstrument.name}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{selectedInstrument.exchange}</Badge>
                      <Badge variant="secondary">token {selectedInstrument.instrument_token}</Badge>
                    </div>
                  </div>
                </div>
              )}
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Timeframe & product */}
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Timeframe</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {TIMEFRAMES.map((tf) => (
                <Button
                  key={tf}
                  size="sm"
                  variant={selection.timeframe === tf ? "default" : "outline"}
                  onClick={() => update({ timeframe: tf })}
                >
                  {tf}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Product Mode</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <Button
              variant={selection.product === "underlying" ? "default" : "outline"}
              onClick={() => update({ product: "underlying", spread: null })}
              className="justify-start"
            >
              Underlying only — trade equity/future/index on 3ST signals
            </Button>
            <Button
              variant={selection.product === "options_spread" ? "default" : "outline"}
              onClick={() =>
                update({
                  product: "options_spread",
                  spread:
                    selection.spread ??
                    {
                      underlying: health?.index_options?.[0] ?? "NIFTY",
                      expiry: "",
                      long_template: "bull_call",
                      short_template: "bear_call",
                      width_steps: 1,
                      legs_long: [],
                      legs_short: [],
                    },
                })
              }
              className="justify-start"
            >
              Index Options Spread — 3ST on spot, execute multi-leg spreads
            </Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Live entry mode</CardTitle>
          <p className="text-xs text-muted-foreground">
            Manual = you choose BUY or SELL on Live Desk. Exit is always managed by 3ST zone logic
            (ST1/ST2/ST3 from Strategy Settings).
          </p>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={selection.entry_mode === "manual" ? "default" : "outline"}
            onClick={() => update({ entry_mode: "manual" })}
          >
            Manual entry
          </Button>
          <Button
            size="sm"
            variant={selection.entry_mode === "signal" ? "default" : "outline"}
            onClick={() => update({ entry_mode: "signal" })}
          >
            3ST signal entry
          </Button>
        </CardContent>
      </Card>

      {selection.product === "options_spread" && (
        <SpreadBuilder indices={health?.index_options ?? ["NIFTY", "BANKNIFTY", "SENSEX"]} />
      )}

      <StrategySettingsPanel />

      <Separator />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-muted-foreground">
          <strong>Send to Live Desk</strong> — manual BUY/SELL, exit by 3ST algo.{" "}
          <strong>Add to Queue</strong> also adds to Dashboard watchlist.
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => onSendToLiveDesk()}>
            Send to Live Desk <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
          <Button variant="outline" onClick={() => onSave()}>
            Save Selection
          </Button>
          <Button variant="secondary" onClick={() => onAddToWatchlist()}>
            Add to Queue
          </Button>
          <Button
            variant="secondary"
            onClick={async () => {
              await onSave();
              navigate({ to: "/backtest" });
            }}
          >
            Backtest <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function setStEnabled(
  key: "st1_enabled" | "st2_enabled" | "st3_enabled",
  enabled: boolean,
  selection: Selection,
  update: (patch: Partial<Selection>) => void,
) {
  const next = {
    st1_enabled: key === "st1_enabled" ? enabled : selection.st1_enabled,
    st2_enabled: key === "st2_enabled" ? enabled : selection.st2_enabled,
    st3_enabled: key === "st3_enabled" ? enabled : selection.st3_enabled,
  };
  if (!next.st1_enabled && !next.st2_enabled && !next.st3_enabled) {
    toast.error("At least one SuperTrend (ST1, ST2, or ST3) must stay enabled");
    return;
  }
  update({ [key]: enabled });
}

function StrategySettingsPanel() {
  const { selection, update } = useSelection();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">3ST Strategy & Session</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label>SuperTrend method</Label>
          <Select
            value={selection.st_method}
            onValueChange={(v) => update({ st_method: v as StMethod })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="heikin_ashi">Heikin Ashi ST</SelectItem>
              <SelectItem value="regular">Regular candle ST</SelectItem>
              <SelectItem value="hybrid">Hybrid (HA ST · regular close)</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Session mode</Label>
          <Select
            value={selection.system_mode}
            onValueChange={(v) => {
              const mode = v as SystemMode;
              update({
                system_mode: mode,
                product_type: mode === "Intraday" ? "MIS" : "NRML",
              });
            }}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="Intraday">Intraday — force exit at time below</SelectItem>
              <SelectItem value="Positional">Positional — hold overnight</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Order product (Kite)</Label>
          <Select
            value={selection.product_type ?? "MIS"}
            onValueChange={(v) => update({ product_type: v as "MIS" | "NRML" })}
            disabled={selection.system_mode === "Positional"}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="MIS">MIS — intraday (auto square-off by broker)</SelectItem>
              <SelectItem value="NRML">NRML — carry overnight</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-[10px] text-muted-foreground">
            MCX commodity options use NRML only on Kite — force exit at 22:45 closes intraday.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Force exit time (intraday)</Label>
          <Input
            type="time"
            value={selection.force_exit}
            disabled={selection.system_mode === "Positional"}
            onChange={(e) => update({ force_exit: e.target.value })}
            className="font-mono"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Session start</Label>
          <Input
            type="time"
            value={selection.session_start}
            disabled={selection.system_mode === "Positional"}
            onChange={(e) => update({ session_start: e.target.value })}
            className="font-mono"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <Label>Session end</Label>
          <Input
            type="time"
            value={selection.session_end}
            disabled={selection.system_mode === "Positional"}
            onChange={(e) => update({ session_end: e.target.value })}
            className="font-mono"
          />
          <p className="text-[10px] text-muted-foreground">
            MCX: use 23:30. Force exit must be before session end (e.g. 22:45 → 23:30).
          </p>
        </div>

        <div className="flex items-center gap-2 md:col-span-3">
          <Checkbox
            checked={selection.adx_enabled}
            onCheckedChange={(v) => update({ adx_enabled: Boolean(v) })}
          />
          <span className="text-sm">ADX filter on entries (regular OHLC · wait for ADX confirmation)</span>
        </div>

        <p className="text-xs text-muted-foreground md:col-span-3">
          Check ST1/ST2/ST3 to include each line in entry rules (close must be above/below every enabled ST).
          Zone exit uses the slowest enabled ST (ST1 → ST2 → ST3) — direction must stay aligned (matches PRS Pine).
          PRS TradingView script always uses Heikin Ashi ST; pick &quot;Heikin Ashi ST&quot; here to match TV values.
        </p>

        <NumField label="ATR 1 / Factor 1 (ST1)" enabled={selection.st1_enabled} onEnabled={(st1_enabled) => setStEnabled("st1_enabled", st1_enabled, selection, update)} a={selection.atr1} b={selection.factor1} onA={(atr1) => update({ atr1 })} onB={(factor1) => update({ factor1 })} stepB={0.1} />
        <NumField label="ATR 2 / Factor 2 (ST2)" enabled={selection.st2_enabled} onEnabled={(st2_enabled) => setStEnabled("st2_enabled", st2_enabled, selection, update)} a={selection.atr2} b={selection.factor2} onA={(atr2) => update({ atr2 })} onB={(factor2) => update({ factor2 })} stepB={0.1} />
        <NumField label="ATR 3 / Factor 3 (ST3)" enabled={selection.st3_enabled} onEnabled={(st3_enabled) => setStEnabled("st3_enabled", st3_enabled, selection, update)} a={selection.atr3} b={selection.factor3} onA={(atr3) => update({ atr3 })} onB={(factor3) => update({ factor3 })} stepB={0.1} />
        <NumField label="ADX period / threshold" a={selection.adx_period} b={selection.adx_threshold} onA={(adx_period) => update({ adx_period })} onB={(adx_threshold) => update({ adx_threshold })} stepB={0.1} />

        <RiskField label="Stop loss" mode={selection.sl_mode} value={selection.sl_value} onMode={(sl_mode) => update({ sl_mode: sl_mode as RiskMode })} onValue={(sl_value) => update({ sl_value })} />
        <RiskField label="Target" mode={selection.tgt_mode} value={selection.tgt_value} onMode={(tgt_mode) => update({ tgt_mode: tgt_mode as RiskMode })} onValue={(tgt_value) => update({ tgt_value })} />
        <RiskField label="Trailing SL" mode={selection.tsl_mode} value={selection.tsl_value} onMode={(tsl_mode) => update({ tsl_mode: tsl_mode as RiskMode })} onValue={(tsl_value) => update({ tsl_value })} allowAtr />
      </CardContent>
    </Card>
  );
}

function NumField({
  label,
  a,
  b,
  onA,
  onB,
  stepB = 1,
  enabled = true,
  onEnabled,
}: {
  label: string;
  a: number;
  b: number;
  onA: (v: number) => void;
  onB: (v: number) => void;
  stepB?: number;
  enabled?: boolean;
  onEnabled?: (v: boolean) => void;
}) {
  return (
    <div className={`flex flex-col gap-1.5 ${enabled ? "" : "opacity-60"}`}>
      <div className="flex items-center gap-2">
        {onEnabled ? (
          <Checkbox checked={enabled} onCheckedChange={(v) => onEnabled(Boolean(v))} />
        ) : null}
        <Label>{label}</Label>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Input type="number" value={a} disabled={!enabled} onChange={(e) => onA(Number(e.target.value))} className="font-mono" />
        <Input type="number" step={stepB} value={b} disabled={!enabled} onChange={(e) => onB(Number(e.target.value))} className="font-mono" />
      </div>
    </div>
  );
}

function RiskField({
  label,
  mode,
  value,
  onMode,
  onValue,
  allowAtr = false,
}: {
  label: string;
  mode: RiskMode;
  value: number;
  onMode: (m: RiskMode) => void;
  onValue: (v: number) => void;
  allowAtr?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      <div className="grid grid-cols-2 gap-2">
        <Select value={mode} onValueChange={(v) => onMode(v as RiskMode)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="Off">Off</SelectItem>
            <SelectItem value="%">%</SelectItem>
            <SelectItem value="Pts">Points</SelectItem>
            {allowAtr && <SelectItem value="ATR">ATR × multiplier</SelectItem>}
          </SelectContent>
        </Select>
        <Input type="number" step={0.1} value={value} disabled={mode === "Off"} onChange={(e) => onValue(Number(e.target.value))} className="font-mono" />
      </div>
    </div>
  );
}

function SpreadBuilder({ indices }: { indices: string[] }) {
  const { selection, update } = useSelection();
  const spread = selection.spread!;
  const [expiries, setExpiries] = useState<string[]>([]);
  const [preview, setPreview] = useState<SpreadPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  useEffect(() => {
    if (!spread.underlying) return;
    api
      .get<{ expiries?: string[] } | string[]>(
        `/options/expiries?underlying=${encodeURIComponent(spread.underlying)}`,
        { silent: true },
      )
      .then((r) => {
        const list = Array.isArray(r) ? r : (r.expiries ?? []);
        setExpiries(list);
        if (!spread.expiry && list[0]) {
          update({ spread: { ...spread, expiry: list[0] } });
        }
      })
      .catch(() => setExpiries([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spread.underlying]);

  async function runPreview() {
    setPreviewing(true);
    try {
      const r = await api.post<{
        long?: { legs?: SpreadPreview["legs_long"]; net_debit?: number; net_credit?: number; max_loss_estimate?: number; lot_size?: number; spot?: number };
        short?: { legs?: SpreadPreview["legs_short"] };
      }>("/options/spreads/preview-directions", {
        underlying: spread.underlying,
        expiry: spread.expiry,
        long_template: spread.long_template,
        short_template: spread.short_template,
        width_steps: spread.width_steps,
      });
      const legsLong = r.long?.legs ?? [];
      const legsShort = r.short?.legs ?? [];
      const previewPayload: SpreadPreview = {
        legs_long: legsLong,
        legs_short: legsShort,
        net_debit: r.long?.net_debit,
        net_credit: r.long?.net_credit,
        max_loss: r.long?.max_loss_estimate,
        lot_size: r.long?.lot_size,
        spot: r.long?.spot,
      };
      setPreview(previewPayload);
      update({
        spread: {
          ...spread,
          legs_long: legsLong,
          legs_short: legsShort,
        },
      });
      toast.success("Spread preview updated");
    } catch {
      // toast handled
    } finally {
      setPreviewing(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Multi-leg Spread Builder</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid gap-3 md:grid-cols-4">
          <div className="flex flex-col gap-1.5">
            <Label>Underlying</Label>
            <Select
              value={spread.underlying}
              onValueChange={(v) => update({ spread: { ...spread, underlying: v, expiry: "" } })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {indices.map((i) => (
                  <SelectItem key={i} value={i}>
                    {i}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Expiry</Label>
            <Select
              value={spread.expiry || undefined}
              onValueChange={(v) => update({ spread: { ...spread, expiry: v } })}
            >
              <SelectTrigger>
                <SelectValue placeholder="Pick expiry" />
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
            <Label>Long template (3ST Long)</Label>
            <Select
              value={spread.long_template}
              onValueChange={(v) =>
                update({ spread: { ...spread, long_template: v as SpreadTemplate } })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TEMPLATES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Short template (3ST Short)</Label>
            <Select
              value={spread.short_template}
              onValueChange={(v) =>
                update({ spread: { ...spread, short_template: v as SpreadTemplate } })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TEMPLATES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    {t.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Width (strike steps)</Label>
            <Input
              type="number"
              min={1}
              value={spread.width_steps}
              onChange={(e) =>
                update({
                  spread: { ...spread, width_steps: Math.max(1, Number(e.target.value) || 1) },
                })
              }
            />
          </div>
          <div className="flex items-end">
            <Button onClick={runPreview} disabled={previewing || !spread.expiry}>
              {previewing ? "Previewing…" : "Preview Spreads"}
            </Button>
          </div>
        </div>

        <Tabs defaultValue="long">
          <TabsList>
            <TabsTrigger value="long">Long spread</TabsTrigger>
            <TabsTrigger value="short">Short spread</TabsTrigger>
          </TabsList>
          <TabsContent value="long" className="mt-3">
            <LegTable legs={spread.legs_long} />
          </TabsContent>
          <TabsContent value="short" className="mt-3">
            <LegTable legs={spread.legs_short} />
          </TabsContent>
        </Tabs>

        {preview && (
          <div className="grid gap-3 rounded-md border border-border bg-muted/30 p-3 md:grid-cols-5">
            <Metric label="Net Debit" value={preview.net_debit} />
            <Metric label="Net Credit" value={preview.net_credit} />
            <Metric label="Max Loss" value={preview.max_loss} tone="bear" />
            <Metric label="Lot Size" value={preview.lot_size} />
            <Metric label="Spot" value={preview.spot} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LegTable({ legs }: { legs: import("@/lib/types").SpreadLeg[] }) {
  if (!legs?.length) {
    return (
      <div className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
        No legs yet — run Preview Spreads.
      </div>
    );
  }
  return (
    <div className="rounded-md border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Side</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Strike</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead className="text-right">LTP</TableHead>
            <TableHead className="text-right">Qty</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {legs.map((l) => (
            <TableRow key={l.instrument_token}>
              <TableCell>
                <Badge className={l.side === "BUY" ? "bg-bull" : "bg-bear"}>{l.side}</Badge>
              </TableCell>
              <TableCell>{l.option_type}</TableCell>
              <TableCell className="font-mono">{l.strike}</TableCell>
              <TableCell className="font-mono text-xs">{l.tradingsymbol}</TableCell>
              <TableCell className="text-right font-mono">
                {l.ltp?.toFixed?.(2) ?? "—"}
              </TableCell>
              <TableCell className="text-right font-mono">{l.quantity}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | undefined;
  tone?: "bull" | "bear";
}) {
  const cls =
    tone === "bull" ? "text-bull" : tone === "bear" ? "text-bear" : "text-foreground";
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className={`font-mono text-sm ${cls}`}>
        {value === undefined || value === null ? "—" : value.toLocaleString()}
      </span>
    </div>
  );
}

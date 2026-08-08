import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  FileSearch,
  Loader2,
  Pin,
  PinOff,
  Play,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api, ApiError } from "@/lib/api";
import type {
  EquityPin,
  EquityReportJob,
  EquityReportListResponse,
  InstrumentSearchResponse,
} from "@/lib/types";
import { ReportPageDownload } from "@/components/ReportPageDownload";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/equity-report")({
  component: EquityReportPage,
});

/** Poll fast while something is in flight, slowly when the desk is idle. */
const ACTIVE_POLL_MS = 3000;
const IDLE_POLL_MS = 20000;
const SEARCH_DEBOUNCE_MS = 250;

const ACTIVE_STATUSES = new Set(["queued", "running"]);

function isActive(job: Pick<EquityReportJob, "status">) {
  return ACTIVE_STATUSES.has(job.status);
}

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "done") return "default";
  if (status === "failed") return "destructive";
  if (status === "cancelled") return "outline";
  return "secondary";
}

function formatWhen(iso: string | null | undefined) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatCost(usd: number | null | undefined) {
  if (!usd) return "—";
  return `$${usd.toFixed(2)}`;
}

function EquityReportPage() {
  const [jobs, setJobs] = useState<EquityReportJob[]>([]);
  const [meta, setMeta] = useState<Omit<EquityReportListResponse, "jobs"> | null>(null);
  const [pins, setPins] = useState<EquityPin[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<EquityReportJob | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<InstrumentSearchResponse["items"]>([]);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [importNote, setImportNote] = useState<string | null>(null);

  const anyActive = useMemo(() => jobs.some(isActive), [jobs]);
  // Ref so the poll effect doesn't re-subscribe on every selection change.
  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selectedId;

  const refreshJobs = useCallback(async () => {
    try {
      const res = await api.get<EquityReportListResponse>("/equity/reports", { silent: true });
      const { jobs: rows, ...rest } = res;
      setJobs(rows);
      setMeta(rest);
      if (!selectedRef.current && rows.length) setSelectedId(rows[0].id);
    } catch {
      /* transient — the next poll will retry */
    }
  }, []);

  const refreshPins = useCallback(async () => {
    try {
      const res = await api.get<{ pins: EquityPin[] }>("/equity/pins", { silent: true });
      setPins(res.pins);
    } catch {
      /* ignore */
    }
  }, []);

  const refreshDetail = useCallback(async (id: string) => {
    try {
      setDetail(await api.get<EquityReportJob>(`/equity/reports/${id}`, { silent: true }));
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    void refreshJobs();
    void refreshPins();
  }, [refreshJobs, refreshPins]);

  useEffect(() => {
    const interval = anyActive ? ACTIVE_POLL_MS : IDLE_POLL_MS;
    const timer = window.setInterval(() => {
      void refreshJobs();
      if (selectedRef.current) void refreshDetail(selectedRef.current);
    }, interval);
    return () => window.clearInterval(timer);
  }, [anyActive, refreshJobs, refreshDetail]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetail(null);
    void refreshDetail(selectedId);
  }, [selectedId, refreshDetail]);

  // Ticker search reuses the existing instruments endpoint — no equity-specific
  // search backend needed.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const res = await api.get<InstrumentSearchResponse>(
          `/instruments/search?q=${encodeURIComponent(q)}&segment=equity&limit=12`,
          { silent: true },
        );
        if (!cancelled) setResults(res.items ?? []);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  async function generate(ticker: string, company = "") {
    if (busy) return;
    setBusy(true);
    try {
      const job = await api.post<EquityReportJob>("/equity/reports", { ticker, company });
      setSelectedId(job.id);
      setQuery("");
      setResults([]);
      await refreshJobs();
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
    } finally {
      setBusy(false);
    }
  }

  async function pin(symbol: string, company = "") {
    try {
      const res = await api.post<{ pins: EquityPin[] }>("/equity/pins", { symbol, company });
      setPins(res.pins);
    } catch {
      /* toast already shown */
    }
  }

  async function unpin(symbol: string) {
    try {
      const res = await api.del<{ pins: EquityPin[] }>(`/equity/pins/${symbol}`);
      setPins(res.pins);
    } catch {
      /* toast already shown */
    }
  }

  async function importWatchlist() {
    try {
      const res = await api.post<{
        added: string[];
        skipped: string[];
        scanned: number;
        pins: EquityPin[];
      }>("/equity/pins/import-watchlist");
      setPins(res.pins);
      setImportNote(
        res.added.length
          ? `Pinned ${res.added.join(", ")}.`
          : `Scanned ${res.scanned} watchlist name${res.scanned === 1 ? "" : "s"} — none are NSE cash equities` +
              (res.skipped.length ? ` (${res.skipped.join(", ")}).` : "."),
      );
    } catch {
      /* toast already shown */
    }
  }

  async function cancel(id: string) {
    try {
      await api.post(`/equity/reports/${id}/cancel`);
      await refreshJobs();
      await refreshDetail(id);
    } catch {
      /* toast already shown */
    }
  }

  async function remove(id: string) {
    try {
      await api.del(`/equity/reports/${id}`);
      if (selectedId === id) setSelectedId(null);
      await refreshJobs();
    } catch {
      /* toast already shown */
    }
  }

  const pinnedSymbols = useMemo(() => new Set(pins.map((p) => p.symbol)), [pins]);
  const selectedTitle = detail
    ? detail.company
      ? `${detail.company} (${detail.ticker})`
      : detail.ticker
    : "Equity Report";

  return (
    <div className="space-y-4 p-4">
      <header className="report-no-print flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <FileSearch className="h-5 w-5 text-muted-foreground" />
          <h1 className="text-lg font-semibold">Equity Report</h1>
          <span className="text-xs text-muted-foreground">
            NSE/BSE fundamental research · sourced, not generated from memory
          </span>
        </div>
        <div className="flex items-center gap-2">
          <SpendBadge meta={meta} />
          <Button size="sm" variant="outline" onClick={() => void refreshJobs()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
          <ReportPageDownload title={selectedTitle} />
        </div>
      </header>

      <ConfigWarnings meta={meta} />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="report-no-print space-y-4 lg:col-span-1">
          <Card className="print-block">
            <CardHeader className="py-3">
              <CardTitle className="text-sm">New report</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search any NSE ticker — RELIANCE, INFY…"
                  className="pl-8"
                />
              </div>

              {searching && <p className="text-xs text-muted-foreground">Searching instruments…</p>}

              {results.length > 0 && (
                <ScrollArea className="max-h-56">
                  <ul className="space-y-1">
                    {results.map((item) => (
                      <li
                        key={`${item.exchange}:${item.tradingsymbol}`}
                        className="flex items-center justify-between gap-2 rounded border px-2 py-1.5"
                      >
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium">{item.tradingsymbol}</div>
                          {/* Equity search returns the same symbol on NSE and
                              BSE — show the exchange so the rows aren't twins. */}
                          <div className="truncate text-xs text-muted-foreground">
                            {item.exchange}
                            {item.name ? ` · ${item.name}` : ""}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-1">
                          <Button
                            size="icon"
                            variant="ghost"
                            title={pinnedSymbols.has(item.tradingsymbol) ? "Already pinned" : "Pin"}
                            disabled={pinnedSymbols.has(item.tradingsymbol)}
                            onClick={() => void pin(item.tradingsymbol, item.name)}
                          >
                            <Pin className="h-4 w-4" />
                          </Button>
                          <Button
                            size="sm"
                            disabled={busy}
                            onClick={() => void generate(item.tradingsymbol, item.name)}
                          >
                            <Play className="mr-1 h-3.5 w-3.5" />
                            Run
                          </Button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </ScrollArea>
              )}

              {query.trim().length >= 2 && !searching && results.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No match. The instrument cache needs a Kite login before search works.
                </p>
              )}
            </CardContent>
          </Card>

          <Card className="print-block">
            <CardHeader className="flex flex-row items-center justify-between gap-2 py-3">
              <CardTitle className="text-sm">Pinned</CardTitle>
              <Button size="sm" variant="ghost" onClick={() => void importWatchlist()}>
                Import watchlist
              </Button>
            </CardHeader>
            <CardContent className="space-y-2">
              {pins.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  Nothing pinned. Search above and hit the pin icon.
                </p>
              )}
              {pins.map((p) => (
                <div
                  key={p.symbol}
                  className="flex items-center justify-between gap-2 rounded border px-2 py-1.5"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{p.symbol}</div>
                    {p.company && (
                      <div className="truncate text-xs text-muted-foreground">{p.company}</div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="icon"
                      variant="ghost"
                      title="Unpin"
                      onClick={() => void unpin(p.symbol)}
                    >
                      <PinOff className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() => void generate(p.symbol, p.company)}
                    >
                      <Play className="mr-1 h-3.5 w-3.5" />
                      Run
                    </Button>
                  </div>
                </div>
              ))}
              {importNote && <p className="text-xs text-muted-foreground">{importNote}</p>}
            </CardContent>
          </Card>

          <Card className="print-block">
            <CardHeader className="py-3">
              <CardTitle className="text-sm">Reports</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5">
              {jobs.length === 0 && (
                <p className="text-xs text-muted-foreground">No reports yet.</p>
              )}
              {jobs.map((job) => (
                <button
                  key={job.id}
                  type="button"
                  onClick={() => setSelectedId(job.id)}
                  className={cn(
                    "flex w-full items-center justify-between gap-2 rounded border px-2 py-1.5 text-left",
                    selectedId === job.id && "border-primary bg-muted/50",
                  )}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-sm font-medium">{job.ticker}</span>
                      {isActive(job) && <Loader2 className="h-3 w-3 animate-spin" />}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {formatWhen(job.created_at)} · {formatCost(job.cost_usd)}
                    </div>
                  </div>
                  <Badge variant={statusVariant(job.status)} className="shrink-0 text-[10px]">
                    {job.status}
                  </Badge>
                </button>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="lg:col-span-2">
          <ReportPane
            job={detail}
            onCancel={(id) => void cancel(id)}
            onDelete={(id) => void remove(id)}
          />
        </div>
      </div>
    </div>
  );
}

function SpendBadge({ meta }: { meta: Omit<EquityReportListResponse, "jobs"> | null }) {
  if (!meta) return null;
  const cap = meta.daily_usd_cap;
  const spent = meta.spent_today_usd ?? 0;
  const label = cap
    ? `$${spent.toFixed(2)} / $${cap.toFixed(2)} today`
    : `$${spent.toFixed(2)} today`;
  return (
    <Badge variant={meta.capped ? "destructive" : "secondary"} className="font-mono text-[11px]">
      {label}
    </Badge>
  );
}

function ConfigWarnings({ meta }: { meta: Omit<EquityReportListResponse, "jobs"> | null }) {
  if (!meta) return null;
  const notes: string[] = [];
  if (!meta.anthropic_ready) {
    notes.push(
      "ANTHROPIC_API_KEY is not set — reports will fail. Add it to .env and restart the API.",
    );
  }
  if (meta.stub_mode) {
    notes.push("EQUITY_REPORT_STUB=1 — placeholder reports only, no API calls and no spend.");
  }
  if (meta.capped) {
    notes.push("Daily spend cap reached — new reports are blocked until tomorrow.");
  }
  if (!notes.length) return null;
  return (
    <div className="report-no-print space-y-1">
      {notes.map((note) => (
        <div
          key={note}
          className="flex items-start gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
          <span>{note}</span>
        </div>
      ))}
    </div>
  );
}

function ReportPane({
  job,
  onCancel,
  onDelete,
}: {
  job: EquityReportJob | null;
  onCancel: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  if (!job) {
    return (
      <Card className="print-block">
        <CardContent className="py-16 text-center text-sm text-muted-foreground">
          Pick a report on the left, or search a ticker to generate one.
        </CardContent>
      </Card>
    );
  }

  const title = job.company ? `${job.company} (${job.ticker})` : job.ticker;

  return (
    <Card className="print-block">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-base">{title}</CardTitle>
          <p className="mt-1 text-xs text-muted-foreground">
            {formatWhen(job.created_at)} · {job.model || "—"} · {formatCost(job.cost_usd)}
            {job.citations?.length ? ` · ${job.citations.length} sources` : ""}
          </p>
        </div>
        <div className="report-no-print flex items-center gap-2">
          <Badge variant={statusVariant(job.status)}>{job.status}</Badge>
          {isActive(job) && (
            <Button size="sm" variant="outline" onClick={() => onCancel(job.id)}>
              <X className="mr-1 h-3.5 w-3.5" />
              Cancel
            </Button>
          )}
          {!isActive(job) && (
            <Button size="sm" variant="ghost" onClick={() => onDelete(job.id)}>
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      </CardHeader>

      <CardContent>
        {isActive(job) && <ProgressView job={job} />}

        {job.status === "failed" && (
          <div className="rounded border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm">
            {job.error || "Report failed."}
          </div>
        )}

        {job.markdown ? (
          <>
            <article
              className={cn(
                "prose-report max-w-none text-sm leading-relaxed",
                "[&_h1]:mb-3 [&_h1]:mt-0 [&_h1]:text-xl [&_h1]:font-semibold",
                "[&_h2]:mb-2 [&_h2]:mt-6 [&_h2]:text-base [&_h2]:font-semibold",
                "[&_h3]:mb-1.5 [&_h3]:mt-4 [&_h3]:text-sm [&_h3]:font-semibold",
                "[&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5",
                "[&_li]:my-1",
                "[&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-muted-foreground/40 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
                "[&_a]:underline [&_a]:underline-offset-2",
                "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs",
                "[&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs",
                "[&_th]:border [&_th]:bg-muted/60 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left",
                "[&_td]:border [&_td]:px-2 [&_td]:py-1 [&_td]:align-top",
                "[&_hr]:my-5",
              )}
            >
              {/* Tables in a research report are frequently wider than the pane;
                  scroll them here rather than letting the page scroll sideways. */}
              <div className="overflow-x-auto">
                <Markdown remarkPlugins={[remarkGfm]}>{job.markdown}</Markdown>
              </div>
            </article>

            {job.citations?.length > 0 && (
              <>
                <Separator className="my-5" />
                <h2 className="mb-2 text-sm font-semibold">Sources fetched</h2>
                <ol className="list-decimal space-y-1 pl-5 text-xs text-muted-foreground">
                  {job.citations.map((c) => (
                    <li key={c.url}>
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="underline underline-offset-2"
                      >
                        {c.title || c.url}
                      </a>
                    </li>
                  ))}
                </ol>
              </>
            )}
          </>
        ) : (
          !isActive(job) &&
          job.status !== "failed" && (
            <p className="py-6 text-center text-sm text-muted-foreground">No report body.</p>
          )
        )}
      </CardContent>
    </Card>
  );
}

function ProgressView({ job }: { job: EquityReportJob }) {
  const p = job.progress ?? {};
  return (
    <div className="report-no-print mb-4 flex items-center gap-3 rounded border bg-muted/40 px-3 py-3 text-sm">
      <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
      <div>
        <div>{p.note || "Working…"}</div>
        <div className="mt-0.5 text-xs text-muted-foreground">
          {p.tool_calls ? `${p.tool_calls} source fetches` : "Starting"}
          {p.iteration && p.iteration > 1 ? ` · pass ${p.iteration}` : ""}
          {" · a full report usually takes 3–8 minutes"}
        </div>
      </div>
    </div>
  );
}

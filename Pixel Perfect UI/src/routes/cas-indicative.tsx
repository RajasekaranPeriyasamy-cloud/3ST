import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Gavel, Loader2, Pause, Play } from "lucide-react";

import { CasHistoryChart } from "@/components/cas/CasHistoryChart";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { CasHistoryResponse, CasIndicative } from "@/lib/types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/cas-indicative")({
  component: CasIndicativePage,
});

type CasIndex = "NIFTY" | "BANKNIFTY" | "SENSEX";

const INDEXES: Array<{ id: CasIndex; label: string; enabled: boolean }> = [
  { id: "NIFTY", label: "NIFTY", enabled: true },
  { id: "BANKNIFTY", label: "BANKNIFTY", enabled: false },
  { id: "SENSEX", label: "SENSEX", enabled: false },
];

const POLL_IN_WINDOW_MS = 8_000;
/** Outside CAS: still useful for pre-15:15 close forecast — poll faster than once/min. */
const POLL_OUTSIDE_MS = 15_000;

/** Human labels for `official_reject_reason` — a blank KPI should say why. */
const REJECT_LABEL: Record<string, string> = {
  outside_window: "Outside CAS window — not read",
  no_quote: "No quote from Kite",
  missing_field: "Kite sent no indicative field",
  no_spot_anchor: "No spot anchor to validate against",
  out_of_band: "Rejected — more than 3% from spot",
};

function fmt(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  return Number(v).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatSigned(n: number, digits = 2): string {
  const abs = Math.abs(n).toFixed(digits);
  return n > 0 ? `+${abs}` : n < 0 ? `-${abs}` : abs;
}

function formatAsOf(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    day: "2-digit",
    month: "short",
  });
}

function KpiCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "up" | "down" | "muted" | "default";
}) {
  return (
    <div className="rounded-sm border border-border bg-card px-4 py-3 shadow-sm">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-1 font-mono text-2xl font-semibold tracking-tight",
          tone === "up" && "text-emerald-600",
          tone === "down" && "text-rose-600",
          tone === "muted" && "text-muted-foreground/70",
          (!tone || tone === "default") && "text-foreground",
        )}
      >
        {value}
      </div>
      {sub ? <div className="mt-1 text-xs text-muted-foreground">{sub}</div> : null}
    </div>
  );
}

function CasIndicativePage() {
  const [index, setIndex] = useState<CasIndex>("NIFTY");
  const [data, setData] = useState<CasIndicative | null>(null);
  const [clientLast, setClientLast] = useState<CasIndicative | null>(null);
  const [history, setHistory] = useState<CasHistoryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authError, setAuthError] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);

  /** Reads a local JSONL file — no Kite session, so it survives token expiry. */
  async function loadHistory() {
    try {
      const res = await api.get<CasHistoryResponse>(
        `/cas/history?underlying=${encodeURIComponent(index)}`,
        { silent: true },
      );
      setHistory(res);
    } catch {
      // Chart is supplementary — a history read failure must not blank the desk.
    }
  }

  async function load(silent = false) {
    if (!silent) setLoading(true);
    setError(null);
    try {
      const res = await api.get<CasIndicative>(
        `/cas/indicative?underlying=${encodeURIComponent(index)}`,
        { silent: true },
      );
      setData(res);
      setAuthError(false);
      if (res.in_cas_window && res.indicative != null && Number.isFinite(res.indicative)) {
        setClientLast(res);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.toLowerCase().includes("session") || msg.includes("401")) {
        setAuthError(true);
      }
      setError(msg);
    } finally {
      if (!silent) setLoading(false);
    }
    // Poll after /cas/indicative — that call is what appends the newest row.
    await loadHistory();
  }

  useEffect(() => {
    setData(null);
    setClientLast(null);
    setHistory(null);
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload on index change only
  }, [index]);

  useEffect(() => {
    if (!autoRefresh || authError || index !== "NIFTY") return;
    const inWindow = data?.in_cas_window === true;
    const ms = inWindow ? POLL_IN_WINDOW_MS : POLL_OUTSIDE_MS;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void load(true);
    }, ms);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- interval keyed on window + auto
  }, [autoRefresh, authError, index, data?.in_cas_window]);

  const inWindow = data?.in_cas_window === true;
  const spot = data?.spot ?? null;

  const officialIndicative =
    data?.official_indicative != null && Number.isFinite(data.official_indicative)
      ? data.official_indicative
      : data?.indicative != null && Number.isFinite(data.indicative)
        ? data.indicative
        : null;
  const liveOfficial =
    inWindow && officialIndicative != null ? officialIndicative : null;
  const lastOfficial =
    liveOfficial ??
    (data?.last?.indicative != null && Number.isFinite(data.last.indicative)
      ? data.last.indicative
      : null) ??
    (clientLast?.indicative != null && Number.isFinite(clientLast.indicative)
      ? clientLast.indicative
      : null);

  const estimate =
    data?.estimate != null && Number.isFinite(data.estimate) ? data.estimate : null;
  const comps = data?.estimate_components ?? null;
  const estimateMethod = data?.estimate_method ?? null;

  // Hero: official when sane; else desk estimate (primary during CAS when Kite garbage).
  const heroValue = liveOfficial ?? estimate ?? lastOfficial;
  const heroIsEstimate = liveOfficial == null && estimate != null;
  const heroIsLast = liveOfficial == null && estimate == null && lastOfficial != null;

  let deltaPts: number | null = null;
  let deltaPct: number | null = null;
  if (heroValue != null && spot != null && spot !== 0) {
    deltaPts = heroValue - spot;
    deltaPct = (deltaPts / spot) * 100;
  }

  const refLimit = inWindow
    ? (data?.reference_limit_price ?? null)
    : (data?.reference_limit_price ?? data?.last?.reference_limit_price ?? null);
  const upper = inWindow
    ? (data?.upper_circuit_limit ?? null)
    : (data?.upper_circuit_limit ?? data?.last?.upper_circuit_limit ?? null);
  const lower = inWindow
    ? (data?.lower_circuit_limit ?? null)
    : (data?.lower_circuit_limit ?? data?.last?.lower_circuit_limit ?? null);
  const imbalance = inWindow
    ? (data?.total_imbalance ?? null)
    : (data?.total_imbalance ?? data?.last?.total_imbalance ?? null);

  const futPoc =
    data?.session_poc?.poc != null && Number.isFinite(data.session_poc.poc)
      ? data.session_poc.poc
      : comps?.fut_poc != null && Number.isFinite(comps.fut_poc)
        ? comps.fut_poc
        : null;
  const futSymbol = data?.session_poc?.fut_symbol ?? comps?.fut_symbol ?? null;
  const synth = data?.synthetic_future ?? null;
  const synthF =
    synth?.F != null && Number.isFinite(synth.F)
      ? synth.F
      : comps?.synth_f != null && Number.isFinite(comps.synth_f)
        ? comps.synth_f
        : null;
  let basisVsSpot: number | null = null;
  if (synth?.basis_vs_spot != null && Number.isFinite(synth.basis_vs_spot)) {
    basisVsSpot = synth.basis_vs_spot;
  } else if (synthF != null && spot != null) {
    basisVsSpot = synthF - spot;
  }
  const basisAnchor = liveOfficial ?? estimate;
  let basisVsCas: number | null = null;
  if (synth?.basis_vs_indicative != null && Number.isFinite(synth.basis_vs_indicative) && liveOfficial != null) {
    basisVsCas = synth.basis_vs_indicative;
  } else if (synthF != null && basisAnchor != null) {
    basisVsCas = synthF - basisAnchor;
  }

  const enabledIndex = INDEXES.find((i) => i.id === index)?.enabled ?? false;

  // Official KPI: distinguish "live and accepted" / "stale last tick" / "rejected, here's why".
  const officialShown = liveOfficial ?? (inWindow ? null : lastOfficial);
  const officialIsFallback = liveOfficial == null && officialShown != null;
  const rejectReason = data?.official_reject_reason ?? null;
  const officialRaw =
    data?.official_raw != null && Number.isFinite(data.official_raw) ? data.official_raw : null;
  const lastAsof = data?.last?.asof ?? clientLast?.asof ?? null;
  const rejectLabel = rejectReason ? (REJECT_LABEL[rejectReason] ?? rejectReason) : null;
  const officialSub =
    liveOfficial != null
      ? "Sanitized Kite field"
      : officialIsFallback
        ? `Last in-window tick${lastAsof ? ` · ${formatAsOf(lastAsof)}` : ""}`
        : rejectLabel
          ? `${rejectLabel}${officialRaw != null ? ` · raw ${fmt(officialRaw)}` : ""}`
          : "Unavailable";

  const heroLabel = heroIsEstimate
    ? "Pre-close forecast"
    : heroIsLast
      ? "Official indicative (last)"
      : liveOfficial != null
        ? "Official indicative"
        : "Pre-close forecast";
  const heroSub = heroIsEstimate
    ? `Desk estimate (not official CAS)${estimateMethod ? ` · ${estimateMethod}` : ""}`
    : heroIsLast
      ? "Cached / last sane tick"
      : liveOfficial != null
        ? "Sanitized Kite / NSE indicative"
        : estimateMethod
          ? `Desk estimate · ${estimateMethod}`
          : "Unavailable";

  const refWindowLabel =
    comps?.ref_vwap_window === "pre_close_1515"
      ? "15:00–15:15 complete"
      : comps?.ref_vwap_window === "running_1500"
        ? "15:00→now (running)"
        : comps?.ref_vwap_window === "session"
          ? "Session VWAP 09:15→now"
          : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 bg-background p-3 text-foreground md:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Gavel className="h-5 w-5 text-sky-700" />
          <h1 className="text-lg font-semibold tracking-tight">CAS / Pre-close</h1>
          <span className="text-xs text-muted-foreground">
            NIFTY · close forecast before 15:15 · read-only
          </span>
          {inWindow ? (
            <Badge className="bg-bull text-bull-foreground hover:bg-bull">In CAS</Badge>
          ) : (
            <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">
              Outside CAS
            </Badge>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {authError ? (
            <Link to="/login" className="text-sm text-sky-700 underline">
              Kite login required
            </Link>
          ) : null}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8"
            onClick={() => setAutoRefresh((v) => !v)}
          >
            {autoRefresh ? (
              <Pause className="mr-1.5 h-3.5 w-3.5" />
            ) : (
              <Play className="mr-1.5 h-3.5 w-3.5" />
            )}
            {autoRefresh ? "Pause" : "Resume"}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8"
            disabled={loading || !enabledIndex}
            onClick={() => void load(false)}
          >
            {loading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
            Refresh
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-sm border border-border bg-card px-3 py-2 shadow-sm">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Index
        </span>
        {INDEXES.map((item) => (
          <button
            key={item.id}
            type="button"
            disabled={!item.enabled}
            onClick={() => item.enabled && setIndex(item.id)}
            className={cn(
              "rounded-sm border px-3 py-1 text-sm font-medium transition-colors",
              item.id === index && item.enabled
                ? "border-sky-600 bg-sky-50 text-sky-800"
                : item.enabled
                  ? "border-border bg-card text-foreground/90 hover:bg-accent"
                  : "cursor-not-allowed border-border/60 bg-muted/50 text-muted-foreground/70",
            )}
            title={item.enabled ? item.label : "Coming soon"}
          >
            {item.label}
            {!item.enabled ? (
              <span className="ml-1.5 text-[10px] font-normal uppercase tracking-wide">Soon</span>
            ) : null}
          </button>
        ))}
        <span className="ml-auto text-xs text-muted-foreground">
          Poll {inWindow ? "8s" : "15s"} while open · source {data?.source ?? "—"} · asof{" "}
          {formatAsOf(data?.asof)}
        </span>
      </div>

      {error ? (
        <div className="rounded-sm border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          {error}
        </div>
      ) : null}

      {!enabledIndex ? (
        <div className="rounded-sm border border-border bg-card px-4 py-10 text-center text-sm text-muted-foreground shadow-sm">
          {index} CAS desk — coming soon. NIFTY is live for v1.
        </div>
      ) : (
        <Tabs defaultValue="indicative" className="w-full">
          <TabsList className="h-9 bg-muted">
            <TabsTrigger value="indicative">Indicative</TabsTrigger>
            <TabsTrigger value="equilibrium">Equilibrium</TabsTrigger>
            <TabsTrigger value="methodology">Methodology</TabsTrigger>
          </TabsList>

          <TabsContent value="indicative" className="mt-3 flex flex-col gap-3">
            {!inWindow && estimate != null && !heroIsLast ? (
              <div className="rounded-sm border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
                Pre-close forecast (before / outside CAS 15:15–15:35 IST). Desk proxy from
                Synth F, Fut LTP, and VWAP — not the official CAS equilibrium.
                {refWindowLabel ? ` VWAP window: ${refWindowLabel}.` : ""}
              </div>
            ) : null}
            {heroIsLast ? (
              <div className="rounded-sm border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                Outside CAS window (15:15–15:35 IST). Forecast unavailable; showing last sane
                official indicative
                {data?.last?.asof || clientLast?.asof
                  ? ` from ${formatAsOf(data?.last?.asof ?? clientLast?.asof)}`
                  : ""}
                .
              </div>
            ) : null}
            {inWindow && liveOfficial == null ? (
              <div className="rounded-sm border border-border bg-muted/50 px-3 py-2 text-sm text-foreground/90">
                Official Kite index indicative unavailable
                {rejectLabel ? (
                  <>
                    {" "}
                    — <span className="font-medium">{rejectLabel.toLowerCase()}</span>
                    {officialRaw != null ? ` (Kite sent ${fmt(officialRaw)})` : ""}
                  </>
                ) : null}
                . Hero shows the desk{" "}
                <span className="font-medium">pre-close forecast</span>
                {estimateMethod ? ` (${estimateMethod})` : ""} — not official CAS equilibrium.
              </div>
            ) : null}

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <KpiCard
                label={heroLabel}
                value={fmt(heroValue)}
                sub={heroSub}
                tone={heroValue == null ? "muted" : "default"}
              />
              <KpiCard label="LTP / spot" value={fmt(spot)} sub="Continuous index (desk math)" />
              <KpiCard
                label="Δ vs spot"
                value={
                  deltaPts != null
                    ? `${formatSigned(deltaPts)} pts${deltaPct != null ? ` (${formatSigned(deltaPct)}%)` : ""}`
                    : "—"
                }
                tone={
                  deltaPts == null
                    ? "muted"
                    : deltaPts > 0
                      ? "up"
                      : deltaPts < 0
                        ? "down"
                        : "default"
                }
                sub={heroIsEstimate ? "Estimate − LTP" : "Indicative − LTP"}
              />
              <KpiCard
                label="Window"
                value={inWindow ? "In CAS" : "Outside CAS"}
                sub="Cash auction 15:15–15:35 IST"
                tone={inWindow ? "up" : "muted"}
              />
            </div>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <KpiCard
                label="Official indicative"
                value={fmt(officialShown)}
                sub={officialSub}
                tone={officialShown == null ? "muted" : "default"}
              />
              <KpiCard
                label="Fut POC"
                value={fmt(futPoc)}
                sub={
                  futSymbol
                    ? `${futSymbol}${data?.session_poc?.total_volume != null ? ` · vol ${fmt(data.session_poc.total_volume, 0)}` : ""}`
                    : "Session futures volume POC"
                }
                tone={futPoc == null ? "muted" : "default"}
              />
              <KpiCard
                label="Synth F"
                value={fmt(synthF)}
                sub={
                  synth
                    ? `${synth.expiry} · ATM ${fmt(synth.atm_strike, 0)}${synth.price_source ? ` · ${synth.price_source}` : ""}`
                    : "Nearest expiry ATM synthetic"
                }
                tone={synthF == null ? "muted" : "default"}
              />
              <KpiCard
                label="Basis vs spot"
                value={
                  basisVsSpot != null
                    ? `${formatSigned(basisVsSpot)} pts${
                        basisVsCas != null
                          ? ` · vs CAS ${formatSigned(basisVsCas)}`
                          : ""
                      }`
                    : "—"
                }
                tone={
                  basisVsSpot == null
                    ? "muted"
                    : basisVsSpot > 0
                      ? "up"
                      : basisVsSpot < 0
                        ? "down"
                        : "default"
                }
                sub="Synth F − LTP"
              />
            </div>

            <CasHistoryChart series={history?.series ?? []} loading={loading} fromHHMM="15:00" />

            <div className="rounded-sm border border-border bg-card px-4 py-3 text-sm text-muted-foreground shadow-sm">
              Display-only pre-close forecast — not official CAS. GEX / OI Movers / spot math
              continue to use continuous LTP. Compact chips on those desks link here during the
              CAS window.
              {history?.count
                ? ` Chart: ${history.count} recorded polls${
                    history.session ? ` for ${history.session}` : ""
                  } (data/cas_history.jsonl, last 14 sessions).`
                : ""}
            </div>
          </TabsContent>

          <TabsContent value="equilibrium" className="mt-3 flex flex-col gap-3">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <KpiCard label="Reference limit" value={fmt(refLimit)} sub="reference_limit_price" />
              <KpiCard label="Upper circuit" value={fmt(upper)} sub="upper_circuit_limit" />
              <KpiCard label="Lower circuit" value={fmt(lower)} sub="lower_circuit_limit" />
              <KpiCard
                label="Total imbalance"
                value={imbalance == null ? "—" : fmt(imbalance, 0)}
                sub="total_imbalance (when quote provides it)"
              />
            </div>
            {refLimit == null && upper == null && lower == null && imbalance == null ? (
              <div className="rounded-sm border border-border bg-card px-4 py-6 text-sm text-muted-foreground shadow-sm">
                Equilibrium fields are null until Kite quote exposes them for this index (often only
                during the auction). They do not affect GEX/OI calculations.
              </div>
            ) : null}
          </TabsContent>

          <TabsContent value="methodology" className="mt-3">
            <div className="space-y-4 rounded-sm border border-border bg-card px-4 py-4 text-sm leading-relaxed text-foreground/90 shadow-sm">
              <section className="space-y-1">
                <h2 className="text-base font-semibold text-foreground">How Nifty close is formed</h2>
                <p>
                  Under CAS (from ~2026-08-03), the exchange discovers <strong className="font-semibold">stock</strong>{" "}
                  closing prices in a 15:20–15:30 auction (reference = 15:00–15:15 VWAP, ±3% band).
                  The indicative / closing <strong className="font-semibold">index</strong> is a
                  free-float weighted function of those stock closes — not a separate Nifty auction
                  book. Official NSE print follows that rebuild.
                </p>
              </section>

              <section className="space-y-1">
                <h2 className="text-base font-semibold text-foreground">What this page shows</h2>
                <ul className="list-disc space-y-1 pl-5">
                  <li>
                    <strong className="font-semibold">Pre-close forecast</strong> — desk proxy for
                    the expected close <em>before 15:15</em> (and during CAS when official is
                    null). Not official CAS equilibrium. Method:{" "}
                    <span className="font-mono">{estimateMethod ?? "—"}</span>.
                  </li>
                  <li>
                    <strong className="font-semibold">Official indicative</strong> — Kite index{" "}
                    <span className="font-mono">indicative_*</span> only if{" "}
                    <span className="font-mono">|ind − spot| / spot ≤ 3%</span>; else null
                    (rejects garbage like 15 / 1866). Only live inside CAS.
                  </li>
                  <li>
                    <strong className="font-semibold">LTP / spot</strong> — continuous index used by
                    GEX, OI Movers, etc.
                  </li>
                  <li>
                    <strong className="font-semibold">Fut POC / Synth F</strong> — reference levels;
                    synth is <span className="font-mono">F = K + CE_mid − PE_mid</span>.
                  </li>
                </ul>
              </section>

              <section className="space-y-2">
                <h2 className="text-base font-semibold text-foreground">Proxy formula (proxy_v1)</h2>
                <p className="font-mono text-xs sm:text-sm">
                  estimate = 0.40·synth_F + 0.35·fut_ltp + 0.25·ref_vwap
                  <br />
                  (renormalize if a leg is missing; clamp to ref_vwap ± 3%)
                </p>
                <p className="text-xs text-muted-foreground">
                  VWAP ladder: complete 15:00–15:15 when available → running 15:00→now during that
                  window → else session VWAP 09:15→now.
                  {refWindowLabel ? (
                    <>
                      {" "}
                      Current: <span className="font-medium">{refWindowLabel}</span>.
                    </>
                  ) : null}
                </p>
                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  <KpiCard
                    label="Ref VWAP"
                    value={fmt(comps?.ref_vwap)}
                    sub={
                      refWindowLabel
                        ? `${refWindowLabel}${comps?.ref_vwap_source ? ` · ${comps.ref_vwap_source}` : ""}`
                        : comps?.ref_vwap_source
                          ? `source ${comps.ref_vwap_source}`
                          : "VWAP reference"
                    }
                    tone={comps?.ref_vwap == null ? "muted" : "default"}
                  />
                  <KpiCard
                    label="Fut LTP"
                    value={fmt(comps?.fut_ltp)}
                    sub={comps?.fut_symbol ?? "Front-month future"}
                    tone={comps?.fut_ltp == null ? "muted" : "default"}
                  />
                  <KpiCard
                    label="Fut POC"
                    value={fmt(comps?.fut_poc ?? futPoc)}
                    sub="Session volume POC"
                    tone={(comps?.fut_poc ?? futPoc) == null ? "muted" : "default"}
                  />
                  <KpiCard
                    label="Synth F"
                    value={fmt(comps?.synth_f ?? synthF)}
                    sub="ATM synthetic"
                    tone={(comps?.synth_f ?? synthF) == null ? "muted" : "default"}
                  />
                </div>
                {comps?.weights_used && Object.keys(comps.weights_used).length > 0 ? (
                  <p className="text-xs text-muted-foreground">
                    Weights used:{" "}
                    {Object.entries(comps.weights_used)
                      .map(([k, v]) => `${k}=${(Number(v) * 100).toFixed(1)}%`)
                      .join(" · ")}
                    {comps.clamped ? " · clamped to ref VWAP ±3%" : ""}
                  </p>
                ) : null}
              </section>

              <section className="space-y-1">
                <h2 className="text-base font-semibold text-foreground">
                  Constituent rebuild (later — not Objective 1)
                </h2>
                <p>
                  Scaffold only: when ≥90% weight of constituents have valid stock CAS prices,
                  <span className="font-mono"> index_est = Σ(wᵢ·pᵢ) / divisor</span> can promote
                  method to <span className="font-mono">constituent_v1</span>. Full Nifty-50
                  rebuild is deferred — we do not scrape NSE HTML.
                </p>
              </section>

              <p>
                Official NSE notes:{" "}
                <a
                  href="https://www.nseindia.com/market-data/closing-auction-session"
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-sky-700 underline"
                >
                  Closing Auction Session
                </a>
                .
              </p>
            </div>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

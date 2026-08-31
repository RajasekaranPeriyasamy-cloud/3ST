import { useState } from "react";

import type { GammaSnapshot, HhiContribution, HhiStats } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fmt } from "@/components/gamma/concentration/shared";

/**
 * Measurement tab — how much to believe the concentration index.
 *
 * Deliberately built as a *comparison*, not a replacement. Every panel shows the
 * reading the other two tabs give beside the corrected one, because the value of
 * this layer is the gap between them, not the corrected number on its own.
 *
 * Nothing here writes, and nothing on the Profile or Concentration tabs reads
 * `hhi_stats` — this tab can be deleted without touching either.
 */

const FAIL = "#e11d48";

function pct(v: number | null | undefined, digits = 1): string {
  return v == null || !Number.isFinite(v)
    ? "—"
    : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}%`;
}

function num(v: number | null | undefined, digits = 4): string {
  return v == null || !Number.isFinite(v) ? "—" : v.toFixed(digits);
}

function Row({
  label,
  hint,
  before,
  after,
  verdict,
}: {
  label: string;
  hint?: string;
  before: React.ReactNode;
  after: React.ReactNode;
  verdict?: string;
}) {
  return (
    <div className="grid grid-cols-1 gap-2 border-b border-border/60 py-3 last:border-0 sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1fr)]">
      <div>
        <p className="text-sm font-medium">{label}</p>
        {hint ? <p className="mt-0.5 text-[11px] text-muted-foreground">{hint}</p> : null}
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
          On the board now
        </p>
        <div className="font-mono text-base tabular-nums text-muted-foreground">{before}</div>
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">Corrected</p>
        <div className="font-mono text-base font-semibold tabular-nums">{after}</div>
        {verdict ? <p className="mt-0.5 text-[11px] text-muted-foreground">{verdict}</p> : null}
      </div>
    </div>
  );
}

function Unmeasured({ what, why }: { what: string; why: string }) {
  return (
    <div className="flex items-start gap-3 border-b border-border/60 py-3 last:border-0">
      <Badge variant="outline" className="mt-0.5 h-5 shrink-0 px-1.5 text-[10px] font-normal">
        not instrumented
      </Badge>
      <div>
        <p className="text-sm">{what}</p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">{why}</p>
      </div>
    </div>
  );
}

/** Ladder depth, matching the expiry-magnet control on the Concentration tab. */
type Depth = 10 | 20 | "all";
const DEPTHS: { value: string; label: string }[] = [
  { value: "10", label: "Top 10" },
  { value: "20", label: "Top 20" },
  { value: "all", label: "All" },
];

const GRID = "grid grid-cols-[4.5rem_1fr_3.5rem_4rem_4.5rem_4.5rem] gap-2";
/**
 * Sign of dH/dm: more mass here raises the index, or lowers it.
 *
 * Tailwind pairs rather than a literal hex — a single dark-tone colour that reads
 * on light paper loses contrast on the dark ground, and this desk is used in both.
 */
const RAISES = "text-teal-700 dark:text-teal-300";
const LOWERS = "text-amber-700 dark:text-amber-400";

function ContributionTable({ rows }: { rows: HhiContribution[] }) {
  const [depth, setDepth] = useState<Depth>(10);
  const shown = depth === "all" ? rows : rows.slice(0, depth);
  const hidden = rows.length - shown.length;
  const hiddenPct = hidden > 0 ? 100 - shown[shown.length - 1].cum_pct : 0;
  const maxSq = rows[0]?.share_sq ?? 1;

  // d_hhi = 2(p - H), so it crosses zero exactly at share == HHI. Recover H from
  // the relation rather than threading it in: H = p - d/2 for any row.
  const hhiPct = shown.length ? (shown[0].share - shown[0].d_hhi / 2) * 100 : 0;
  const firstNeg = shown.findIndex((r) => r.d_hhi < 0);
  // Only mark the crossover when it actually falls inside the visible rows.
  const crossover = firstNeg > 0 ? firstNeg : -1;

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-sm">Which strikes produce the index</CardTitle>
          <p className="mt-1 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
            HHI = Σ share² · each strike&apos;s own term
          </p>
        </div>
        <Select
          value={String(depth)}
          onValueChange={(v) => setDepth(v === "all" ? "all" : (Number(v) as Depth))}
        >
          <SelectTrigger className="h-7 w-[5.5rem] text-sm" aria-label="Table depth">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {DEPTHS.map((d) => (
              <SelectItem key={d.value} value={d.value}>
                {d.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </CardHeader>

      <CardContent className="space-y-2">
        {/* Six columns do not fit a narrow pane; scroll the table rather than
            dropping a column, so no reading silently disappears. */}
        <div className="overflow-x-auto">
          <div className="min-w-[40rem] space-y-2">
            <div className={`${GRID} text-[10px] uppercase tracking-[0.1em] text-muted-foreground`}>
              <span>Strike</span>
              <span>Contribution</span>
              <span className="text-right">Share</span>
              <span className="text-right">% index</span>
              <span className="text-right">Cumulative</span>
              <span className="text-right">Δ index</span>
            </div>

            {shown.map((r, i) => (
              <div key={r.strike}>
                {/* The sign of dH/dm flips exactly where share crosses HHI. Mark it —
                    it is the boundary between strikes that raise the index as they
                    grow and strikes that lower it. */}
                {i === crossover ? (
                  <div className="my-1.5 flex items-center gap-2">
                    <span className="h-px flex-1 bg-border" aria-hidden />
                    <span className="whitespace-nowrap font-mono text-[10px] tabular-nums text-muted-foreground">
                      share = HHI = {hhiPct.toFixed(1)}% · below here, growth lowers the index
                    </span>
                    <span className="h-px flex-1 bg-border" aria-hidden />
                  </div>
                ) : null}
                <div className={`${GRID} items-center font-mono text-xs tabular-nums`}>
                  <span>{fmt(r.strike)}</span>
                  <div className="h-3.5 overflow-hidden rounded-sm bg-muted">
                    <div
                      className="h-full rounded-sm bg-primary"
                      style={{ width: `${Math.max(1, (r.share_sq / maxSq) * 100)}%` }}
                    />
                  </div>
                  <span className="text-right text-muted-foreground">
                    {(r.share * 100).toFixed(1)}%
                  </span>
                  <span className="text-right font-semibold">{r.pct_of_index.toFixed(1)}%</span>
                  <span className="text-right text-muted-foreground">{r.cum_pct.toFixed(1)}%</span>
                  <span
                    className={`text-right ${r.d_hhi >= 0 ? RAISES : LOWERS}`}
                    title={r.d_hhi >= 0 ? "More mass here raises HHI" : "More mass here lowers HHI"}
                  >
                    {r.d_hhi >= 0 ? "+" : "−"}
                    {Math.abs(r.d_hhi).toFixed(3)}
                  </span>
                </div>
              </div>
            ))}

            {/* Never let a truncated table read as the whole index. */}
            {hidden > 0 ? (
              <div
                className={`${GRID} border-t border-border/60 pt-2 font-mono text-xs tabular-nums text-muted-foreground`}
              >
                <span>+{hidden}</span>
                <span className="font-sans text-[11px]">remaining strikes, combined</span>
                <span />
                <span className="text-right">{hiddenPct.toFixed(1)}%</span>
                <span className="text-right">100.0%</span>
                <span />
              </div>
            ) : null}
          </div>
        </div>

        <p className="pt-1 text-[11px] text-muted-foreground">
          The term is <em>squared</em>, so the index is far more concentrated than the book: a
          strike with twice the share contributes four times the HHI. Read the cumulative column for
          how few strikes actually produce the number — and treat any illiquid leg near the top of
          this list as a reason to distrust it.
        </p>
        <p className="text-[11px] text-muted-foreground">
          <span className="font-mono">Δ index</span> is <span className="font-mono">2(pᵢ − H)</span>
          , the signed sensitivity of HHI to that strike&apos;s mass. It is <em>not</em> a ranking —
          it says which way the index moves if the strike grows. A strike sitting at the crossover
          barely moves it at all, which is the same fact that makes those strikes contribute nothing
          to the index&apos;s standard error.
        </p>
      </CardContent>
    </Card>
  );
}

export function MeasurementBoard({ snapshot }: { snapshot: GammaSnapshot }) {
  const st: HhiStats | null | undefined = snapshot.hhi_stats;

  if (!st) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-base">Measurement quality</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Needs the day-end history block — unavailable for this snapshot.
          </p>
        </CardContent>
      </Card>
    );
  }

  const c = st.cohort;
  const eta = c?.eta_sq ?? null;
  const flips =
    c?.mixed?.vs_mean_pct != null &&
    c?.cohort?.vs_mean_pct != null &&
    Math.sign(c.mixed.vs_mean_pct) !== Math.sign(c.cohort.vs_mean_pct);

  return (
    <div className="flex flex-col gap-6">
      {/* ── Headline: the reading that changes ─────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
          <div>
            <CardTitle className="text-base">Same book, two readings</CardTitle>
            <p className="mt-1 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              what changes when the comparison is made like-for-like
            </p>
          </div>
          {flips ? (
            <Badge variant="outline" style={{ color: FAIL, borderColor: FAIL }}>
              conclusion reverses
            </Badge>
          ) : null}
        </CardHeader>
        <CardContent className="pt-0">
          <Row
            label="Today vs recent sessions"
            hint={
              c?.today_dte != null
                ? `today is ${c.today_dte} DTE · cohort “${c.today_bucket}” · n=${c.cohort?.n ?? 0}`
                : "DTE unavailable"
            }
            before={
              <>
                {pct(c?.mixed?.vs_mean_pct)}{" "}
                <span className="text-[11px]">vs mixed mean {num(c?.mixed?.mean, 4)}</span>
              </>
            }
            after={
              <>
                {pct(c?.cohort?.vs_mean_pct)}{" "}
                <span className="text-[11px] font-normal text-muted-foreground">
                  vs {c?.today_bucket} DTE mean {num(c?.cohort?.mean, 4)}
                </span>
              </>
            }
            verdict={
              flips
                ? "The mixed sample pools expiry days with the rest of the week — the sign of the conclusion is an artefact of that."
                : undefined
            }
          />
          <Row
            label="Percentile"
            hint="mixed sample vs DTE-matched cohort"
            before={
              c?.mixed?.percentile != null
                ? `${c.mixed.percentile.toFixed(1)}th of ${c.mixed.n}`
                : "—"
            }
            after={
              c?.cohort?.percentile != null ? (
                `${c.cohort.percentile.toFixed(1)}th of ${c.cohort.n}`
              ) : (
                <span className="text-sm font-normal text-muted-foreground">
                  withheld — cohort n={c?.cohort?.n ?? 0} &lt; {c?.min_cohort_for_percentile}
                </span>
              )
            }
            verdict={
              c?.cohort?.percentile == null
                ? "A percentile over a handful of sessions can take only a handful of values. The cohort mean above is reported instead."
                : undefined
            }
          />
          <Row
            label="Window sensitivity"
            hint={`raw HHI has a floor of 1/N = ${num(st.floor, 4)} at N=${st.n_strikes ?? "—"}`}
            before={num(st.hhi, 4)}
            after={
              <>
                {num(st.hhi_norm, 4)}{" "}
                <span className="text-[11px] font-normal text-muted-foreground">H*</span>
              </>
            }
            verdict="H* rescales the floor away, so a wider strike window no longer reads as a less concentrated book."
          />
        </CardContent>
      </Card>

      {/* ── Why: variance decomposition ────────────────────────────────── */}
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">Why the two disagree</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div>
              <p className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
                variance explained by DTE
              </p>
              <p className="font-mono text-4xl font-light tabular-nums leading-none">
                {eta != null ? `${(eta * 100).toFixed(0)}%` : "—"}
              </p>
            </div>
            <p className="max-w-[46ch] text-sm text-muted-foreground">
              of the spread in the recorded HHI history is position in the expiry cycle, not book
              structure. A percentile over a DTE-mixed sample reports mostly-calendar as though it
              were mostly-market.
            </p>
          </div>
          {eta != null ? (
            <div className="h-2 w-full overflow-hidden rounded-sm bg-muted">
              <div
                className="h-full rounded-sm bg-primary"
                style={{ width: `${Math.max(0, Math.min(100, eta * 100))}%` }}
              />
            </div>
          ) : null}
          <p className="text-[11px] text-muted-foreground">
            One-way η² over {c?.n_total ?? 0} recorded sessions, bucketed 0 / 1–2 / 3–7 / 8+ DTE.
            DTE is derived from the row date at read time — no stored row is modified.
          </p>
        </CardContent>
      </Card>

      {/* ── Shape: the Hill profile ────────────────────────────────────── */}
      {st.hill ? (
        <Card>
          <CardHeader className="flex flex-row items-baseline justify-between gap-2 py-3">
            <CardTitle className="text-sm">Concentration profile</CardTitle>
            <span className="text-[11px] text-muted-foreground">
              effective strikes by order · HHI is order 2 alone
            </span>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {st.hill.map((h) => {
              const max = Math.max(...(st.hill ?? []).map((x) => x.n_eff ?? 0), 1);
              const w = h.n_eff != null ? (h.n_eff / max) * 100 : 0;
              const isHhi = h.order === 2;
              return (
                <div key={String(h.order)} className="flex items-center gap-3">
                  <span className="w-16 shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
                    N<sub>{h.order === "inf" ? "∞" : h.order}</sub>
                  </span>
                  <div className="h-4 flex-1 overflow-hidden rounded-sm bg-muted">
                    <div
                      className={`h-full rounded-sm ${isHhi ? "bg-primary" : "bg-muted-foreground/40"}`}
                      style={{ width: `${w}%` }}
                    />
                  </div>
                  <span className="w-14 shrink-0 text-right font-mono text-xs tabular-nums">
                    {h.n_eff != null ? h.n_eff.toFixed(1) : "—"}
                  </span>
                  {isHhi ? (
                    <span className="w-16 shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                      = 1/HHI
                    </span>
                  ) : (
                    <span className="w-16 shrink-0" />
                  )}
                </div>
              );
            })}
            <p className="pt-1 text-[11px] text-muted-foreground">
              A steep fall from N<sub>0</sub> to N<sub>∞</sub> means one strike dominates; a flat
              profile means broad, even mass. This is what reconciles a “balanced” HHI sitting
              beside an “unequal” Gini on the Concentration tab.
            </p>
          </CardContent>
        </Card>
      ) : null}

      {/* ── Per-strike decomposition ───────────────────────────────────── */}
      {st.contributions && st.contributions.length > 0 ? (
        <ContributionTable rows={st.contributions} />
      ) : null}

      {/* ── What is still not measured ─────────────────────────────────── */}
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">Input quality</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <Row
            label="Legs surviving the filter"
            hint="by count — a dropped leg may have carried any amount of gamma"
            before={<span className="text-sm">not shown</span>}
            after={
              st.quality.legs_total != null ? (
                <>
                  {st.quality.legs_quoted}/{st.quality.legs_total}{" "}
                  <span className="text-[11px] font-normal text-muted-foreground">
                    {st.quality.legs_dropped_pct != null
                      ? `· ${st.quality.legs_dropped_pct.toFixed(0)}% dropped`
                      : ""}
                  </span>
                </>
              ) : (
                "—"
              )
            }
            verdict="A jump here is a filter event, not a concentration event. Worth checking before reading a move in HHI as real."
          />
          <Unmeasured
            what="Standard error on HHI"
            why="The delta-method SE is implemented and tested, but per-leg quote uncertainty is not on the strike rows yet — they carry IV without bid/ask. Reported as null rather than fabricated from an assumed error model."
          />
          <Unmeasured
            what="Mass-weighted dropped share"
            why="Requires the mass of filtered-out legs, which is discarded before this point. The leg count above is the honest proxy available today."
          />
        </CardContent>
      </Card>

      <p className="text-[11px] text-muted-foreground">
        This tab is additive. The Profile and Concentration tabs compute exactly as before and read
        none of these fields; <span className="font-mono">hhi_stats</span> is a new payload block
        and <span className="font-mono">options/hhi_stats.py</span> performs no I/O.
      </p>
    </div>
  );
}

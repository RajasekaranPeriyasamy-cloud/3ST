import type { ExpiryMagnet, ExpiryPinState } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CE_COLOR, PE_COLOR, fmt, gexIndian } from "./shared";

/** The pin colour. Deliberately the same red the ladder and levels already use. */
const PIN_RED = "#ef4444";

const STATE_ORDER: { key: ExpiryPinState; label: string; blurb: string }[] = [
  { key: "no_pin", label: "No pin", blurb: "Gamma is spread, nothing anchors" },
  { key: "shifting", label: "Shifting", blurb: "A leader exists, but it keeps changing" },
  { key: "stable", label: "Stable", blurb: "A clear leader, not yet entrenched" },
  { key: "locked", label: "Locked", blurb: "One strike dominates and has held" },
];

const STATE_TONE: Record<string, string> = {
  locked: "text-emerald-600 dark:text-emerald-400",
  stable: "text-sky-600 dark:text-sky-400",
  shifting: "text-amber-600 dark:text-amber-400",
  no_pin: "text-muted-foreground",
};

function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <p className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p
        className={`font-mono text-xl font-semibold tabular-nums leading-tight ${tone ?? ""}`}
      >
        {value}
      </p>
      {hint ? <p className="text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** Horizontal pressure bar for one strike. */
function LadderRow({
  row,
  isPin,
  rank,
}: {
  row: ExpiryMagnet["ladder"][number];
  isPin: boolean;
  rank?: number;
}) {
  const pct = Math.max(0, Math.min(100, row.pressure * 100));
  const short = row.net_gamma < 0;
  return (
    <div className={`flex items-center gap-2 text-xs ${isPin ? "font-semibold" : ""}`}>
      <span
        className="w-16 shrink-0 text-right font-mono tabular-nums"
        style={isPin ? { color: PIN_RED } : undefined}
      >
        {fmt(row.strike)}
      </span>
      <span className="w-6 shrink-0 font-mono text-[11px] text-muted-foreground">
        {rank ? `#${rank}` : ""}
      </span>
      <div className="relative h-2.5 flex-1 overflow-hidden rounded-sm bg-muted/60">
        <div
          className="absolute inset-y-0 left-0 rounded-sm"
          style={{
            width: `${pct}%`,
            background: isPin ? PIN_RED : "#8b8fa3",
            opacity: isPin ? 1 : 0.55,
          }}
        />
      </div>
      <span
        className="w-11 shrink-0 text-right font-mono tabular-nums"
        style={isPin ? { color: PIN_RED } : undefined}
      >
        {pct.toFixed(0)}%
      </span>
      <span className="w-20 shrink-0 text-right font-mono tabular-nums text-muted-foreground">
        {gexIndian(row.gamma).replace("+", "")}
      </span>
      <span
        className="w-24 shrink-0 text-right font-mono tabular-nums"
        style={{ color: short ? CE_COLOR : PE_COLOR }}
      >
        {gexIndian(row.net_gamma)}
        <span className="ml-1 text-[10px] uppercase text-muted-foreground">
          {short ? "short" : "long"}
        </span>
      </span>
    </div>
  );
}

export function ExpiryMagnetPanel({ em }: { em: ExpiryMagnet | null | undefined }) {
  if (!em) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-base">Expiry magnet</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Needs an expected move and strike gamma — unavailable for this snapshot.
          </p>
        </CardContent>
      </Card>
    );
  }

  const c = em.conviction;
  const ladder = [...em.ladder].sort((a, b) => b.strike - a.strike);
  const rankByStrike = new Map(em.top.map((t) => [t.strike, t.rank]));
  const spot = em.pin - em.distance_pts;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-base">Expiry magnet</CardTitle>
          <p className="mt-1 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
            pressure = γ × chance of settling there
          </p>
        </div>
        <div className="text-right">
          <p className={`font-mono text-lg font-semibold ${STATE_TONE[em.state] ?? ""}`}>
            {em.state_label.toUpperCase()}
          </p>
          {c.score != null ? (
            <p className="font-mono text-[11px] tabular-nums text-muted-foreground">
              conviction {c.score.toFixed(0)}
            </p>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-x-5 gap-y-1">
          <p
            className="font-mono text-4xl font-light tabular-nums leading-none"
            style={{ color: PIN_RED }}
          >
            {fmt(em.pin)}
          </p>
          <p className="pb-1 text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
            {em.distance_pts >= 0 ? "+" : "−"}
            {Math.abs(em.distance_pts).toFixed(0)} pts from spot
            {em.distance_sigma != null
              ? ` · ${Math.abs(em.distance_sigma * 100).toFixed(0)}% of expiry σ`
              : ""}
          </p>
        </div>

        <p className="text-sm leading-relaxed text-muted-foreground">
          {em.state_description}
          {em.time_boost != null && em.time_boost > 1.05 ? (
            <>
              {" "}
              The pull is not constant — it strengthens as expiry nears, because σ collapses onto
              the strike. Right now it is{" "}
              <span className="font-medium text-foreground">{em.time_boost.toFixed(2)}×</span> what
              the same book would exert {em.time_boost_reference_dte.toFixed(0)} sessions out.
            </>
          ) : null}
        </p>

        {/* Pin-state ladder */}
        <div className="space-y-1 border-t border-border/60 pt-3">
          {STATE_ORDER.map((s) => {
            const active = s.key === em.state;
            return (
              <div
                key={s.key}
                className={`flex gap-3 border-l-2 py-0.5 pl-2 text-xs ${
                  active ? "border-l-current" : "border-transparent"
                } ${active ? STATE_TONE[s.key] : "text-muted-foreground/60"}`}
              >
                <span className="w-16 shrink-0 uppercase tracking-[0.08em]">{s.label}</span>
                <span className={active ? "text-foreground" : ""}>{s.blurb}</span>
              </div>
            );
          })}
        </div>

        <div className="grid grid-cols-2 gap-x-6 gap-y-3 border-t border-border/60 pt-3 sm:grid-cols-4">
          <Metric label="Γ at pin" value={gexIndian(em.pin_gamma).replace("+", "")} hint="dealer gamma load" />
          <Metric
            label="Runner-up"
            value={em.runner_up != null ? fmt(em.runner_up) : "—"}
            hint={em.margin != null ? `margin ${(em.margin * 100).toFixed(1)}%` : undefined}
          />
          <Metric
            label="σ to expiry"
            value={em.sigma_pts != null ? `${em.sigma_pts.toFixed(0)} pts` : "—"}
            hint={em.dte != null ? `${em.dte} DTE` : undefined}
          />
          <Metric
            label="Time boost"
            value={em.time_boost != null ? `${em.time_boost.toFixed(2)}×` : "—"}
            hint={
              em.time_boost != null && em.time_boost >= 2
                ? "strong · dealers hedging hard"
                : "vs a calmer horizon"
            }
          />
        </div>

        {/* Pressure by strike, high to low — a price ladder. */}
        <div className="space-y-1 border-t border-border/60 pt-3">
          <div className="flex items-baseline justify-between pb-1">
            <span className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              Dealer pressure by strike
            </span>
            <span className="text-[11px] text-muted-foreground">
              leader = 100% · Γ ₹Cr · net Γ
            </span>
          </div>
          {ladder.map((row, i) => (
            <div key={row.strike}>
              {i > 0 && ladder[i - 1].strike > spot && row.strike < spot ? (
                <div className="my-1 flex items-center gap-2">
                  <span className="h-px flex-1 bg-sky-500/40" aria-hidden />
                  <span className="font-mono text-[11px] tabular-nums text-sky-600 dark:text-sky-400">
                    spot {fmt(spot)}
                  </span>
                  <span className="h-px flex-1 bg-sky-500/40" aria-hidden />
                </div>
              ) : null}
              <LadderRow
                row={row}
                isPin={row.strike === em.pin}
                rank={rankByStrike.get(row.strike)}
              />
            </div>
          ))}
        </div>

        {/* Conviction is a summary of its parts, and says so. */}
        <div className="space-y-1.5 border-t border-border/60 pt-3">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
              Conviction components
            </span>
            {!c.calibrated ? (
              <Badge variant="outline" className="h-5 px-1.5 text-[10px] font-normal">
                provisional
              </Badge>
            ) : null}
          </div>
          {Object.entries(c.parts).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2 text-[11px]">
              <span className="w-20 shrink-0 capitalize text-muted-foreground">{k}</span>
              <div className="relative h-1.5 flex-1 overflow-hidden rounded-sm bg-muted/60">
                {v != null ? (
                  <div
                    className="absolute inset-y-0 left-0 rounded-sm bg-primary"
                    style={{ width: `${Math.min(100, Math.max(0, v * 100))}%` }}
                  />
                ) : null}
              </div>
              <span className="w-12 shrink-0 text-right font-mono tabular-nums">
                {v == null ? "n/a" : `${(v * 100).toFixed(0)}%`}
              </span>
              <span className="w-10 shrink-0 text-right font-mono text-[10px] text-muted-foreground">
                ×{c.weights[k]?.toFixed(2) ?? "—"}
              </span>
            </div>
          ))}
          <p className="pt-1 text-[11px] text-muted-foreground">
            Weights are reasoned, not fitted — the score summarises the four components above
            rather than measuring anything on its own. A component with no data is dropped and
            the rest re-weighted, never scored zero.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

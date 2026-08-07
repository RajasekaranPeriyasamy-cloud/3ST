import { useAnalyticsDesk } from "@/context/AnalyticsDeskContext";
import type { VolSurfaceSnapshot } from "@/lib/types";
import { WidgetShell } from "./WidgetShell";
import { useWidgetPoll } from "./useWidgetPoll";

function ivColor(iv: number | null, lo: number, hi: number): string {
  if (iv == null) return "transparent";
  const t = hi === lo ? 0.5 : Math.max(0, Math.min(1, (iv - lo) / (hi - lo)));
  const hue = 220 * (1 - t);
  return `hsl(${hue}, 72%, 46%)`;
}

export function VolSurfaceWidget() {
  const { underlying } = useAnalyticsDesk();
  const url = `/vol-surface/snapshot?underlying=${encodeURIComponent(underlying)}&strike_count=11&max_expiries=4`;
  const { data, loading, error, authError } = useWidgetPoll<VolSurfaceSnapshot>(url);

  const flat = data?.z.flat().filter((v): v is number => v != null) ?? [];
  const lo = flat.length ? Math.min(...flat) : 0;
  const hi = flat.length ? Math.max(...flat) : 1;

  return (
    <WidgetShell
      title="Vol Surface"
      fullRoute="/vol-surface"
      loading={loading}
      authError={authError}
      error={error}
      meta={
        data ? (
          <>
            Spot {data.spot.toFixed(1)} · ATM {data.atm_strike} · {data.legs_resolved} legs
          </>
        ) : null
      }
    >
      {!data ? (
        <p className="py-8 text-center text-xs text-muted-foreground">
          {loading ? "Loading…" : "No surface data"}
        </p>
      ) : (
        <div className="space-y-2">
          <div className="max-h-[280px] overflow-auto">
            <table className="border-separate border-spacing-0 text-[10px]">
              <thead>
                <tr>
                  <th className="sticky left-0 top-0 z-10 bg-card px-1.5 py-1 text-left text-muted-foreground">
                    DTE
                  </th>
                  {data.strikes.map((s) => (
                    <th key={s} className="sticky top-0 bg-card px-1 py-1 font-mono font-normal text-muted-foreground">
                      {s}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.expiries.map((exp, i) => (
                  <tr key={exp.expiry}>
                    <td className="sticky left-0 bg-card px-1.5 py-1 font-mono text-muted-foreground">
                      {exp.dte}d
                    </td>
                    {data.z[i].map((iv, j) => (
                      <td
                        key={j}
                        className="px-1 py-1 text-center font-mono text-white"
                        style={{ backgroundColor: ivColor(iv, lo, hi) }}
                        title={`${exp.expiry} · ${data.strikes[j]} · ${iv ?? "—"}%`}
                      >
                        {iv != null ? iv.toFixed(0) : ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
            <span>{lo.toFixed(1)}%</span>
            <div
              className="h-1.5 w-28 rounded"
              style={{ background: "linear-gradient(90deg, hsl(220,72%,46%), hsl(0,72%,46%))" }}
            />
            <span>{hi.toFixed(1)}%</span>
          </div>
        </div>
      )}
    </WidgetShell>
  );
}

import { useMemo } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useAnalyticsDesk } from "@/context/AnalyticsDeskContext";
import type { OiVarRow, OiVarSnapshot } from "@/lib/types";
import { WidgetShell } from "./WidgetShell";
import { useWidgetPoll } from "./useWidgetPoll";

const POS = "#059669";
const NEG = "#e11d48";

function MiniBoard({
  side,
  rows,
}: {
  side: "CE" | "PE";
  rows: OiVarRow[];
}) {
  const list = rows.slice(0, 8);
  return (
    <div className="min-w-0 flex-1">
      <div className="mb-1 text-[10px] font-semibold tracking-wide text-muted-foreground">
        {side} · TOP VAR
      </div>
      <div className="overflow-hidden rounded border border-border/60">
        <table className="w-full text-[10px] font-mono">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-1.5 py-1 text-left font-medium">K</th>
              <th className="px-1.5 py-1 text-right font-medium">VAR</th>
              <th className="px-1.5 py-1 text-right font-medium">ΔVAR</th>
            </tr>
          </thead>
          <tbody>
            {list.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-1.5 py-3 text-center text-muted-foreground">
                  —
                </td>
              </tr>
            ) : (
              list.map((r) => (
                <tr key={`${side}-${r.strike}`} className="border-t border-border/40">
                  <td className="px-1.5 py-0.5">{r.strike}</td>
                  <td className="px-1.5 py-0.5 text-right">{r.var_cr?.toFixed(1) ?? "—"}</td>
                  <td
                    className={`px-1.5 py-0.5 text-right ${
                      (r.var_chg_cr ?? 0) >= 0 ? "text-emerald-600" : "text-red-600"
                    }`}
                  >
                    {r.var_chg_cr?.toFixed(1) ?? "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function OiVarWidget({ expanded = false }: { expanded?: boolean }) {
  const { underlying, expiry } = useAnalyticsDesk();
  const url = expiry
    ? `/oi-var/snapshot?underlying=${encodeURIComponent(underlying)}&expiry=${encodeURIComponent(expiry)}&multi_expiry=false&gamma_context=false`
    : null;
  const { data, loading, error, authError } = useWidgetPoll<OiVarSnapshot>(url);

  const chartData = useMemo(
    () =>
      (data?.var_profile ?? []).map((r) => ({
        strike: r.strike,
        ce: r.ce_var,
        pe: -r.pe_var,
      })),
    [data],
  );

  const s = data?.summary;
  const ceTop = data?.calls?.top_var ?? data?.calls?.top_oi ?? [];
  const peTop = data?.puts?.top_var ?? data?.puts?.top_oi ?? [];
  const chartH = expanded ? 320 : 200;

  return (
    <WidgetShell
      title={expanded ? "OI VAR · Full view" : "OI VAR"}
      fullRoute="/oi-var"
      deskFocus="oi-var"
      loading={loading}
      authError={authError}
      error={error}
      meta={
        data ? (
          <>
            Spot {data.spot.toFixed(0)}
            {s ? (
              <>
                {" · "}CE {s.ce_var_total?.toFixed(1) ?? "—"} · PE {s.pe_var_total?.toFixed(1) ?? "—"} · PCR{" "}
                {s.pcr_var?.toFixed(2) ?? "—"} · Δ {s.net_dvar?.toFixed(1) ?? "—"} Cr
              </>
            ) : null}
          </>
        ) : null
      }
    >
      {!data ? (
        <p className="py-8 text-center text-xs text-muted-foreground">
          {loading ? "Loading…" : "No VAR data"}
        </p>
      ) : (
        <div className={`flex h-full min-h-0 flex-col gap-2 ${expanded ? "gap-3" : ""}`}>
          <ResponsiveContainer width="100%" height={chartH}>
            <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
              <XAxis dataKey="strike" type="number" domain={["dataMin", "dataMax"]} tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} width={36} />
              <Tooltip contentStyle={{ fontSize: 11, borderRadius: 8 }} />
              <ReferenceLine y={0} stroke="#94a3b8" />
              <ReferenceLine x={data.spot} stroke="#ea580c" strokeWidth={1.5} />
              <Bar dataKey="ce" name="CE VAR" radius={[2, 2, 0, 0]}>
                {chartData.map((_, i) => (
                  <Cell key={i} fill={POS} />
                ))}
              </Bar>
              <Bar dataKey="pe" name="PE VAR" radius={[2, 2, 0, 0]}>
                {chartData.map((_, i) => (
                  <Cell key={`p-${i}`} fill={NEG} />
                ))}
              </Bar>
            </ComposedChart>
          </ResponsiveContainer>

          {expanded ? (
            <div className="grid min-h-0 flex-1 grid-cols-2 gap-3 overflow-auto">
              <MiniBoard side="CE" rows={ceTop} />
              <MiniBoard side="PE" rows={peTop} />
            </div>
          ) : null}
        </div>
      )}
    </WidgetShell>
  );
}

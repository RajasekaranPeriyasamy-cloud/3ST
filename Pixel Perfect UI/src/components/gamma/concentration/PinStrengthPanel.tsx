import type { GammaPinLock, GammaPinWindow } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { CE_COLOR, PE_COLOR, compactOi, fmt } from "./shared";

const WINDOWS: GammaPinWindow[] = ["15m", "30m", "60m", "session"];

/** Only `dominant` is a real gamma pin — see the backend `pin_source` note. */
const SOURCE_LABEL: Record<string, string> = {
  dominant: "gamma pin",
  wall_mid: "wall midpoint · inferred",
  atm: "ATM placeholder · not a pin",
  fallback: "fallback",
};

function GateRow({ ok, label }: { ok: boolean | null; label: string }) {
  const tone =
    ok === true
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
      : ok === false
        ? "border-rose-500/40 bg-rose-500/10 text-rose-700 dark:text-rose-300"
        : "border-border bg-muted text-muted-foreground";
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <Badge variant="outline" className={`${tone} h-5 px-1.5 font-mono text-[10px]`}>
        {ok === true ? "PASS" : ok === false ? "FAIL" : "N/A"}
      </Badge>
      <span className={ok === false ? "text-foreground" : "text-muted-foreground"}>{label}</span>
    </div>
  );
}

/**
 * A component reading. `pct` drives the bar; `null` renders as unmeasured rather
 * than as zero — an empty session must not look like a broken pin.
 */
function Component({
  label,
  value,
  pct,
  hint,
  tone,
}: {
  label: string;
  value: string;
  pct: number | null;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
          {label}
        </span>
        <span className="font-mono text-xs tabular-nums" style={tone ? { color: tone } : undefined}>
          {value}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-sm bg-muted/60">
        {pct != null ? (
          <div
            className="h-full rounded-sm"
            style={{
              width: `${Math.min(100, Math.max(0, pct))}%`,
              background: tone ?? "hsl(var(--primary))",
            }}
          />
        ) : null}
      </div>
      {hint ? <p className="text-[10px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export function PinStrengthPanel({
  pinLock,
  window: win,
  onWindowChange,
}: {
  pinLock: GammaPinLock | null | undefined;
  window?: GammaPinWindow;
  onWindowChange?: (w: GammaPinWindow) => void;
}) {
  const g = pinLock?.gates;
  const c = pinLock?.components;
  const passed = g?.passed ?? null;

  const verdict =
    passed === true ? "PINNED" : passed === false ? "NOT PINNED" : "UNMEASURED";
  const verdictTone =
    passed === true
      ? "text-emerald-600 dark:text-emerald-400"
      : passed === false
        ? "text-rose-600 dark:text-rose-400"
        : "text-muted-foreground";

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2 py-3">
        <div>
          <CardTitle className="text-sm">Pin strength</CardTitle>
          <p className="mt-1 text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
            gates + components · no blended score until calibrated
          </p>
        </div>
        {onWindowChange ? (
          <Select value={win ?? "30m"} onValueChange={(v) => onWindowChange(v as GammaPinWindow)}>
            <SelectTrigger className="h-7 w-[5.5rem] text-xs" aria-label="Pin window">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOWS.map((w) => (
                <SelectItem key={w} value={w}>
                  {w}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-end gap-x-5 gap-y-1">
          <p className={`font-mono text-3xl font-light tabular-nums ${verdictTone}`}>{verdict}</p>
          <div className="pb-1">
            <p className="font-mono text-sm font-semibold tabular-nums">
              {fmt(pinLock?.pin_mode ?? pinLock?.pin)}
            </p>
            <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              {pinLock?.pin_source
                ? (SOURCE_LABEL[pinLock.pin_source] ?? pinLock.pin_source)
                : "no pin"}
            </p>
          </div>
        </div>

        <div className="space-y-1 border-t border-border/60 pt-3">
          <GateRow ok={g?.pin_is_dominant ?? null} label="dominant strike holds the gamma" />
          <GateRow
            ok={g?.dealers_long_gamma ?? null}
            label={
              g?.long_gamma_share != null
                ? `dealers long gamma (${(g.long_gamma_share * 100).toFixed(0)}% of window)`
                : "dealers long gamma"
            }
          />
        </div>

        <div className="grid gap-3 border-t border-border/60 pt-3 sm:grid-cols-2">
          <Component
            label="Pin stability"
            value={c?.stability_pct != null ? `${c.stability_pct.toFixed(0)}%` : "—"}
            pct={c?.stability_pct ?? null}
            hint="ticks on the modal pin"
          />
          <Component
            label="Containment"
            value={c?.containment_pct != null ? `${c.containment_pct.toFixed(0)}%` : "—"}
            pct={c?.containment_pct ?? null}
            hint={`minutes within ${c?.containment_steps ?? 1} strike step`}
          />
          <Component
            label="Crossings"
            value={c?.crossings_per_hour != null ? `${c.crossings_per_hour.toFixed(1)}/h` : "—"}
            pct={c?.crossings_per_hour != null ? Math.min(100, c.crossings_per_hour * 10) : null}
            hint="spot oscillating across the pin"
          />
          <Component
            label="Flip room"
            value={c?.flip_room_sigma != null ? `${c.flip_room_sigma.toFixed(2)}σ` : "—"}
            pct={c?.flip_room_sigma != null ? Math.min(100, c.flip_room_sigma * 50) : null}
            hint={c?.flip_room_ok === false ? "flip inside 1σ — little room" : "distance to gamma flip"}
            tone={c?.flip_room_ok === false ? CE_COLOR : undefined}
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3 text-[11px]">
          <span className="text-muted-foreground">
            Pin wall ΔOI{" "}
            <span
              className="font-mono"
              style={{
                color:
                  c?.pin_doi_direction === "unwinding"
                    ? CE_COLOR
                    : c?.pin_doi_direction === "writing"
                      ? PE_COLOR
                      : undefined,
              }}
            >
              {compactOi(c?.pin_doi)}
            </span>
            {c?.pin_doi_direction ? ` · ${c.pin_doi_direction}` : ""}
          </span>
          <span className="text-muted-foreground">
            {pinLock?.samples
              ? `${pinLock.samples.ticks} ticks · ${pinLock.samples.minutes} min`
              : "—"}
          </span>
        </div>

        <p className="text-[11px] text-foreground/90">
          Breaks when {pinLock?.breaker?.label ?? "—"}.
        </p>

        {pinLock?.reasons?.length ? (
          <p className="text-[10px] text-muted-foreground">
            Not pinned: {pinLock.reasons.join(" · ")}.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

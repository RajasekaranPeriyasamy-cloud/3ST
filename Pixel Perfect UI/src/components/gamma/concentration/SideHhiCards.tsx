import type { GammaConcentrationBand } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { CE_COLOR, PE_COLOR, bandLabel } from "./shared";

function SideCard({
  title,
  value,
  band,
  color,
}: {
  title: string;
  value: number | null | undefined;
  band: GammaConcentrationBand | null | undefined;
  color: string;
}) {
  return (
    <Card>
      <CardContent className="space-y-1 pt-5">
        <p
          className="text-[10px] font-semibold uppercase tracking-[0.14em]"
          style={{ color }}
        >
          {title}
        </p>
        <p className="font-mono text-3xl font-light tabular-nums" style={{ color }}>
          {value != null ? value.toFixed(3) : "—"}
        </p>
        <p className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
          {value != null ? bandLabel(band) : "no side mass"}
        </p>
      </CardContent>
    </Card>
  );
}

export function SideHhiCards({
  callHhi,
  putHhi,
  callBand,
  putBand,
}: {
  callHhi: number | null;
  putHhi: number | null;
  callBand?: GammaConcentrationBand | null;
  putBand?: GammaConcentrationBand | null;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <SideCard title="Call γ HHI" value={callHhi} band={callBand} color={CE_COLOR} />
      <SideCard title="Put γ HHI" value={putHhi} band={putBand} color={PE_COLOR} />
    </div>
  );
}

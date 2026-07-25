import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Trash2 } from "lucide-react";

import { api, getApiBaseUrl } from "@/lib/api";
import { useSelection } from "@/context/SelectionContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

interface RiskLimits {
  max_qty?: number;
  max_open_positions?: number;
  max_daily_loss?: number;
  max_orders_per_minute?: number;
  open_positions?: number;
  mode?: string;
}

function SettingsPage() {
  const { clear } = useSelection();
  const [risk, setRisk] = useState<RiskLimits>({});
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [instrumentInfo, setInstrumentInfo] = useState<unknown>(null);

  async function loadRisk() {
    api.get<RiskLimits>("/risk/limits", { silent: true }).then((r) => setRisk(r ?? {})).catch(() => {});
  }

  useEffect(() => {
    void loadRisk();
  }, []);

  async function saveRisk() {
    setSaving(true);
    try {
      await api.post("/risk/limits", {
        max_qty: risk.max_qty,
        max_open_positions: risk.max_open_positions,
        max_daily_loss: risk.max_daily_loss,
        max_orders_per_minute: risk.max_orders_per_minute,
      });
      toast.success("Risk limits updated");
      await loadRisk();
    } catch {
      /* api toast */
    } finally {
      setSaving(false);
    }
  }

  async function refreshInstruments() {
    setRefreshing(true);
    try {
      const r = await api.get("/instruments?refresh=true");
      setInstrumentInfo(r);
      toast.success("Instruments refreshed");
    } catch {
      /* api toast */
    } finally {
      setRefreshing(false);
    }
  }

  async function clearSelection() {
    try {
      await clear();
      toast.success("Selection cleared");
    } catch {
      /* api toast */
    }
  }

  const atPositionCap =
    risk.open_positions != null &&
    risk.max_open_positions != null &&
    risk.open_positions >= risk.max_open_positions;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Manage risk limits, instrument cache, and current selection.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Environment</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">VITE_API_BASE_URL</span>
            <code className="rounded bg-muted px-2 py-1 font-mono text-xs">{getApiBaseUrl()}</code>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base">Risk Limits</CardTitle>
          {atPositionCap && (
            <Badge variant="outline" className="border-bear/50 text-bear">
              Position cap reached
            </Badge>
          )}
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2 rounded-md border border-border bg-muted/20 p-3 text-sm">
            Open positions now:{" "}
            <span className="font-mono font-semibold">{risk.open_positions ?? "—"}</span>
            {" / "}
            <span className="font-mono">{risk.max_open_positions ?? "—"}</span>
            {risk.mode && (
              <span className="ml-2 text-muted-foreground">({risk.mode} broker)</span>
            )}
            {atPositionCap && (
              <p className="mt-2 text-xs text-bear">
                Close old trades on Live Desk or raise Max open positions below, then restart is not
                required — click Save.
              </p>
            )}
          </div>
          <NumField
            label="Max open positions"
            value={risk.max_open_positions}
            onChange={(v) => setRisk({ ...risk, max_open_positions: v })}
          />
          <NumField
            label="Max quantity (per order)"
            value={risk.max_qty}
            onChange={(v) => setRisk({ ...risk, max_qty: v })}
          />
          <NumField
            label="Max daily loss"
            value={risk.max_daily_loss}
            onChange={(v) => setRisk({ ...risk, max_daily_loss: v })}
          />
          <NumField
            label="Max orders / minute"
            value={risk.max_orders_per_minute}
            onChange={(v) => setRisk({ ...risk, max_orders_per_minute: v })}
          />
          <div className="md:col-span-2">
            <Button onClick={saveRisk} disabled={saving}>
              {saving ? "Saving…" : "Save Risk Limits"}
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              Saved to disk — survives API restart and uvicorn reload.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Instrument Cache</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Button variant="outline" onClick={refreshInstruments} disabled={refreshing}>
            <RefreshCw className="mr-2 h-4 w-4" />
            {refreshing ? "Refreshing…" : "Refresh instruments"}
          </Button>
          {instrumentInfo != null && (
            <pre className="max-h-64 overflow-auto rounded-md bg-muted/40 p-3 font-mono text-xs">
              {JSON.stringify(instrumentInfo, null, 2)}
            </pre>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Danger Zone</CardTitle>
        </CardHeader>
        <CardContent>
          <Button variant="destructive" onClick={clearSelection}>
            <Trash2 className="mr-2 h-4 w-4" /> Clear saved selection
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function NumField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number | undefined;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      <Input
        type="number"
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
        className="font-mono"
      />
    </div>
  );
}

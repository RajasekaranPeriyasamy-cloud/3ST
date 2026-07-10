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

export const Route = createFileRoute("/settings")({
  component: SettingsPage,
});

interface RiskLimits {
  max_loss_day?: number;
  max_trades_day?: number;
  max_qty?: number;
}

function SettingsPage() {
  const { clear } = useSelection();
  const [risk, setRisk] = useState<RiskLimits>({});
  const [saving, setSaving] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [instrumentInfo, setInstrumentInfo] = useState<unknown>(null);

  useEffect(() => {
    api.get<RiskLimits>("/risk/limits", { silent: true }).then((r) => setRisk(r ?? {})).catch(() => {});
  }, []);

  async function saveRisk() {
    setSaving(true);
    try {
      await api.post("/risk/limits", risk);
      toast.success("Risk limits updated");
    } catch { /* */ } finally {
      setSaving(false);
    }
  }

  async function refreshInstruments() {
    setRefreshing(true);
    try {
      const r = await api.get("/instruments?refresh=true");
      setInstrumentInfo(r);
      toast.success("Instruments refreshed");
    } catch { /* */ } finally {
      setRefreshing(false);
    }
  }

  async function clearSelection() {
    try {
      await clear();
      toast.success("Selection cleared");
    } catch { /* */ }
  }

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
        <CardHeader>
          <CardTitle className="text-base">Risk Limits</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-3">
          <NumField
            label="Max loss / day"
            value={risk.max_loss_day}
            onChange={(v) => setRisk({ ...risk, max_loss_day: v })}
          />
          <NumField
            label="Max trades / day"
            value={risk.max_trades_day}
            onChange={(v) => setRisk({ ...risk, max_trades_day: v })}
          />
          <NumField
            label="Max quantity"
            value={risk.max_qty}
            onChange={(v) => setRisk({ ...risk, max_qty: v })}
          />
          <div className="md:col-span-3">
            <Button onClick={saveRisk} disabled={saving}>
              {saving ? "Saving…" : "Save Risk Limits"}
            </Button>
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

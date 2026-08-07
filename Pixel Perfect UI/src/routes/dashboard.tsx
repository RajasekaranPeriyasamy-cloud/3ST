import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Activity, BarChart3, RefreshCw, Settings as SettingsIcon, Target, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { useWatchlistByStatus } from "@/context/WatchlistContext";
import type { WatchlistItem } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export const Route = createFileRoute("/dashboard")({
  component: DashboardPage,
});

interface Me {
  user_name?: string;
  user_id?: string;
  login_time?: string;
}
interface Margins {
  equity?: { net?: number; available?: { cash?: number } };
}
interface ArmStatus {
  armed?: boolean;
  mode?: string;
}

function DashboardPage() {
  const { items: waiting, loading, refresh, remove, scan } = useWatchlistByStatus("waiting");
  const [me, setMe] = useState<Me | null>(null);
  const [margins, setMargins] = useState<Margins | null>(null);
  const [arm, setArm] = useState<ArmStatus | null>(null);
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    api.get<Me>("/auth/me", { silent: true }).then(setMe).catch(() => {});
    api.get<Margins>("/margins", { silent: true }).then(setMargins).catch(() => {});
    api.get<ArmStatus>("/live/arm", { silent: true }).then(setArm).catch(() => {});
  }, []);

  useEffect(() => {
    const poll = setInterval(() => {
      void refresh();
      api.get<ArmStatus>("/live/arm", { silent: true }).then(setArm).catch(() => {});
    }, 8000);
    return () => clearInterval(poll);
  }, [refresh]);

  useEffect(() => {
    if (!arm?.armed) return;
    const scanPoll = setInterval(async () => {
      try {
        const r = await scan(true);
        if (r.triggered.length) {
          toast.success(`${r.triggered.length} signal(s) — check Live Desk`, {
            action: {
              label: "Open",
              onClick: () => {
                window.location.href = "/live";
              },
            },
          });
        }
      } catch {
        /* silent background scan */
      }
    }, 30000);
    return () => clearInterval(scanPoll);
  }, [arm?.armed, scan]);

  async function runScan() {
    setScanning(true);
    try {
      const r = await scan(false);
      if (r.triggered.length) {
        toast.success(`${r.triggered.length} signal(s) — open Live Desk`, {
          action: {
            label: "Live Desk",
            onClick: () => {
              window.location.href = "/live";
            },
          },
        });
      } else {
        toast.message("No new 3ST entry signals on waiting items");
      }
    } catch {
      /* api toast */
    } finally {
      setScanning(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            {me?.user_name
              ? `Welcome, ${me.user_name} — instruments queued here wait for a 3ST signal.`
              : "Queue instruments waiting for 3ST signals. Triggered trades move to Live Desk."}
          </p>
        </div>
        <Badge className={arm?.armed ? "bg-bull" : "bg-bear"}>
          {arm?.armed ? "ARMED" : "DISARMED"}
        </Badge>
      </header>

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Kite user</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="font-mono text-lg">{me?.user_id ?? "—"}</div>
            <div className="text-xs text-muted-foreground">
              {me?.login_time ? new Date(me.login_time).toLocaleString() : "Not signed in"}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Equity net</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="font-mono text-lg">
              {margins?.equity?.net?.toLocaleString() ?? "—"}
            </div>
            <div className="text-xs text-muted-foreground">Total equity value</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">Waiting for signal</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="font-mono text-lg">{waiting.length}</div>
            <div className="text-xs text-muted-foreground">Queued on this desk</div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base">Signal Queue</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">
              Add instruments from Stock Selection. When 3ST fires, they appear on Live Desk.
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => refresh()} disabled={loading}>
              <RefreshCw className="mr-2 h-4 w-4" /> Reload
            </Button>
            <Button size="sm" onClick={runScan} disabled={scanning}>
              {scanning ? "Scanning…" : "Scan for signals"}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {loading && !waiting.length ? (
            <p className="text-sm text-muted-foreground">Loading queue…</p>
          ) : waiting.length === 0 ? (
            <div className="rounded-md border border-dashed border-border p-8 text-center">
              <p className="text-sm text-muted-foreground">No instruments waiting for a signal.</p>
              <Button asChild className="mt-4" variant="outline">
                <Link to="/">
                  <Target className="mr-2 h-4 w-4" /> Add from Stock Selection
                </Link>
              </Button>
            </div>
          ) : (
            <WatchlistTable items={waiting} onRemove={remove} />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-3 md:grid-cols-4">
        <QuickLink to="/" icon={<Target className="h-4 w-4" />} label="Stock Selection" />
        <QuickLink to="/backtest" icon={<BarChart3 className="h-4 w-4" />} label="Backtest" />
        <QuickLink to="/live" icon={<Activity className="h-4 w-4" />} label="Live Desk" />
        <QuickLink to="/settings" icon={<SettingsIcon className="h-4 w-4" />} label="Settings" />
      </div>
    </div>
  );
}

function WatchlistTable({
  items,
  onRemove,
}: {
  items: WatchlistItem[];
  onRemove: (id: string) => Promise<void>;
}) {
  return (
    <div className="overflow-auto rounded-md border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Instrument</TableHead>
            <TableHead>Timeframe</TableHead>
            <TableHead>Entry</TableHead>
            <TableHead>Product</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.id}>
              <TableCell>
                <div className="font-mono text-sm">{item.tradingsymbol ?? "—"}</div>
                <div className="text-xs text-muted-foreground">{item.exchange}</div>
              </TableCell>
              <TableCell className="font-mono">{item.timeframe}</TableCell>
              <TableCell>
                <Badge variant="outline" className="text-xs uppercase">
                  {(item.entry_mode ?? "manual") === "manual" ? "Manual" : "3ST signal"}
                </Badge>
              </TableCell>
              <TableCell>
                <div className="font-mono text-xs">{item.product}</div>
                {item.spread && (
                  <div className="text-xs text-muted-foreground">
                    {item.spread.underlying} · {item.spread.expiry}
                  </div>
                )}
              </TableCell>
              <TableCell>
                <Badge variant="outline" className="border-amber-500/50 text-amber-600 dark:text-amber-400">
                  waiting
                </Badge>
              </TableCell>
              <TableCell className="text-right">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    try {
                      await onRemove(item.id);
                      toast.success("Removed from queue");
                    } catch {
                      /* handled */
                    }
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function QuickLink({
  to,
  icon,
  label,
}: {
  to: "/" | "/backtest" | "/live" | "/settings";
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <Button asChild variant="outline" className="h-auto justify-start p-4">
      <Link to={to} className="flex items-center gap-3">
        {icon}
        <span>{label}</span>
      </Link>
    </Button>
  );
}

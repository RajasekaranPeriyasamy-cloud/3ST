import { ChevronDown, ChevronUp, Shield, ShieldOff, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useExecutionQueue } from "@/hooks/useExecutionQueue";
import type { ExecutionQueueItem } from "@/lib/types";

const ACTION_LABELS: Record<string, string> = {
  adopt: "Adopt",
  unlink: "Stop monitoring",
  close: "Close",
  ship: "Ship",
  execute: "Execute",
  dismiss: "Dismiss",
};

function fmtPnl(pnl: number | null | undefined) {
  if (pnl == null || Number.isNaN(pnl)) return "—";
  const sign = pnl >= 0 ? "+" : "";
  return `${sign}${pnl.toFixed(0)}`;
}

function QueueRow({
  item,
  onAction,
  busy,
}: {
  item: ExecutionQueueItem;
  onAction: (legId: string, action: string) => Promise<void>;
  busy: string | null;
}) {
  const side = item.side?.toUpperCase() ?? "—";
  const qty = item.qty ?? 0;
  const exitLine =
    item.exit_triggers?.next_exit?.label ??
    item.exit_triggers?.zone_exit_label ??
    item.exit_triggers?.st_exit_label ??
    null;
  const exitPx =
    item.exit_triggers?.next_exit?.price ??
    item.exit_triggers?.zone_exit_level ??
    item.exit_triggers?.st_exit_price ??
    null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/50 px-3 py-2 text-xs last:border-b-0">
      <div className="min-w-[140px] flex-1">
        <p className="font-medium text-foreground">{item.instrument}</p>
        <p className="text-muted-foreground">
          {item.owner_label ?? item.source} · {item.status}
          {item.signal_note ? ` · ${item.signal_note}` : ""}
        </p>
      </div>
      <div className="w-16 text-right tabular-nums">{side}</div>
      <div className="w-14 text-right tabular-nums">{qty !== 0 ? qty : "—"}</div>
      <div className="w-20 text-right tabular-nums">
        {item.ltp != null ? item.ltp.toFixed(2) : item.entry_price != null ? `@ ${item.entry_price.toFixed(2)}` : "—"}
      </div>
      <div className="w-16 text-right tabular-nums">{fmtPnl(item.pnl)}</div>
      <div className="min-w-[100px] text-right text-muted-foreground">
        {exitLine && exitPx != null ? `${exitLine} @ ${Number(exitPx).toFixed(2)}` : "—"}
      </div>
      <div className="flex flex-wrap gap-1">
        {item.actions.map((action) => (
          <Button
            key={action}
            size="sm"
            variant={action === "close" ? "destructive" : action === "ship" || action === "execute" ? "default" : "outline"}
            className="h-7 px-2 text-[11px]"
            disabled={busy === `${item.leg_id}:${action}`}
            onClick={async () => {
              try {
                await onAction(item.leg_id, action);
                toast.success(`${ACTION_LABELS[action] ?? action} sent`);
              } catch (e) {
                toast.error(e instanceof Error ? e.message : "Action failed");
              }
            }}
          >
            {ACTION_LABELS[action] ?? action}
          </Button>
        ))}
      </div>
    </div>
  );
}

function QueueList({
  items,
  empty,
  onAction,
  busy,
}: {
  items: ExecutionQueueItem[];
  empty: string;
  onAction: (legId: string, action: string) => Promise<void>;
  busy: string | null;
}) {
  if (items.length === 0) {
    return <p className="px-3 py-4 text-xs text-muted-foreground">{empty}</p>;
  }
  return (
    <div>
      {items.map((item) => (
        <QueueRow key={item.leg_id} item={item} onAction={onAction} busy={busy} />
      ))}
    </div>
  );
}

export function ExecutionTaskbar() {
  const { queue, runAction, refresh } = useExecutionQueue();
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const summary = queue?.summary;
  const orphanCount = summary?.orphan_count ?? 0;
  const activeCount = summary?.active_count ?? 0;
  const pendingCount = summary?.pending_count ?? 0;
  const errorCount = summary?.error_count ?? 0;
  const mode = summary?.mode ?? "paper";
  const armed = summary?.armed ?? false;

  async function handleAction(legId: string, action: string) {
    const key = `${legId}:${action}`;
    setBusy(key);
    try {
      await runAction(legId, action);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div
      className="sticky bottom-0 z-30 border-t border-border bg-background/95 backdrop-blur"
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/40"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Execution</span>
        <Badge variant={pendingCount > 0 ? "default" : "secondary"} className="text-[10px]">
          Pending {pendingCount}
        </Badge>
        <Badge variant="secondary" className="text-[10px]">
          Active {activeCount}
        </Badge>
        <Badge variant={orphanCount > 0 ? "destructive" : "secondary"} className="text-[10px]">
          Orphans {orphanCount}
        </Badge>
        {errorCount > 0 ? (
          <Badge variant="destructive" className="text-[10px]">
            Errors {errorCount}
          </Badge>
        ) : null}
        <span className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
          {mode.toUpperCase()}
          {armed ? (
            <span className="inline-flex items-center gap-1 text-emerald-400">
              <Shield className="h-3 w-3" /> ARMED
            </span>
          ) : (
            <span className="inline-flex items-center gap-1">
              <ShieldOff className="h-3 w-3" /> DISARMED
            </span>
          )}
          {summary?.kite_authenticated ? " · Kite ✓" : " · Kite ✗"}
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
        </span>
      </button>

      {expanded ? (
        <div className="max-h-56 overflow-auto border-t border-border/60">
          {orphanCount > 0 ? (
            <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs text-amber-200">
              <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
              Unlinked Kite legs need Adopt or Close — they will not auto-exit.
            </div>
          ) : null}
          <Tabs defaultValue={orphanCount > 0 ? "orphans" : activeCount > 0 ? "active" : "pending"} className="w-full">
            <TabsList className="h-8 w-full justify-start rounded-none border-b bg-muted/30 px-2">
              <TabsTrigger value="pending" className="text-xs">
                Pending ({pendingCount})
              </TabsTrigger>
              <TabsTrigger value="active" className="text-xs">
                Active ({activeCount})
              </TabsTrigger>
              <TabsTrigger value="orphans" className="text-xs">
                Orphans ({orphanCount})
              </TabsTrigger>
              <TabsTrigger value="errors" className="text-xs">
                Errors ({errorCount})
              </TabsTrigger>
            </TabsList>
            <TabsContent value="pending" className="mt-0">
              <QueueList
                items={queue?.pending ?? []}
                empty="No pending signals"
                onAction={handleAction}
                busy={busy}
              />
            </TabsContent>
            <TabsContent value="active" className="mt-0">
              <QueueList
                items={queue?.active ?? []}
                empty="No managed legs"
                onAction={handleAction}
                busy={busy}
              />
            </TabsContent>
            <TabsContent value="orphans" className="mt-0">
              <QueueList
                items={queue?.orphans ?? []}
                empty="No orphan positions"
                onAction={handleAction}
                busy={busy}
              />
            </TabsContent>
            <TabsContent value="errors" className="mt-0">
              {(queue?.errors?.length ?? 0) === 0 ? (
                <p className="px-3 py-4 text-xs text-muted-foreground">No errors</p>
              ) : (
                <div className="divide-y divide-border/50">
                  {queue!.errors!.map((err) => (
                    <div key={err.leg_id + err.message} className="px-3 py-2 text-xs text-destructive">
                      {err.message}
                    </div>
                  ))}
                </div>
              )}
            </TabsContent>
          </Tabs>
          <div className="flex justify-end border-t border-border/50 px-2 py-1">
            <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => refresh()}>
              Refresh
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

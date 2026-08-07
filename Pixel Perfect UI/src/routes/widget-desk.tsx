import { createFileRoute } from "@tanstack/react-router";
import { Maximize2, Minimize2, Pause, Play, RefreshCw } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import {
  AnalyticsDeskProvider,
  useAnalyticsDesk,
  type WidgetId,
} from "@/context/AnalyticsDeskContext";
import { getAvailableWidgets, WIDGET_CATALOG, type WidgetDef } from "@/components/widgets/registry";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useSidebar } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import type { OiUnderlying } from "@/lib/types";

export type WidgetDeskSearch = {
  focus?: WidgetId;
  full?: boolean;
  underlying?: OiUnderlying;
  expiry?: string;
};

const FOCUSABLE = new Set(
  WIDGET_CATALOG.filter((w) => w.available).map((w) => w.id),
);

export const Route = createFileRoute("/widget-desk")({
  validateSearch: (search: Record<string, unknown>): WidgetDeskSearch => {
    const focusRaw = typeof search.focus === "string" ? search.focus : undefined;
    const focus =
      focusRaw && FOCUSABLE.has(focusRaw as WidgetId) ? (focusRaw as WidgetId) : undefined;
    const full = search.full === true || search.full === "1" || search.full === "true";
    const underlying =
      search.underlying === "NIFTY" ||
      search.underlying === "BANKNIFTY" ||
      search.underlying === "SENSEX"
        ? search.underlying
        : undefined;
    const expiry = typeof search.expiry === "string" ? search.expiry : undefined;
    return { focus, full: full || undefined, underlying, expiry };
  },
  component: WidgetDeskRoute,
});

function WidgetDeskRoute() {
  return (
    <AnalyticsDeskProvider>
      <WidgetDeskPage />
    </AnalyticsDeskProvider>
  );
}

function PanelWrap({ children }: { children: ReactNode }) {
  return <div className="h-full min-h-0 overflow-hidden p-1.5">{children}</div>;
}

function RenderWidget({ def, expanded }: { def: WidgetDef; expanded?: boolean }) {
  const Comp = def.Component;
  return <Comp expanded={expanded} />;
}

function DeskGrid({ widgets }: { widgets: WidgetDef[] }) {
  if (!widgets.length) {
    return (
      <div className="flex h-full min-h-[320px] items-center justify-center rounded-lg border border-dashed border-primary/30 bg-card/40 px-6 text-center">
        <div>
          <p className="text-sm font-medium text-foreground">No widgets enabled</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Use the Widgets checklist above to add boards to this desk.
          </p>
        </div>
      </div>
    );
  }

  if (widgets.length === 1) {
    return (
      <div className="h-full min-h-[420px]">
        <PanelWrap>
          <RenderWidget def={widgets[0]} expanded />
        </PanelWrap>
      </div>
    );
  }

  if (widgets.length === 2) {
    return (
      <ResizablePanelGroup orientation="horizontal" className="min-h-[480px]">
        <ResizablePanel defaultSize="50%" minSize="25%">
          <PanelWrap>
            <RenderWidget def={widgets[0]} />
          </PanelWrap>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize="50%" minSize="25%">
          <PanelWrap>
            <RenderWidget def={widgets[1]} />
          </PanelWrap>
        </ResizablePanel>
      </ResizablePanelGroup>
    );
  }

  const top = widgets.slice(0, 2);
  const bottom = widgets.slice(2);

  return (
    <ResizablePanelGroup orientation="vertical" className="min-h-[640px]">
      <ResizablePanel defaultSize="50%" minSize="25%">
        <ResizablePanelGroup orientation="horizontal">
          <ResizablePanel defaultSize="50%" minSize="25%">
            <PanelWrap>
              <RenderWidget def={top[0]} />
            </PanelWrap>
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize="50%" minSize="25%">
            <PanelWrap>
              <RenderWidget def={top[1]} />
            </PanelWrap>
          </ResizablePanel>
        </ResizablePanelGroup>
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize="50%" minSize="25%">
        {bottom.length === 1 ? (
          <PanelWrap>
            <RenderWidget def={bottom[0]} />
          </PanelWrap>
        ) : (
          <ResizablePanelGroup orientation="horizontal">
            <ResizablePanel defaultSize="50%" minSize="25%">
              <PanelWrap>
                <RenderWidget def={bottom[0]} />
              </PanelWrap>
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize="50%" minSize="25%">
              <PanelWrap>
                <RenderWidget def={bottom[1]} />
              </PanelWrap>
            </ResizablePanel>
          </ResizablePanelGroup>
        )}
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}

function WidgetDeskPage() {
  const search = Route.useSearch();
  const {
    underlying,
    setUnderlying,
    underlyings,
    expiry,
    setExpiry,
    expiries,
    expiriesLoading,
    refreshSec,
    setRefreshSec,
    autoRefresh,
    setAutoRefresh,
    enabled,
    toggleWidget,
    setEnabled,
    bumpRefresh,
  } = useAnalyticsDesk();
  const { open: sidebarOpen, setOpen: setSidebarOpen, isMobile, toggleSidebar } =
    useSidebar();
  const fullView = isMobile ? false : !sidebarOpen;
  const deepLinkApplied = useRef(false);

  // Apply deep-link once per landing (focus / full / underlying / expiry)
  useEffect(() => {
    if (deepLinkApplied.current) return;
    const hasDeepLink = Boolean(
      search.focus || search.full || search.underlying || search.expiry,
    );
    if (!hasDeepLink) return;
    deepLinkApplied.current = true;
    if (search.focus) setEnabled([search.focus]);
    if (search.underlying) setUnderlying(search.underlying);
    if (search.expiry) setExpiry(search.expiry);
    if (search.full && !isMobile) setSidebarOpen(false);
  }, [
    search.focus,
    search.full,
    search.underlying,
    search.expiry,
    setEnabled,
    setUnderlying,
    setExpiry,
    setSidebarOpen,
    isMobile,
  ]);

  const activeWidgets = getAvailableWidgets().filter((w) => enabled.includes(w.id));
  const focusedTitle = search.focus
    ? WIDGET_CATALOG.find((w) => w.id === search.focus)?.title
    : null;

  return (
    <div
      className={cn(
        "flex min-h-0 flex-1 flex-col gap-3",
        fullView ? "h-[calc(100vh-3rem)] p-1.5 md:p-2" : "h-[calc(100vh-3.5rem)] p-3 md:p-4",
      )}
    >
      <header className="shrink-0 space-y-3 rounded-lg border border-primary/15 bg-card/80 px-3 py-3 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-foreground">
              Widget Desk
              {focusedTitle ? (
                <span className="ml-2 text-sm font-medium text-primary">
                  · {focusedTitle} full view
                </span>
              ) : null}
            </h1>
            <p className="text-xs text-muted-foreground">
              Toggle analytics boards onto one shared screen · shared underlying &amp; expiry
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant={fullView ? "default" : "outline"}
              size="sm"
              className="h-8"
              title={
                fullView
                  ? "Restore sidebar"
                  : "Hide sidebar for a wider desk (Ctrl/Cmd+B)"
              }
              onClick={() => {
                if (isMobile) toggleSidebar();
                else setSidebarOpen(!sidebarOpen);
              }}
            >
              {fullView ? (
                <>
                  <Minimize2 className="mr-1.5 h-3.5 w-3.5" /> Show sidebar
                </>
              ) : (
                <>
                  <Maximize2 className="mr-1.5 h-3.5 w-3.5" /> Full view
                </>
              )}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8"
              onClick={() => setAutoRefresh(!autoRefresh)}
            >
              {autoRefresh ? (
                <>
                  <Pause className="mr-1.5 h-3.5 w-3.5" /> Auto
                </>
              ) : (
                <>
                  <Play className="mr-1.5 h-3.5 w-3.5" /> Paused
                </>
              )}
            </Button>
            <Select
              value={String(refreshSec)}
              onValueChange={(v) => setRefreshSec(Number(v))}
            >
              <SelectTrigger className="h-8 w-[5.5rem] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[30, 60, 90, 120].map((s) => (
                  <SelectItem key={s} value={String(s)}>
                    {s}s
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button type="button" size="sm" className="h-8" onClick={bumpRefresh}>
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              Refresh
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1.5">
            <Label className="text-[11px] text-muted-foreground">Underlying</Label>
            <div className="flex flex-wrap gap-1.5">
              {underlyings.map((u) => (
                <Button
                  key={u}
                  type="button"
                  size="sm"
                  variant={underlying === u ? "default" : "outline"}
                  className={cn("h-8 px-3 text-xs", underlying === u && "shadow-sm")}
                  onClick={() => setUnderlying(u)}
                >
                  {u}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="text-[11px] text-muted-foreground">Expiry</Label>
            <Select
              value={expiry || undefined}
              onValueChange={setExpiry}
              disabled={expiriesLoading || !expiries.length}
            >
              <SelectTrigger className="h-8 w-[10rem] text-xs">
                <SelectValue placeholder={expiriesLoading ? "Loading…" : "Select expiry"} />
              </SelectTrigger>
              <SelectContent>
                {expiries.map((e) => (
                  <SelectItem key={e} value={e}>
                    {e}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="space-y-1.5 border-t border-border/60 pt-3">
          <Label className="text-[11px] text-muted-foreground">Widgets</Label>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {WIDGET_CATALOG.map((w) => {
              const checked = enabled.includes(w.id);
              return (
                <label
                  key={w.id}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 text-xs",
                    !w.available && "cursor-not-allowed opacity-45",
                  )}
                >
                  <Checkbox
                    checked={checked && w.available}
                    disabled={!w.available}
                    onCheckedChange={() => {
                      if (w.available) toggleWidget(w.id as WidgetId);
                    }}
                  />
                  <span>
                    {w.title}
                    {!w.available ? (
                      <span className="ml-1 text-[10px] text-muted-foreground">(soon)</span>
                    ) : null}
                  </span>
                </label>
              );
            })}
          </div>
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-hidden">
        <DeskGrid widgets={activeWidgets} />
      </main>
    </div>
  );
}

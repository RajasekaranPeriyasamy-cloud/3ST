import { useCallback, useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { CircleDot, Pause, Play, RefreshCw } from "lucide-react";

import { NewsRow } from "@/components/news/NewsRow";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { NewsFeedSnapshot, NewsSourcesSnapshot } from "@/lib/types";

export const Route = createFileRoute("/news")({ component: NewsPage });

const TABS = [
  { key: "all", label: "All News" },
  { key: "mine", label: "News for Me" },
  { key: "actions", label: "Corporate Actions" },
] as const;

const SENTIMENTS = ["", "positive", "negative", "neutral"] as const;

const POLL_SEC = 30;

function NewsPage() {
  const [tab, setTab] = useState<string>("all");
  const [sentiment, setSentiment] = useState<string>("");
  const [snap, setSnap] = useState<NewsFeedSnapshot | null>(null);
  const [sources, setSources] = useState<NewsSourcesSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ tab, limit: "80" });
      if (sentiment) params.set("sentiment", sentiment);
      const data = await api.get<NewsFeedSnapshot>(
        `/newsfeed/items?${params.toString()}`,
        { silent: true },
      );
      setSnap(data);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [tab, sentiment]);

  const refreshSources = useCallback(async () => {
    try {
      setSources(await api.get<NewsSourcesSnapshot>("/newsfeed/sources", { silent: true }));
    } catch {
      /* health is advisory — a failure here must not blank the feed */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void refreshSources();
  }, [refreshSources]);

  useEffect(() => {
    if (!auto) return;
    const id = window.setInterval(() => {
      // Hidden tabs stop polling — the same gate useWidgetPoll applies.
      if (document.visibilityState !== "visible") return;
      void refresh();
      void refreshSources();
    }, POLL_SEC * 1000);
    return () => window.clearInterval(id);
  }, [auto, refresh, refreshSources]);

  const kickPoll = useCallback(async () => {
    try {
      await api.post("/newsfeed/refresh", {});
      // The runner fetches eleven publishers asynchronously; give it a moment
      // before reading the store back.
      window.setTimeout(() => void refresh(), 2500);
      window.setTimeout(() => void refreshSources(), 2500);
    } catch {
      /* surfaced by the api layer's toast */
    }
  }, [refresh, refreshSources]);

  const healthy = sources?.sources.filter((s) => s.ok).length ?? 0;
  const totalSources = sources?.sources.length ?? 0;
  const allOk = totalSources > 0 && healthy === totalSources;

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Market News</h1>
          <p className="text-sm text-muted-foreground">
            Live headlines from {totalSources || "…"} sources, scored for sentiment and
            matched to NSE symbols.
          </p>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {sources && (
            <TooltipProvider delayDuration={100}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant="outline"
                    className={cn(
                      "gap-1",
                      allOk ? "border-bull/50 text-bull" : "border-amber-500/50 text-amber-500",
                    )}
                  >
                    <CircleDot className="h-3 w-3" />
                    {healthy}/{totalSources} sources
                  </Badge>
                </TooltipTrigger>
                <TooltipContent className="max-w-sm text-xs">
                  <div className="space-y-0.5">
                    {sources.sources.map((s) => (
                      <p key={s.key} className={s.ok ? "" : "text-bear"}>
                        {s.ok ? "✓" : "✕"} {s.key} · {s.count}
                        {s.error ? ` — ${s.error.slice(0, 60)}` : ""}
                      </p>
                    ))}
                    <p className="pt-1 opacity-70">
                      engine: {sources.engine.provider}
                      {sources.engine.model ? ` (${sources.engine.model})` : ""}
                    </p>
                    <p className="opacity-70">
                      {sources.items} items stored · runner{" "}
                      {sources.runner_alive ? "alive" : "stopped"}
                    </p>
                  </div>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}

          <Button
            variant="outline"
            size="sm"
            onClick={() => setAuto((v) => !v)}
            title={auto ? "Pause auto-refresh" : "Resume auto-refresh"}
          >
            {auto ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void kickPoll()}>
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-3">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              {TABS.map((t) => (
                <TabsTrigger key={t.key} value={t.key}>
                  {t.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          <div className="flex items-center gap-1">
            {SENTIMENTS.map((s) => (
              <Button
                key={s || "any"}
                variant={sentiment === s ? "secondary" : "ghost"}
                size="sm"
                className="h-7 px-2 text-xs capitalize"
                onClick={() => setSentiment(s)}
              >
                {s || "all"}
              </Button>
            ))}
          </div>

          <span className="ml-auto text-xs text-muted-foreground tabular-nums">
            {snap ? `${snap.returned} of ${snap.total}` : "…"}
            {snap?.last_poll.at ? ` · polled ${snap.last_poll.at.slice(11, 19)}Z` : ""}
          </span>
        </div>
      </Card>

      {error && (
        <Card className="border-bear/50 p-3 text-sm text-bear">
          Could not load the feed: {error}
        </Card>
      )}

      {tab === "mine" && snap?.returned === 0 && (
        <Card className="p-4 text-sm text-muted-foreground">
          Nothing here yet — this tab shows headlines matching symbols on your{" "}
          <span className="font-medium">watchlist</span>. Add a symbol on the Live Desk
          and it will start filling.
        </Card>
      )}

      <ScrollArea className="h-[calc(100vh-19rem)]">
        <div className="flex flex-col gap-2 pr-3">
          {snap?.items.map((item) => (
            <NewsRow key={item.id} item={item} />
          ))}
          {!snap && !error && (
            <p className="p-4 text-sm text-muted-foreground">Loading headlines…</p>
          )}
          {snap && snap.returned === 0 && tab !== "mine" && (
            <p className="p-4 text-sm text-muted-foreground">
              No items match this filter yet.
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

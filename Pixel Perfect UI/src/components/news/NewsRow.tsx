import { useState } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { NewsItem, NewsSentimentLabel, NewsSymbol } from "@/lib/types";

/** Sentiment tones use the project's --bull/--bear tokens rather than raw
 *  Tailwind colours, so they flip with the theme without a `dark:` variant. */
const SENTIMENT_TONE: Record<NewsSentimentLabel, string> = {
  positive: "border-bull/50 bg-bull/10 text-bull",
  negative: "border-bear/50 bg-bear/10 text-bear",
  neutral: "border-muted-foreground/30 bg-muted/40 text-muted-foreground",
};

const SENTIMENT_LABEL: Record<NewsSentimentLabel, string> = {
  positive: "POSITIVE",
  negative: "NEGATIVE",
  neutral: "NEUTRAL",
};

/** Relative time, short form — "17m ago", "3h ago", "2d ago".
 *  Hand-rolled rather than pulling date-fns in for one call: the feed only ever
 *  shows coarse buckets, and this avoids a locale-formatted string that would
 *  change width on every tick. */
function timeAgo(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function TickerChip({ symbol }: { symbol: NewsSymbol }) {
  const hasPrice = symbol.last_price != null;
  const up = (symbol.change ?? 0) >= 0;

  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-muted/30 px-2 py-0.5 text-xs">
      <span className="font-medium tabular-nums">{symbol.tradingsymbol}</span>
      {hasPrice && (
        <>
          <span className="tabular-nums text-muted-foreground">
            {symbol.last_price?.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
          </span>
          {symbol.change_pct != null && (
            <span className={cn("tabular-nums", up ? "text-bull" : "text-bear")}>
              {up ? "+" : ""}
              {symbol.change?.toFixed(2)} ({symbol.change_pct.toFixed(2)}%)
            </span>
          )}
        </>
      )}
    </span>
  );
}

export function NewsRow({ item }: { item: NewsItem }) {
  const [expanded, setExpanded] = useState(false);
  const label = item.sentiment?.label ?? "neutral";
  const category = item.sentiment?.category ?? "Neutral/Markets";

  return (
    <article
      className={cn(
        "rounded-lg border border-border/60 bg-card p-3 transition-colors",
        label === "positive" && "border-l-2 border-l-bull/60",
        label === "negative" && "border-l-2 border-l-bear/60",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant="outline"
          className={cn("px-2 py-0 text-[10px] font-semibold tracking-wide", SENTIMENT_TONE[label])}
        >
          {SENTIMENT_LABEL[label]}
        </Badge>
        <span className="text-xs text-muted-foreground">{category}</span>
        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
          {timeAgo(item.published_at)}
        </span>
      </div>

      <h3 className="mt-1.5 text-sm font-medium leading-snug">
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:underline"
          >
            {item.title}
            <ExternalLink className="ml-1 inline h-3 w-3 opacity-50" />
          </a>
        ) : (
          item.title
        )}
      </h3>

      {item.summary && (
        <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{item.summary}</p>
      )}

      {item.symbols.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {item.symbols.map((s) => (
            <TickerChip key={`${s.exchange}:${s.tradingsymbol}`} symbol={s} />
          ))}
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{item.publisher}</span>
        {item.also_reported_by?.length ? (
          <span className="opacity-70">also on {item.also_reported_by.join(", ")}</span>
        ) : null}

        {item.related_count > 0 && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-auto inline-flex items-center gap-1 rounded border border-amber-500/40 px-1.5 py-0.5 text-amber-600 hover:bg-amber-500/10 dark:text-amber-400"
          >
            <span className="tabular-nums">{item.related_count}</span>
            more on this stock
            <ChevronDown className={cn("h-3 w-3 transition-transform", expanded && "rotate-180")} />
          </button>
        )}
      </div>

      {expanded && item.related_count > 0 && (
        <ul className="mt-2 space-y-1 border-t border-border/50 pt-2">
          {item.related.map((r) => (
            <li key={r.id} className="flex items-start gap-2 text-xs">
              <span
                className={cn(
                  "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
                  r.sentiment?.label === "positive" && "bg-bull",
                  r.sentiment?.label === "negative" && "bg-bear",
                  (!r.sentiment || r.sentiment.label === "neutral") && "bg-muted-foreground/40",
                )}
              />
              <a
                href={r.url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 hover:underline"
              >
                {r.title}
              </a>
              <span className="shrink-0 text-muted-foreground tabular-nums">
                {timeAgo(r.published_at)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

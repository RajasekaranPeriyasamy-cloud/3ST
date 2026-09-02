import { useCallback, useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";

import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface InstrumentHit {
  exchange: string;
  tradingsymbol: string;
  name: string;
  instrument_token: number;
}

export interface EquityQuery {
  /** NSE tradingsymbol — matches resolved chips AND text mentions. */
  symbol: string;
  /** Free text, used when the typed value is not a known equity. */
  q: string;
}

const DEBOUNCE_MS = 250;

export function EquitySearch({
  value,
  onChange,
}: {
  value: EquityQuery;
  onChange: (next: EquityQuery) => void;
}) {
  const [text, setText] = useState(value.symbol || value.q || "");
  const [hits, setHits] = useState<InstrumentHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);

  // Keep the input in step when the parent clears the search (e.g. tab change).
  useEffect(() => {
    if (!value.symbol && !value.q) setText("");
  }, [value.symbol, value.q]);

  // Debounced autocomplete against the instrument master. `segment=equity`
  // keeps options and futures out — this desk searches stocks.
  useEffect(() => {
    const term = text.trim();
    if (term.length < 2) {
      setHits([]);
      return;
    }
    const id = window.setTimeout(async () => {
      try {
        const res = await api.get<{ items: InstrumentHit[] }>(
          `/instruments/search?q=${encodeURIComponent(term)}&segment=equity&limit=8`,
          { silent: true },
        );
        setHits((res.items ?? []).filter((i) => i.exchange === "NSE"));
        setActive(0);
      } catch {
        // No instrument cache or no Kite session — free-text search still works.
        setHits([]);
      }
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [text]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const pick = useCallback(
    (hit: InstrumentHit) => {
      setText(hit.tradingsymbol);
      setOpen(false);
      onChange({ symbol: hit.tradingsymbol, q: "" });
    },
    [onChange],
  );

  const clear = useCallback(() => {
    setText("");
    setHits([]);
    setOpen(false);
    onChange({ symbol: "", q: "" });
  }, [onChange]);

  const submitFreeText = useCallback(() => {
    const term = text.trim();
    setOpen(false);
    // An exact symbol match wins even without picking from the list, so typing
    // "RELIANCE" and pressing Enter searches the stock, not the word.
    const exact = hits.find((h) => h.tradingsymbol.toUpperCase() === term.toUpperCase());
    if (exact) onChange({ symbol: exact.tradingsymbol, q: "" });
    else onChange({ symbol: "", q: term });
  }, [text, hits, onChange]);

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, hits.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && hits[active]) pick(hits[active]);
      else submitFreeText();
    } else if (e.key === "Escape") {
      if (open) setOpen(false);
      else clear();
    }
  }

  const activeLabel = value.symbol || (value.q ? `“${value.q}”` : "");

  return (
    <div ref={boxRef} className="relative w-full sm:w-80">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          placeholder="Search a stock — e.g. RELIANCE, Hero MotoCorp"
          className="h-9 pl-8 pr-8"
          aria-label="Search news by equity"
        />
        {(text || activeLabel) && (
          <button
            type="button"
            onClick={clear}
            aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {open && hits.length > 0 && (
        <ul className="absolute z-50 mt-1 max-h-72 w-full overflow-auto rounded-md border border-border bg-popover p-1 shadow-md">
          {hits.map((hit, i) => (
            <li key={hit.instrument_token}>
              <button
                type="button"
                onMouseEnter={() => setActive(i)}
                onClick={() => pick(hit)}
                className={cn(
                  "flex w-full items-baseline gap-2 rounded px-2 py-1.5 text-left text-sm",
                  i === active ? "bg-accent text-accent-foreground" : "hover:bg-accent/50",
                )}
              >
                <span className="font-medium tabular-nums">{hit.tradingsymbol}</span>
                <span className="truncate text-xs text-muted-foreground">{hit.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

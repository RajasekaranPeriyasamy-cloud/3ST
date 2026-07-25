import { Link } from "@tanstack/react-router";
import { ExternalLink, Loader2, Maximize2 } from "lucide-react";
import type { ReactNode } from "react";

import type { WidgetId } from "@/context/AnalyticsDeskContext";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

export function WidgetShell({
  title,
  fullRoute,
  deskFocus,
  loading,
  authError,
  error,
  meta,
  children,
}: {
  title: string;
  fullRoute: string;
  /** When set, shows Full view → Widget Desk focused on this board */
  deskFocus?: WidgetId;
  loading?: boolean;
  authError?: boolean;
  error?: string | null;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="flex h-full min-h-0 flex-col border-primary/15 bg-card/95 shadow-sm">
      <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0 px-3 py-2.5">
        <div className="min-w-0">
          <CardTitle className="truncate text-sm font-semibold">{title}</CardTitle>
          {meta ? <div className="mt-0.5 text-[10px] text-muted-foreground">{meta}</div> : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
          {deskFocus ? (
            <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" asChild title="Full view on Widget Desk">
              <Link to="/widget-desk" search={{ focus: deskFocus, full: true }}>
                <Maximize2 className="mr-1 h-3 w-3" />
                Full
              </Link>
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" className="h-7 px-2 text-[11px]" asChild>
            <Link to={fullRoute}>
              Open
              <ExternalLink className="ml-1 h-3 w-3" />
            </Link>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-auto px-3 pb-3 pt-0">
        {authError ? (
          <p className="py-6 text-center text-xs text-destructive">
            Kite session required.{" "}
            <Link to="/login" className="underline">
              Log in
            </Link>
          </p>
        ) : error && !children ? (
          <p className="py-6 text-center text-xs text-destructive">{error}</p>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

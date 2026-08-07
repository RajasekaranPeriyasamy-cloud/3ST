import { Link } from "@tanstack/react-router";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { ApiHealthState } from "@/hooks/useApiHealth";

type Props = Pick<
  ApiHealthState,
  "reachable" | "checking" | "kiteAuthenticated" | "kiteConfigured" | "userId" | "userName" | "loginTime"
>;

function statusMeta(props: Props): {
  label: string;
  dotClass: string;
  textClass: string;
  hint: string;
  href?: string;
} {
  const { reachable, checking, kiteAuthenticated, kiteConfigured, userId, userName, loginTime } =
    props;

  if (checking) {
    return {
      label: "Checking…",
      dotClass: "bg-muted-foreground/60 animate-pulse",
      textClass: "text-muted-foreground",
      hint: "Checking Kite session…",
    };
  }

  if (!reachable) {
    return {
      label: "API offline",
      dotClass: "bg-red-500",
      textClass: "text-red-400",
      hint: "Backend API unreachable — start Start_API.cmd",
    };
  }

  if (kiteConfigured === false) {
    return {
      label: "Kite not configured",
      dotClass: "bg-orange-500",
      textClass: "text-orange-400",
      hint: "Set KITE_API_KEY and KITE_API_SECRET in .env",
    };
  }

  if (kiteAuthenticated) {
    const who = userName || userId || "Zerodha";
    const when = loginTime ? `Logged in ${loginTime}` : "Session active";
    return {
      label: "Connected",
      dotClass: "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.65)]",
      textClass: "text-emerald-400",
      hint: `${who} · ${when}`,
    };
  }

  return {
    label: "Not connected",
    dotClass: "bg-orange-500",
    textClass: "text-orange-400",
    hint: "Kite login required for live quotes and orders",
    href: "/login",
  };
}

export function KiteConnectionIndicator(props: Props) {
  const meta = statusMeta(props);
  const body = (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border border-border/60 bg-muted/30 px-2.5 py-1 text-xs font-medium",
        meta.textClass,
      )}
    >
      <span className={cn("h-2 w-2 shrink-0 rounded-full", meta.dotClass)} aria-hidden />
      <span>Kite</span>
      <span className="hidden sm:inline opacity-90">· {meta.label}</span>
    </span>
  );

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          {meta.href ? (
            <Link to={meta.href} className="outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring rounded-full">
              {body}
            </Link>
          ) : (
            <button type="button" className="cursor-default outline-none rounded-full">
              {body}
            </button>
          )}
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs text-xs">
          {meta.hint}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

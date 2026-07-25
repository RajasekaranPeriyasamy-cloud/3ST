import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  BookMarked,
  Cpu,
  LayoutDashboard,
  Layers,
  RefreshCw,
  Shield,
  TrendingUp,
  Waves,
  Zap,
} from "lucide-react";

import { api } from "@/lib/api";
import type { HealthResponse, PremiumBookStatus, RollingStraddleStatus } from "@/lib/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/execution")({
  component: ExecutionHubPage,
});

interface ArmStatus {
  armed?: boolean;
  mode?: "paper" | "live";
  note?: string;
}

const STRATEGIES = [
  {
    id: "rolling-straddle",
    title: "Rolling Straddle",
    description:
      "Auto ATM CE/PE on 3ST signals from 9:20. Rolls strike with spot; CE and PE run independently with reentry caps.",
    href: "/rolling-straddle",
    icon: TrendingUp,
    badge: "Automated",
  },
  {
    id: "premium-book",
    title: "Premium Book",
    description:
      "Table 2.1 short premium — bull put / bear call / strangle / straddle. ST1+ADX entry; Force → ATR → ST1; SL converts to credit vertical.",
    href: "/premium-book",
    icon: BookMarked,
    badge: "Paper first",
  },
  {
    id: "survivor",
    title: "Survivor Algo",
    description:
      "Gap-based NIFTY option premium selling — sells PE on rises and CE on drops with multiplier scaling.",
    href: "/survivor",
    icon: Zap,
    badge: "trading-algo",
  },
  {
    id: "wave",
    title: "Wave Algo",
    description:
      "Limit buy/sell wave pairs on futures with delta-based restrictions and order-cycle management.",
    href: "/wave",
    icon: Waves,
    badge: "trading-algo",
  },
  {
    id: "oi-var-desk",
    title: "OI VAR Live Desk",
    description:
      "Full-chain Top/Bottom 10 by VAR (Cr) and EOD OI change — CE/PE panels with LTP.",
    href: "/oi-var",
    icon: Layers,
    badge: "Analytics",
  },
  {
    id: "watchlist-desk",
    title: "Watchlist Live Desk",
    description:
      "Manual queue workflow — add instruments on Dashboard, scan for 3ST signals, activate from Live Desk.",
    href: "/live",
    icon: Activity,
    badge: "Manual",
  },
  {
    id: "dashboard-queue",
    title: "Signal Queue",
    description: "Manage waiting watchlist items and run scans before signals move to Live Desk.",
    href: "/dashboard",
    icon: LayoutDashboard,
    badge: "Queue",
  },
] as const;

function ExecutionHubPage() {
  const [arm, setArm] = useState<ArmStatus | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [rsStatus, setRsStatus] = useState<RollingStraddleStatus | null>(null);
  const [pbStatus, setPbStatus] = useState<PremiumBookStatus | null>(null);
  const [positionCount, setPositionCount] = useState(0);

  async function refresh() {
    try {
      const [a, h, rs, pb, pos] = await Promise.all([
        api.get<ArmStatus>("/live/arm", { silent: true }),
        api.get<HealthResponse>("/health", { silent: true }),
        api.get<RollingStraddleStatus>("/live/rolling-straddle/status", { silent: true }).catch(() => null),
        api.get<PremiumBookStatus>("/live/premium-book/status", { silent: true }).catch(() => null),
        api.get<{ positions?: unknown[] }>("/live/positions", { silent: true }).catch(() => ({ positions: [] })),
      ]);
      setArm(a);
      setHealth(h);
      if (rs) setRsStatus(rs);
      if (pb) setPbStatus(pb);
      setPositionCount(pos.positions?.length ?? 0);
    } catch {
      /* silent */
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 8000);
    return () => clearInterval(t);
  }, []);

  const rsRunning = rsStatus?.state?.runner === "running";
  const pbRunning = pbStatus?.state?.runner === "running";

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6 pb-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Algo Execution</h1>
          <p className="text-sm text-muted-foreground">
            Choose a strategy, monitor global safety state, and open dedicated control pages
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={refresh}>
          <RefreshCw className="h-4 w-4" />
        </Button>
      </header>

      {/* Global safety strip */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Shield className="h-4 w-4" />
            Global execution state
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Badge variant={arm?.armed ? "destructive" : "secondary"}>
            {arm?.armed ? "ARMED" : "DISARMED"}
          </Badge>
          <Badge variant="outline">{arm?.mode?.toUpperCase() ?? "PAPER"}</Badge>
          <Badge variant={health?.kite_authenticated ? "outline" : "secondary"}>
            Kite: {health?.kite_authenticated ? "connected" : "login required"}
          </Badge>
          <Badge variant="outline">{positionCount} open position(s)</Badge>
          {rsRunning ? (
            <Badge className="bg-primary">Rolling Straddle running</Badge>
          ) : null}
          {pbRunning ? (
            <Badge className="bg-primary">Premium Book running</Badge>
          ) : null}
          <div className="ml-auto flex gap-2">
            <Button asChild size="sm" variant="outline">
              <Link to="/live">Live Desk</Link>
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to="/settings">Risk limits</Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Strategy cards */}
      <div>
        <h2 className="mb-3 text-sm font-medium text-muted-foreground uppercase tracking-wide">
          Strategies
        </h2>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {STRATEGIES.map((s) => (
            <Card key={s.id} className="flex flex-col">
              <CardHeader>
                <div className="flex items-start justify-between gap-2">
                  <s.icon className="mt-0.5 h-5 w-5 text-muted-foreground" />
                  <Badge variant="secondary" className="text-[10px]">
                    {s.badge}
                  </Badge>
                </div>
                <CardTitle className="text-base">{s.title}</CardTitle>
                <CardDescription className="text-xs leading-relaxed">
                  {s.description}
                </CardDescription>
              </CardHeader>
              <CardContent className="mt-auto pt-0">
                {s.id === "rolling-straddle" && rsStatus?.state ? (
                  <div className="mb-3 space-y-1 text-xs font-mono text-muted-foreground">
                    <div>ATM: {rsStatus.state.current_atm ?? "—"}</div>
                    <div>CE: {rsStatus.state.ce?.status ?? "flat"} · PE: {rsStatus.state.pe?.status ?? "flat"}</div>
                  </div>
                ) : null}
                {s.id === "premium-book" && pbStatus?.state ? (
                  <div className="mb-3 space-y-1 text-xs font-mono text-muted-foreground">
                    <div>
                      {pbStatus.config?.structure ?? "—"} · ATM: {pbStatus.state.current_atm ?? "—"}
                    </div>
                    <div>
                      Pkg: {pbStatus.state.package?.status ?? "flat"} · CE:{" "}
                      {(pbStatus.state.ce as { status?: string } | undefined)?.status ?? "flat"} · PE:{" "}
                      {(pbStatus.state.pe as { status?: string } | undefined)?.status ?? "flat"}
                    </div>
                  </div>
                ) : null}
                <Button asChild className="w-full" size="sm">
                  <Link to={s.href}>
                    Open <ArrowRight className="ml-1 h-4 w-4" />
                  </Link>
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* Quick reference */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Cpu className="h-4 w-4" />
            Recommended flow
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            <strong className="text-foreground">Rolling Straddle:</strong> Configure on{" "}
            <Link to="/rolling-straddle" className="text-primary underline-offset-4 hover:underline">
              /rolling-straddle
            </Link>
            {" "}→ paper test → live + ARM when ready.
          </p>
          <p>
            <strong className="text-foreground">Premium Book:</strong> Credit verticals & short premium on{" "}
            <Link to="/premium-book" className="text-primary underline-offset-4 hover:underline">
              /premium-book
            </Link>
            {" "}— paper first; SL on straddle/strangle converts to a defined-risk wing.
          </p>
          <p>
            <strong className="text-foreground">Watchlist desk:</strong> Stock Selection → Dashboard queue → scan → Live Desk activate.
          </p>
          <p>DISARM is the kill switch for all live Kite orders across strategies.</p>
        </CardContent>
      </Card>
    </div>
  );
}

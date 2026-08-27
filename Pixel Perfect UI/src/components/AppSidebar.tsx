import { Link, useRouterState } from "@tanstack/react-router";
import {
  AlignVerticalDistributeCenter,
  Activity,
  ArrowLeftRight,
  BarChart3,
  Blend,
  BookMarked,
  CandlestickChart,
  ChartNoAxesCombined,
  ChevronLeft,
  ChevronRight,
  Cpu,
  FileSearch,
  Gavel,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  LineChart,
  Gauge,
  Grid3x3,
  LogIn,
  Orbit,
  Radar,
  Scale,
  Sigma,
  Settings,
  Target,
  TrendingUp,
  TableProperties,
} from "lucide-react";
import { useExecutionQueue } from "@/hooks/useExecutionQueue";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";

const items = [
  { title: "Stock Selection", url: "/", icon: Target },
  { title: "Dashboard", url: "/dashboard", icon: LayoutDashboard },
  { title: "Widget Desk", url: "/widget-desk", icon: LayoutGrid },
  { title: "Backtest", url: "/backtest", icon: BarChart3 },
  { title: "OI Tracker", url: "/oi-tracker", icon: Layers },
  { title: "OI Movers", url: "/oi-movers", icon: ArrowLeftRight },
  { title: "OI VAR Desk", url: "/oi-var", icon: Scale },
  { title: "Gamma Density", url: "/gamma-density", icon: Sigma },
  { title: "Delta Velocity", url: "/delta-velocity", icon: Radar },
  { title: "CAS Indicative", url: "/cas-indicative", icon: Gavel },
  { title: "Vanna Exposure", url: "/vanna-exposure", icon: Orbit },
  { title: "Vol Surface", url: "/vol-surface", icon: Grid3x3 },
  { title: "IV Skew", url: "/iv-skew", icon: Blend },
  { title: "OI Profile", url: "/oi-profile", icon: CandlestickChart },
  { title: "Chain Build-Up", url: "/chain-buildup", icon: TableProperties },
  { title: "Options Arbitrage", url: "/opt-arb", icon: Scale },
  { title: "Volume Footprint", url: "/volume-footprint", icon: AlignVerticalDistributeCenter },
  { title: "Equity Report", url: "/equity-report", icon: FileSearch },
  { title: "Algo Execution", url: "/execution", icon: Cpu },
  { title: "Straddle Watch", url: "/straddle-watch", icon: ChartNoAxesCombined },
  { title: "Rolling Straddle", url: "/rolling-straddle", icon: TrendingUp },
  { title: "Premium Book", url: "/premium-book", icon: BookMarked },
  { title: "Live Desk", url: "/live", icon: Activity },
  { title: "Execution Health", url: "/latency", icon: Gauge },
  { title: "Settings", url: "/settings", icon: Settings },
];

export function AppSidebar() {
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const { queue } = useExecutionQueue(8000);
  const { state, toggleSidebar } = useSidebar();
  const orphanCount = queue?.summary?.orphan_count ?? 0;
  const isActive = (url: string) =>
    url === "/" ? pathname === "/" : pathname.startsWith(url);
  const collapsed = state === "collapsed";

  // Widget Desk needs a true full-width canvas — offcanvas hides the rail entirely.
  const collapsible = pathname.startsWith("/widget-desk") ? "offcanvas" : "icon";

  return (
    <Sidebar collapsible={collapsible}>
      <SidebarHeader className="relative border-b border-sidebar-border">
        <div
          className={cn(
            "flex items-center gap-2 px-2 py-3 pr-9",
            collapsed && "justify-center px-0 pr-0 pt-8",
          )}
        >
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-primary to-chart-1 text-primary-foreground shadow-sm shadow-primary/30">
            <LineChart className="h-4 w-4" />
          </div>
          <div className="flex min-w-0 flex-col leading-tight group-data-[collapsible=icon]:hidden">
            <span className="truncate text-sm font-semibold text-foreground">3ST Algo Desk</span>
            <span className="truncate text-[10px] font-medium tracking-wide text-primary uppercase">
              Kite Control Panel
            </span>
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={cn(
            "absolute z-20 h-6 w-6 rounded-md border border-sidebar-border/80 bg-background/90 text-foreground shadow-sm hover:bg-sidebar-accent",
            collapsed
              ? "right-1/2 top-1.5 translate-x-1/2"
              : "right-1.5 top-1.5",
          )}
          title={collapsed ? "Open sidebar" : "Close sidebar"}
          aria-label={collapsed ? "Open sidebar" : "Close sidebar"}
          onClick={toggleSidebar}
        >
          {collapsed ? (
            <ChevronRight className="h-3.5 w-3.5" />
          ) : (
            <ChevronLeft className="h-3.5 w-3.5" />
          )}
        </Button>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Trading</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {items.map((item) => (
                <SidebarMenuItem key={item.title}>
                  <SidebarMenuButton asChild isActive={isActive(item.url)}>
                    <Link to={item.url} className="flex items-center gap-2">
                      <item.icon className="h-4 w-4" />
                      <span>{item.title}</span>
                      {item.url === "/execution" && orphanCount > 0 ? (
                        <span className="ml-auto rounded-full bg-destructive px-1.5 py-0.5 text-[10px] font-semibold text-destructive-foreground">
                          {orphanCount}
                        </span>
                      ) : null}
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter className="border-t border-sidebar-border">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild isActive={pathname === "/login"}>
              <Link to="/login" className="flex items-center gap-2">
                <LogIn className="h-4 w-4" />
                <span>Kite Login</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}

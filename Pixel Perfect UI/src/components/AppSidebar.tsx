import { Link, useRouterState } from "@tanstack/react-router";
import {
  Activity,
  BarChart3,
  Cpu,
  Layers,
  LayoutDashboard,
  LineChart,
  LogIn,
  Scale,
  Settings,
  Target,
  TrendingUp,
} from "lucide-react";
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
} from "@/components/ui/sidebar";

const items = [
  { title: "Stock Selection", url: "/", icon: Target },
  { title: "Dashboard", url: "/dashboard", icon: LayoutDashboard },
  { title: "Backtest", url: "/backtest", icon: BarChart3 },
  { title: "OI Tracker", url: "/oi-tracker", icon: Layers },
  { title: "OI VAR Desk", url: "/oi-var", icon: Scale },
  { title: "Algo Execution", url: "/execution", icon: Cpu },
  { title: "Rolling Straddle", url: "/rolling-straddle", icon: TrendingUp },
  { title: "Live Desk", url: "/live", icon: Activity },
  { title: "Settings", url: "/settings", icon: Settings },
];

export function AppSidebar() {
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const isActive = (url: string) =>
    url === "/" ? pathname === "/" : pathname.startsWith(url);

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="border-b border-sidebar-border">
        <div className="flex items-center gap-2 px-2 py-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <LineChart className="h-4 w-4" />
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold">3ST Algo Desk</span>
            <span className="text-[10px] text-muted-foreground tracking-wide uppercase">
              Kite Control Panel
            </span>
          </div>
        </div>
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

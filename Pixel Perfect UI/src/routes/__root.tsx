import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  useRouterState,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import { useEffect, type ReactNode } from "react";

import appCss from "../styles.css?url";
import { reportLovableError } from "../lib/lovable-error-reporting";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/AppSidebar";
import { ApiHealthBanner } from "@/components/ApiHealthBanner";
import { KiteConnectionIndicator } from "@/components/KiteConnectionIndicator";
import { ExecutionTaskbar } from "@/components/execution/ExecutionTaskbar";
import { useApiHealth } from "@/hooks/useApiHealth";
import { SelectionProvider } from "@/context/SelectionContext";
import { WatchlistProvider } from "@/context/WatchlistContext";
import { Toaster } from "@/components/ui/sonner";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-7xl font-bold text-foreground">404</h1>
        <h2 className="mt-4 text-xl font-semibold">Page not found</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          The route you're looking for doesn't exist.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:bg-primary/90"
          >
            Back to Stock Selection
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();
  useEffect(() => {
    reportLovableError(error, { boundary: "tanstack_root_error_component" });
  }, [error]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1 className="text-xl font-semibold">This page didn't load</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {error.message || "Something went wrong."}
        </p>
        <div className="mt-6 flex justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            Try again
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium hover:bg-accent"
          >
            Home
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "3ST Algo Desk — Kite Control Panel" },
      {
        name: "description",
        content:
          "Algo trading control panel for Zerodha Kite Connect. 3ST (Triple HA SuperTrend + ADX) strategy with backtest and live desk.",
      },
      { name: "author", content: "3ST Algo Desk" },
      { property: "og:title", content: "3ST Algo Desk" },
      {
        property: "og:description",
        content: "Kite Connect algo trading control panel with 3ST strategy.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
    links: [
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=JetBrains+Mono:wght@400;500;600&display=swap",
      },
      { rel: "stylesheet", href: appCss },
      { rel: "icon", href: "/favicon.ico", type: "image/x-icon" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const isLogin = pathname === "/login";
  const isWidgetDesk = pathname.startsWith("/widget-desk");
  const apiHealth = useApiHealth();

  if (isLogin) {
    return (
      <QueryClientProvider client={queryClient}>
        <div className="min-h-screen bg-background text-foreground">
          <div className="flex items-center justify-end border-b border-border px-4 py-2">
            <KiteConnectionIndicator
              reachable={apiHealth.reachable}
              checking={apiHealth.checking}
              kiteAuthenticated={apiHealth.kiteAuthenticated}
              kiteConfigured={apiHealth.kiteConfigured}
              userId={apiHealth.userId}
              userName={apiHealth.userName}
              loginTime={apiHealth.loginTime}
            />
          </div>
          <ApiHealthBanner
            reachable={apiHealth.reachable}
            checking={apiHealth.checking}
            onRetry={apiHealth.retry}
          />
          <Outlet />
          <Toaster richColors position="top-right" theme="light" />
        </div>
      </QueryClientProvider>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <SelectionProvider>
        <WatchlistProvider>
        <SidebarProvider>
          <div className="flex min-h-screen w-full bg-transparent text-foreground">
            <AppSidebar />
            <div className="flex flex-1 flex-col min-w-0">
              <ApiHealthBanner
                reachable={apiHealth.reachable}
                checking={apiHealth.checking}
                onRetry={apiHealth.retry}
              />
              <header className="sticky top-0 z-10 flex h-12 items-center gap-2 border-b border-border/80 bg-card/70 backdrop-blur-md px-3 shadow-sm shadow-primary/5">
                <SidebarTrigger />
                <div className="text-sm font-semibold tracking-tight text-primary">
                  3ST Algo Desk
                </div>
                <div className="ml-auto">
                  <KiteConnectionIndicator
                    reachable={apiHealth.reachable}
                    checking={apiHealth.checking}
                    kiteAuthenticated={apiHealth.kiteAuthenticated}
                    kiteConfigured={apiHealth.kiteConfigured}
                    userId={apiHealth.userId}
                    userName={apiHealth.userName}
                    loginTime={apiHealth.loginTime}
                  />
                </div>
              </header>
              <main
                className={
                  isWidgetDesk
                    ? "flex min-h-0 flex-1 flex-col p-0"
                    : "flex-1 p-4 md:p-6 pb-4"
                }
              >
                <Outlet />
              </main>
              <ExecutionTaskbar />
            </div>
          </div>
          <Toaster richColors position="top-right" theme="light" />
        </SidebarProvider>
        </WatchlistProvider>
      </SelectionProvider>
    </QueryClientProvider>
  );
}

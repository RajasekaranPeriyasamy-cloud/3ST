import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { LineChart, LogOut } from "lucide-react";

import { api, getApiBaseUrl } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

type SessionStatus = {
  authenticated?: boolean;
  kite_configured?: boolean;
  user_id?: string;
  user_name?: string;
  login_time?: string;
};

export const Route = createFileRoute("/login")({
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [redirecting, setRedirecting] = useState(false);
  const [apiReachable, setApiReachable] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    let h: HealthResponse | null = null;
    let s: SessionStatus | null = null;
    let reachable = false;

    try {
      h = await api.get<HealthResponse>("/health", { silent: true });
      reachable = true;
    } catch {
      h = null;
    }

    try {
      s = await api.get<SessionStatus>("/auth/me", { silent: true });
      reachable = true;
    } catch {
      s = null;
    }

    setHealth(h);
    setSession(s);
    setApiReachable(reachable);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const configured = health?.kite_configured ?? health?.configured ?? false;
  const authenticated = session?.authenticated ?? health?.kite_authenticated ?? false;

  useEffect(() => {
    if (!loading && authenticated) {
      navigate({ to: "/" });
    }
  }, [loading, authenticated, navigate]);

  async function loginWithKite() {
    setRedirecting(true);
    try {
      const r = await api.get<{ login_url: string }>("/auth/login-url");
      if (!r?.login_url) {
        toast.error("No login URL returned — check KITE_API_KEY in .env");
        setRedirecting(false);
        return;
      }
      window.location.href = r.login_url;
    } catch {
      setRedirecting(false);
    }
  }

  async function logout() {
    try {
      await api.del("/auth/session");
      toast.success("Signed out");
      await refresh();
    } catch {
      /* handled */
    }
  }

  if (!loading && authenticated) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <LineChart className="h-6 w-6" />
            </div>
            <CardTitle>Already connected</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col items-center gap-4 text-center">
            <p className="text-sm text-muted-foreground">
              Logged in as{" "}
              <span className="font-semibold text-foreground">
                {session?.user_name || session?.user_id || "Kite user"}
              </span>
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              <Button onClick={() => navigate({ to: "/" })}>Open Stock Selection</Button>
              <Button variant="outline" onClick={logout}>
                <LogOut className="mr-2 h-4 w-4" /> Sign out
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center px-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <LineChart className="h-6 w-6" />
          </div>
          <CardTitle>3ST Algo Desk</CardTitle>
          <p className="text-sm text-muted-foreground">Sign in with Zerodha Kite to start the session</p>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex items-center justify-between rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
            <span className="text-muted-foreground">Backend</span>
            <Badge variant={!apiReachable ? "destructive" : configured ? "default" : "destructive"}>
              {loading ? "…" : !apiReachable ? "Unreachable" : configured ? "Ready" : "Not configured"}
            </Badge>
          </div>

          {!apiReachable && !loading ? (
            <p className="text-center text-sm text-destructive">
              Cannot reach API at <code className="rounded bg-muted px-1">{getApiBaseUrl()}</code>.
              Run <code className="rounded bg-muted px-1">Start_3ST.cmd</code> from the project folder
              (or <code className="rounded bg-muted px-1">powershell -ExecutionPolicy Bypass -File .\scripts\start_3st_dev.ps1</code>)
              and refresh this page.
            </p>
          ) : null}

          {!configured && !loading && apiReachable ? (
            <p className="text-center text-sm text-destructive">
              Set <code className="rounded bg-muted px-1">KITE_API_KEY</code> and{" "}
              <code className="rounded bg-muted px-1">KITE_API_SECRET</code> in the project{" "}
              <code className="rounded bg-muted px-1">.env</code>, then restart the API.
            </p>
          ) : null}

          <Button
            size="lg"
            className="w-full"
            onClick={loginWithKite}
            disabled={!configured || redirecting || loading}
          >
            {redirecting ? "Opening Zerodha…" : "Login with Zerodha"}
          </Button>

          <p className="text-center text-xs text-muted-foreground">
            One click — Zerodha redirects back automatically. No token paste needed.
          </p>

          <p className="text-center text-xs text-muted-foreground">
            API-only fallback:{" "}
            <a
              href={`${getApiBaseUrl() || "http://127.0.0.1:8001"}/auth/login`}
              className="underline hover:text-foreground"
              target="_blank"
              rel="noreferrer"
            >
              /auth/login
            </a>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

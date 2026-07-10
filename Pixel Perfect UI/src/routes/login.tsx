import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ExternalLink, LogOut } from "lucide-react";

import { api } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";

export const Route = createFileRoute("/login")({
  component: LoginPage,
});

function LoginPage() {
  const navigate = useNavigate();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [reqToken, setReqToken] = useState("");
  const [connecting, setConnecting] = useState(false);

  useEffect(() => {
    api.get<HealthResponse>("/health", { silent: true }).then(setHealth).catch(() => {});
  }, []);

  async function openKiteLogin() {
    try {
      const r = await api.get<{ login_url: string }>("/auth/login-url");
      if (r?.login_url) window.open(r.login_url, "_blank", "noopener");
      else toast.error("No login URL returned");
    } catch {
      /* handled */
    }
  }

  async function connect() {
    if (!reqToken.trim()) {
      toast.error("Paste the request_token from the redirect URL");
      return;
    }
    setConnecting(true);
    try {
      await api.post("/auth/session", { request_token: reqToken.trim() });
      toast.success("Connected to Kite");
      navigate({ to: "/" });
    } catch {
      /* handled */
    } finally {
      setConnecting(false);
    }
  }

  async function logout() {
    try {
      await api.del("/auth/session");
      toast.success("Signed out");
    } catch {
      /* handled */
    }
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Kite Login</h1>
        <p className="text-sm text-muted-foreground">
          Connect the backend to your Zerodha Kite Connect session.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between text-base">
            Backend health
            <Badge
              variant={
                health?.kite_configured ?? health?.configured ? "default" : "destructive"
              }
            >
              {health
                ? (health.kite_configured ?? health.configured ? "Configured" : "Not configured")
                : "Unknown"}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {health ? (
            <pre className="rounded-md bg-muted/40 p-3 font-mono text-xs">
              {JSON.stringify(health, null, 2)}
            </pre>
          ) : (
            "Contacting backend…"
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">1 — Open Kite login</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            Opens Zerodha's OAuth screen. After you approve, Zerodha redirects with a{" "}
            <code className="rounded bg-muted px-1 font-mono">request_token</code>{" "}
            in the URL. Copy that value and paste it below.
          </p>
          <div>
            <Button onClick={openKiteLogin}>
              <ExternalLink className="mr-2 h-4 w-4" /> Open Kite Login
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">2 — Paste request_token</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <Label htmlFor="req">request_token</Label>
          <Input
            id="req"
            value={reqToken}
            onChange={(e) => setReqToken(e.target.value)}
            placeholder="e.g. abc123XYZ…"
            className="font-mono"
          />
          <div className="flex gap-2">
            <Button onClick={connect} disabled={connecting}>
              {connecting ? "Connecting…" : "Connect"}
            </Button>
            <Button variant="outline" onClick={logout}>
              <LogOut className="mr-2 h-4 w-4" /> Logout
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

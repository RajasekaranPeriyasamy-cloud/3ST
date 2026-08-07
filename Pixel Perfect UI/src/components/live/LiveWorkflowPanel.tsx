import { CheckCircle2, Circle, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export interface WorkflowStep {
  ok: boolean;
  label: string;
  detail?: string;
}

export interface WorkflowStatus {
  kite_authenticated?: boolean;
  mode?: string;
  armed?: boolean;
  ready_to_execute?: boolean;
  waiting_manual?: number;
  active_trades?: number;
  steps?: WorkflowStep[];
}

export function LiveWorkflowPanel({ workflow }: { workflow: WorkflowStatus | null }) {
  if (!workflow?.steps?.length) return null;

  return (
    <Card className="border-primary/20">
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Live workflow</CardTitle>
        <p className="text-xs text-muted-foreground">
          Instrument → Manual entry → 3ST exit → Live Desk → BUY/SELL → Exchange order → Auto exit
        </p>
      </CardHeader>
      <CardContent>
        <ol className="grid gap-2 md:grid-cols-2">
          {workflow.steps.map((step, i) => (
            <li
              key={i}
              className={cn(
                "flex items-start gap-2 rounded-md border px-3 py-2 text-sm",
                step.ok ? "border-bull/30 bg-bull/5" : "border-border bg-muted/20",
              )}
            >
              {step.ok ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-bull" />
              ) : i === 5 && workflow.mode === "live" ? (
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
              ) : (
                <Circle className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              )}
              <div>
                <div className={step.ok ? "text-foreground" : "text-muted-foreground"}>{step.label}</div>
                {step.detail && <div className="text-xs text-muted-foreground">{step.detail}</div>}
              </div>
            </li>
          ))}
        </ol>
        {!workflow.kite_authenticated && (
          <p className="mt-3 text-xs text-bear">Sign in to Kite first (auth URL / Settings).</p>
        )}
        {workflow.mode === "live" && !workflow.armed && (
          <p className="mt-3 text-xs text-amber-400">
            LIVE mode is on but DISARMED — click <strong>ARM</strong> below, then BUY or SELL.
          </p>
        )}
        {workflow.ready_to_execute && (
          <p className="mt-3 text-xs text-bull">Ready — click BUY or SELL on your instrument below.</p>
        )}
      </CardContent>
    </Card>
  );
}

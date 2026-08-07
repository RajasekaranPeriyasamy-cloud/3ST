import { AlertTriangle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { ApiHealthState } from "@/hooks/useApiHealth";

type Props = Pick<ApiHealthState, "reachable" | "checking"> & {
  onRetry: () => void;
};

export function ApiHealthBanner({ reachable, checking, onRetry }: Props) {
  if (reachable || checking) return null;

  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-200"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <div>
          <p className="font-medium">Backend API unreachable (port 8001)</p>
          <p className="text-xs text-amber-200/80">
            UI is up but FastAPI is down - expiries, save, login, and live trading will fail until
            the API is running. Double-click{" "}
            <span className="font-mono text-amber-100">Start_API.cmd</span> or{" "}
            <span className="font-mono text-amber-100">Run_API.cmd</span> or{" "}
            <span className="font-mono text-amber-100">Start_3ST.cmd</span> in the repo root.
            Keep the green API window open.
          </p>
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="shrink-0 border-amber-500/50 text-amber-100 hover:bg-amber-500/20"
        onClick={onRetry}
      >
        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
        Retry
      </Button>
    </div>
  );
}

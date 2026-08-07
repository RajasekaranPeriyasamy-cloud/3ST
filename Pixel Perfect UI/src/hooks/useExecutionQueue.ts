import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { ExecutionQueueResponse } from "@/lib/types";

export function useExecutionQueue(intervalMs = 5000) {
  const [queue, setQueue] = useState<ExecutionQueueResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.get<ExecutionQueueResponse>("/execution/queue", { silent: true });
      setQueue(data);
    } catch {
      /* silent poll */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, refresh]);

  async function runAction(legId: string, action: string) {
    await api.post(`/execution/queue/${encodeURIComponent(legId)}/action`, { action });
    await refresh();
  }

  return { queue, loading, refresh, runAction };
}

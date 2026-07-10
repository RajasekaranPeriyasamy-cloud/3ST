import { toast } from "sonner";

const BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ||
  "http://127.0.0.1:8000";

export function getApiBaseUrl() {
  return BASE_URL;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

function formatApiError(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          const loc = "loc" in item && Array.isArray(item.loc) ? item.loc.join(".") : "";
          return loc ? `${loc}: ${String(item.msg)}` : String(item.msg);
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }
  return fallback;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  { silent = false }: { silent?: boolean } = {},
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  let res: Response;
  try {
    res = await fetch(url, { ...init, headers });
  } catch (e) {
    const msg = `Network error contacting ${url}`;
    if (!silent) toast.error(msg);
    throw new ApiError(0, msg);
  }

  const text = await res.text();
  const data = text ? safeJson(text) : null;

  if (!res.ok) {
    const detail = formatApiError(data, text || res.statusText);
    if (res.status === 401 && typeof window !== "undefined") {
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    if (!silent) toast.error(detail);
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

function safeJson(t: string): unknown {
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

export const api = {
  get: <T>(path: string, opts?: { silent?: boolean }) =>
    request<T>(path, { method: "GET" }, opts),
  post: <T>(path: string, body?: unknown, opts?: { silent?: boolean }) =>
    request<T>(
      path,
      { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) },
      opts,
    ),
  del: <T>(path: string, opts?: { silent?: boolean }) =>
    request<T>(path, { method: "DELETE" }, opts),
};

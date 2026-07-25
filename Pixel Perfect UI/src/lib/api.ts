import { toast } from "sonner";

function resolveApiBaseUrl(): string {
  const fromEnv = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(
    /\/$/,
    "",
  );
  if (fromEnv) return fromEnv;

  // Dev UI on :8080 must not call itself for API (SPA returns HTML).
  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location;
    if (port === "8080" || port === "8081" || port === "8082" || port === "5173") {
      return `${protocol}//${hostname}:8001`;
    }
  }
  return "";
}

export function getApiBaseUrl() {
  return resolveApiBaseUrl();
}

/** Build a ws:// or wss:// URL for a backend WebSocket path (e.g. "/ws/ltp"). */
export function getWsUrl(path: string) {
  const origin =
    resolveApiBaseUrl() ||
    (typeof window !== "undefined" ? window.location.origin : "");
  const wsOrigin = origin.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  return `${wsOrigin}${path}`;
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
  const base = resolveApiBaseUrl();
  const url = `${base}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
    // Defeat browsers that cached SPA HTML for these URLs when routes were missing.
    "Cache-Control": "no-cache",
    Pragma: "no-cache",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  let res: Response;
  try {
    res = await fetch(url, { ...init, headers, cache: "no-store" });
  } catch (e) {
    const msg = `Network error contacting ${url}`;
    if (!silent) toast.error(msg);
    throw new ApiError(0, msg);
  }

  const text = await res.text();
  let data: unknown = null;
  let parseOk = false;
  if (text) {
    try {
      data = JSON.parse(text);
      parseOk = true;
    } catch {
      data = text;
    }
  }
  const looksLikeHtml = typeof data === "string" && /^\s*</.test(data);

  if (!res.ok) {
    const detail = looksLikeHtml
      ? `API ${res.status} for ${url}`
      : formatApiError(data, text || res.statusText);
    if (res.status === 401 && typeof window !== "undefined") {
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    if (!silent) toast.error(detail);
    throw new ApiError(res.status, detail);
  }
  // SPA fallback can return index.html with 200 — never treat that as JSON data.
  if (!parseOk || looksLikeHtml) {
    // One cache-bust retry (stale HTML from when API routes were missing).
    if (!path.includes("_cb=")) {
      const join = path.includes("?") ? "&" : "?";
      return request<T>(`${path}${join}_cb=${Date.now()}`, init, { silent });
    }
    const snippet = text.replace(/\s+/g, " ").slice(0, 80);
    const detail =
      `Expected JSON from ${url}, got HTML/text (${snippet}…). ` +
      `Clear site data for 127.0.0.1:8001 (DevTools → Application → Clear storage), then hard-refresh.`;
    if (!silent) toast.error(detail);
    throw new ApiError(res.status || 502, detail);
  }
  return data as T;
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

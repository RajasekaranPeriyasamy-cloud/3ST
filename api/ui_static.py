"""Serve built Pixel Perfect UI from FastAPI (single-port mode)."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

_UI_PUBLIC = Path(__file__).resolve().parents[1] / "Pixel Perfect UI" / ".output" / "public"

# Paths handled by API routes — SPA fallback must not intercept these.
_API_PREFIXES = (
    "/api/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/auth",
    "/live",
    "/backtest",
    "/oi-tracker",
    "/oi-movers",
    "/oi-var",
    "/gamma-density",
    "/cas/",  # trailing slash so SPA /cas-indicative is not treated as API
    "/vanna-exposure",
    "/greeks",
    "/trade-suggestions",
    "/pricing",
    "/straddle-watch",
    "/oi-profile",
    "/vol-surface",
    "/iv-smile",
    "/arbitrage",
    "/rrg",
    "/analogue",
    "/latency",
    "/execution",
    "/market",
    "/options",
    "/instruments",
    "/selection",
    "/watchlist",
    "/margins",
    "/risk",
)


def ui_public_dir() -> Path:
    return _UI_PUBLIC


def ui_build_available() -> bool:
    return (_UI_PUBLIC / "index.html").is_file()


def is_api_path(path: str) -> bool:
    if path.startswith("/assets/"):
        return True
    for prefix in _API_PREFIXES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False


def mount_ui(app) -> None:
    """Mount static UI assets and SPA fallback when build output exists.

    SPA pages are served via a 404 exception handler (not a GET catch-all route).
    A GET-only ``/{path}`` catch-all makes every unknown path look like a GET
    route, so POST/PUT to a missing API path incorrectly returns 405 Method Not
    Allowed — the Premium Book symptom when the API process is stale.
    """
    if not ui_build_available():
        return

    assets_dir = _UI_PUBLIC / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="ui-assets")

    index_path = _UI_PUBLIC / "index.html"
    shell_path = _UI_PUBLIC / "_shell.html"

    @app.get("/", include_in_schema=False)
    async def ui_root():
        return FileResponse(index_path, media_type="text/html")

    async def _spa_or_pass(request: Request, exc: StarletteHTTPException):
        if exc.status_code != 404:
            return await http_exception_handler(request, exc)

        path = request.url.path
        if is_api_path(path):
            # Preserve JSON 404 for API clients (toast detail).
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        if request.method not in ("GET", "HEAD"):
            return JSONResponse({"detail": "Method Not Allowed"}, status_code=405)

        # Exact static file under public root (favicon, robots, etc.)
        rel = path.lstrip("/")
        if rel:
            candidate = (_UI_PUBLIC / rel).resolve()
            try:
                candidate.relative_to(_UI_PUBLIC.resolve())
            except ValueError:
                return HTMLResponse("Not Found", status_code=404)
            if candidate.is_file():
                return FileResponse(candidate)

        fallback = shell_path if shell_path.is_file() else index_path
        return FileResponse(fallback, media_type="text/html")

    app.add_exception_handler(StarletteHTTPException, _spa_or_pass)
    app.add_exception_handler(HTTPException, _spa_or_pass)

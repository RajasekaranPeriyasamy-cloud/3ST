# Pixel Perfect UI

React 19 + Vite 8 + Tailwind 4 + shadcn/Radix + TanStack Router. Client-rendered
SPA — no SSR, no server functions.

This project is **no longer connected to Lovable** (removed 2026-08-08; see
`docs/UI_LOVABLE_EXIT_PLAN.md`). The previous force-push restriction no longer
applies, since nothing syncs back to an external editor.

## Build

```bash
npm run dev     # Vite dev server on 127.0.0.1:8080
npm run build   # vite build -> .output/public (served by FastAPI, api/ui_static.py)
```

⚠️ **Stop the FastAPI process before `npm run build`.** It mounts
`.output/public/assets` via `StaticFiles`, which holds a Windows directory lock;
`emptyOutDir` then fails with `EBUSY`.

## Conventions

- `index.html` at the project root owns `<head>`: title, meta, fonts, favicon, and
  the inline theme boot script (mirrors `THEME_BOOT_SCRIPT` in `src/lib/theme.ts` —
  keep both in sync).
- `src/routes/` is file-based; `routeTree.gen.ts` is generated — never hand-edit.
- API calls go through `src/lib/api.ts` (`resolveApiBaseUrl()`), never hardcoded hosts.
- A new route's path must not collide with an API prefix in `api/ui_static.py`, or
  direct browser loads return a JSON 404.

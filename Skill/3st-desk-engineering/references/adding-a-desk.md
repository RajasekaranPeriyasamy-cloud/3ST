# Adding or changing a desk

A "desk" is one analysis surface: a backend engine module, a set of API routes
under one prefix, and one SPA page. Five things have to line up, and the two
that most often don't are **route-name collision** and **which of the two UI
surfaces you actually verified**.

---

## 1. The backend engine module

Lives under `analysis/<desk>/` or `options/`. The full-fat pattern is
`analysis/delta_velocity/`:

| File | Role |
|---|---|
| `collector.py` | pulls a snapshot from the broker |
| `store.py` | persists it under `data/` |
| `runner.py` | daemon thread driving the collector on a tick |
| `features.py` | derives the numbers |
| `chart.py` | shapes them for the UI |

Not every desk needs all five. `analysis/theta_decay/` has **only**
`features.py` + `chart.py` — no collector, store, or runner — because it reads
the delta-velocity minute archive and re-derives greeks on every read
(~0.3s/session, vectorised). That is a deliberate choice, not an omission:

> Do not "optimise" it by storing greeks in the snapshot. The collector computes
> at `q=0.012` while the archived IV is solved at `q=0`; mixing them shifts ATM
> theta by ~5%.

Read `analysis/theta_decay/features.py`'s module docstring before trusting
`capture_ratio` — burn rate is solid, decay capture is a session-scale statistic
behind a quality gate.

**Isolation rule:** `analysis/equity_report/` imports nothing from `broker/`,
`execution/`, or `risk/`, and runs its own daemon thread. A slow model call must
never be able to delay an order-placing tick. Keep new analysis modules on the
same side of that line.

## 2. The API routes

`api/main.py` is ~3,170 lines with ~160 inline `@app.get` / `@app.post`
declarations. There is **no `include_router`** — routes go directly in
`main.py`, grouped by prefix. Follow the neighbours (see the `/velocity/*` block
around line 2970 and `/decay/*` around line 3068).

## 3. Route naming — the collision trap

`api/ui_static.py` holds `_API_PREFIXES` and `is_api_path()` (`:62`). Any path
matching a prefix in that tuple is treated as an API path, so a **hard browser
load** of a SPA route with the same name returns a JSON 404 instead of the app.
Clicking through the sidebar still works (client-side routing), which is exactly
why this gets missed.

Three ways out, in order of preference:

1. **Give the SPA page a path that isn't an API prefix.** This is what the newer
   desks do: API `/decay/*` + page `/theta-decay`; API `/velocity/*` + page
   `/delta-velocity`. Neither `/decay` nor `/velocity` is in `_API_PREFIXES`,
   and no collision exists.
2. **Register the API side with a trailing slash** — `"/equity/"` and `"/cas/"`
   are in the tuple precisely so the SPA's `/equity-report` and
   `/cas-indicative` are not swallowed.
3. Accept sidebar-only reachability. `/gamma-density`, `/oi-var`, `/execution`
   and `/live` are in this state today.

Check `_API_PREFIXES` before you name anything.

## 4. The SPA page

TanStack Router, **file-based**. `vite.config.ts` runs
`tanstackRouter({ target: "react", autoCodeSplitting: true })` before the React
plugin, which generates `src/routeTree.gen.ts`. Do not hand-edit that file.

To add a page:

1. Create `src/routes/<page>.tsx` exporting
   `export const Route = createFileRoute("/<page>")({ component: … })`.
2. Add the sidebar entry in `src/components/AppSidebar.tsx` —
   `{ title: "…", url: "/<page>", icon: … }`.
3. Add the fetchers to `src/lib/api.ts` (the `api` object, ~line 134) and the
   response types to `src/lib/types.ts`.

The app is **client-rendered only**. No SSR, no server functions. Don't add
`createServerFn` or reintroduce `@tanstack/react-start` — data comes from
FastAPI via `src/lib/api.ts`. Document `<head>` and the theme boot script live
in `Pixel Perfect UI/index.html`, not `__root.tsx`.

**Do not add a Vite proxy entry for a UI page path.** `vite.config.ts` warns
about this explicitly: proxying e.g. `/vanna-exposure` to `:8001` steals the
Vite route and serves the built SPA shell instead. API calls resolve through
`VITE_API_BASE_URL` / `resolveApiBaseUrl()` in `src/lib/api.ts`.

## 5. Build both surfaces — this is the step people skip

Port 8080 is the Vite dev server (live source). Port 8001 is FastAPI serving a
**prebuilt** bundle from `Pixel Perfect UI/.output/public` (`api/ui_static.py`,
matching `build.outDir` in `vite.config.ts`).

A new route appears on 8080 immediately and on 8001 **only after
`npm run build`**. Verifying on 8080 alone proves nothing about the app as
normally opened.

> **Stop the API before building.** FastAPI mounts `.output/public/assets` via
> `StaticFiles`, holding a Windows directory lock. Vite then fails to clean it
> with `EBUSY: resource busy or locked, rmdir` and can leave a half-written
> bundle. `.output/` is gitignored — a clobbered build is **not** recoverable
> from git.

```bash
cd "C:\Dev\3ST\Pixel Perfect UI" && npm run build
```

`npm run build` is literally `vite build`, exits zero, and emits a normal
`index.html`. If you find docs claiming a non-zero exit is expected, or
referencing `scripts/build-desk.mjs`, `_shell.html`, `src/server.ts`, or
`src/start.ts` — that is the pre-2026-08-08 Lovable/nitro stack and it is gone.

---

## Definition of done for a desk change

- [ ] Backend module doesn't import `broker/`/`execution/`/`risk/` unless it
      genuinely places orders
- [ ] Route prefix checked against `_API_PREFIXES` in `api/ui_static.py`
- [ ] Sidebar entry added; `routeTree.gen.ts` regenerated by the plugin, not by hand
- [ ] API restarted (`--reload` is unreliable for `execution/` and
      `analysis/equity_report/`); `GET /health` confirms it took
- [ ] API **stopped**, then `npm run build`, then API restarted
- [ ] Page verified on **port 8001**, and a direct URL load tried, not just a
      sidebar click
- [ ] `pytest tests/` green

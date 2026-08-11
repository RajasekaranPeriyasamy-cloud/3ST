# UI build: exiting Lovable

**Status:** plan only — nothing implemented. Drafted 2026-08-08.

**Goal:** take the `Pixel Perfect UI` build off `@lovable.dev/vite-tanstack-config`
and onto a plain Vite SPA, so `npm run build` is a normal `vite build` that exits
zero and emits a real `index.html`.

**Decision:** Option B — drop the Lovable preset *and* `@tanstack/react-start`,
keep `@tanstack/react-router`. Rationale below.

---

## 1. What is actually coupled to Lovable

Full repo sweep (`grep -ril lovable`, excluding `node_modules`/`.output`/`.git`/`.venv`).
**13 real references across frontend, backend, and docs** — an earlier `src/`-only
scan undercounted this.

### Functional — the build breaks/changes if these move

| # | Where | What |
| --- | --- | --- |
| 1 | `Pixel Perfect UI/vite.config.ts` | the preset — the entire file is `defineConfig` from it |
| 2 | `Pixel Perfect UI/package.json:75` | `@lovable.dev/vite-tanstack-config` devDependency |
| 3 | `package-lock.json` / `bun.lock` | the preset **plus** `vite-plugin-dev-server-bridge`, `vite-plugin-hmr-gate`; `bun.lock` additionally records `lovable-tagger`, which drags in a **second, Tailwind 3 dependency tree** alongside your Tailwind 4 |
| 4 | `Pixel Perfect UI/bunfig.toml` | 6 `@lovable.dev/*` packages exempted from the 24h supply-chain guard (`minimumReleaseAgeExcludes`) |
| 5 | `src/lib/lovable-error-reporting.ts` | 36 lines, posts to `window.__lovableEvents` |
| 6 | `src/routes/__root.tsx:14,51` | the single call site |

Only 3 `@lovable.dev` packages are actually installed under `node_modules` (npm path);
`lovable-tagger` and its Tailwind 3 subtree exist in `bun.lock` but were not installed.

### Backend — cosmetic, but real

| # | Where | What |
| --- | --- | --- |
| 7 | `api/main.py:1` | docstring: "FastAPI backend for Lovable UI" |
| 8 | `api/main.py:226-227` | CORS allowlist: `https://*.lovable.app`, `https://*.lovable.dev` |

⚠️ Note while you're in there: `api/main.py:228` is `"*"` with `allow_credentials=True`
on line 230. The wildcard already grants everything the two Lovable entries grant, so
they are dead weight today — but the wildcard itself is worth a separate look. Flagged,
not in scope here.

### Docs — stale, no functional effect

| # | Where |
| --- | --- |
| 9 | `docs/LOVABLE_UI_SPEC.md` — the original "paste this into Lovable" build prompt |
| 10 | `README.md:5,36-40` — "Lovable UI" section |
| 11 | `docs/KITE_SETUP.md:39-44` — §5 "Lovable UI" |
| 12 | `Pixel Perfect UI/AGENTS.md` — `<!-- LOVABLE:BEGIN/END -->` force-push warning |
| 13 | `CLAUDE.md` — the `build-desk.mjs` rationale section |

README and KITE_SETUP are **already wrong independent of Lovable** — both tell you to set
`VITE_API_BASE_URL=http://127.0.0.1:8000` (the API is on 8001) and to place the UI under
`web/` (it lives in `Pixel Perfect UI/`).

### False positives — do not touch

`data/kite_instruments.json` and `_review/openalgo/test/*.csv` match on `LOVABLE`, the
NSE ticker for Lovable Lingerie Ltd. Instrument data, unrelated.
`Pixel Perfect UI.zip` (162K, repo root) is untracked local cruft, not in git.

### Everything else is yours

The other **121 files / ~32k lines** under `src/` are plain React 19 + Tailwind 4 +
shadcn/Radix + TanStack Router + Highcharts/Plotly. Nothing in them imports anything
Lovable-specific.

**No server functions exist.** `grep` for `createServerFn|useServerFn|serverOnly|createIsomorphicFn`
across `src/` matches only `start.ts`, `server.ts`, and a `declare module` line in
the generated `routeTree.gen.ts`. TanStack **Start** is present solely to satisfy
the preset's pipeline and to host an SSR error wrapper that production never runs
— SPA mode is already on (`vite.config.ts:14`), and FastAPI serves a static shell.

This is why Option B is safe: Start is load-bearing for nothing.

## 2. Why the preset is the thing to remove

The preset routes the server build through **nitro**. `@tanstack/start-plugin-core`'s
prerender step imports `<serverOutputDir>/server.js` and calls `.default.fetch()`;
nitro writes `.output/server/index.mjs` instead. Prerender 500s and emits no shell.

Everything downstream of that is workaround:

- `scripts/build-desk.mjs` (176 lines) — boots the nitro server on port 3199, fetches
  `/`, scrapes out stylesheets + the `$tsr` bootstrap + entry module, writes that as
  the shell.
- The `hydrateRoot` → `createRoot` string-patch applied to an already-built bundle
  (`build-desk.mjs:115`).
- `vite build` exiting non-zero being documented as expected.
- `scripts/write-spa-shell.mjs` as a fallback that "current TanStack versions will
  not boot from" (its own docstring).

A plain Vite SPA build has no prerender step, no nitro, and no shell to reconstruct —
`index.html` is an input, not an output. All four items above delete.

## 3. Target end state

```
vite.config.ts     hand-written: react + tailwind + tsconfigPaths + tanstackRouter
index.html         real SPA entry at project root (fonts, favicon, #root, module script)
src/main.tsx       new: createRoot + RouterProvider
package.json       "build": "vite build"
```

Removed dependencies: `@lovable.dev/vite-tanstack-config`, `@tanstack/react-start`, `nitro`.
Retained: `@tanstack/react-router`, `@tanstack/router-plugin` (still generates `routeTree.gen.ts`).

## 4. File-by-file changes

### Add

**`index.html`** (project root) — carries what `__root.tsx`'s `head:` block currently
emits: charset/viewport, `<title>`, description + og/twitter meta, the DM Sans +
JetBrains Mono Google Fonts `<link>`, favicon, `<div id="root">`, and
`<script type="module" src="/src/main.tsx">`. Keep `class="dark"` on `<html>` —
`build-desk.mjs:95` sets it today and the theme depends on it.

**`src/main.tsx`** — `createRoot(...).render(<RouterProvider router={getRouter()} />)`,
plus `import "./styles.css"`.

**`vite.config.ts`** (rewrite) — the four plugins the preset was bundling, minus nitro:
`@vitejs/plugin-react`, `@tailwindcss/vite`, `vite-tsconfig-paths`,
`@tanstack/router-plugin/vite` (`target: "react"`, `autoCodeSplitting: true`).
Plus explicitly, since the preset was supplying them:

- `server: { host: "127.0.0.1", port: 8080, strictPort: true }` — matches the current
  `dev` script and the CORS allowlist in `api/main.py:219`.
- `resolve.alias` `@` → `./src` (also in `tsconfig.json`, but the preset set it in Vite too).
- `build.outDir: ".output/public"` + `emptyOutDir: true` — **keeps `api/ui_static.py`
  untouched.** Default `dist` would require a backend edit; not worth it.
- React/TanStack dedupe — the preset did this; carry it over to avoid a duplicate-React
  hooks error.

### Modify

**`src/routes/__root.tsx`** — the only route file that changes.
- Drop `shellComponent: RootShell` and the `RootShell` function (lines 118–130).
- Drop the `HeadContent` / `Scripts` imports; static meta now lives in `index.html`.
- Drop `import appCss from "../styles.css?url"` and its `links` entry — `main.tsx`
  imports the CSS directly.
- Drop `reportLovableError` import + the `useEffect` at line 50. Replace with a plain
  `console.error` or leave the boundary silent; there is no Lovable listener to receive it.
- The `head:` block can go entirely, **or** be kept for per-route dynamic titles by
  rendering `<HeadContent />` inside `RootComponent`. `HeadContent` is exported from
  `@tanstack/react-router` and works client-side. Recommend deleting for now and adding
  back only if per-route titles are wanted.

**`package.json`** — `"build": "vite build"`; remove the three dependencies above.

**`Pixel Perfect UI/bunfig.toml`** — drop all 6 `@lovable.dev/*` entries from
`minimumReleaseAgeExcludes`. Leave `minimumReleaseAge = 86400` alone; that guard is
worth keeping. This is the one file where a leftover entry is a genuine (if small)
supply-chain concern rather than cosmetic.

**`api/main.py`** — remove `https://*.lovable.app` / `https://*.lovable.dev` from the
CORS allowlist (lines 226-227) and reword the line 1 docstring. No behaviour change
while line 228's `"*"` remains.

**`AGENTS.md`** — remove the `LOVABLE:BEGIN/END` block, or replace it with a note that
the project is no longer Lovable-connected. The force-push warning stops applying.

**`README.md`, `docs/KITE_SETUP.md`** — replace the "Lovable UI" sections with the real
workflow (`cd "Pixel Perfect UI" && npm run dev`, API on **8001** not 8000, no `web/`
folder). Fixes pre-existing staleness at the same time.

**`docs/LOVABLE_UI_SPEC.md`** — keep as a historical record of the original screen spec,
with a header noting it is superseded; or delete. Recommend keeping — it documents intent
for 29 routes. Do not leave it presenting itself as current instructions.

**`CLAUDE.md`** — the "Why `scripts/build-desk.mjs` is not just `vite build`" section
(~15 lines) and the `.output/` / prerender warnings become obsolete. Replace with a
short "plain Vite SPA, `outDir` is `.output/public`" note. **Keep** the "stop the API
before `npm run build`" warning — `StaticFiles` still holds a Windows directory lock on
`.output/public/assets`, and `emptyOutDir` will still hit `EBUSY`.

### Delete

| File | Why |
| --- | --- |
| `src/start.ts` | Start-only SSR middleware |
| `src/server.ts` | Start-only SSR entry |
| `src/lib/error-page.ts` | imported only by the two above (verified) |
| `src/lib/error-capture.ts` | imported only by `server.ts` (verified) |
| `src/lib/lovable-error-reporting.ts` | no listener after the exit |
| `scripts/build-desk.mjs` | replaced by `vite build` |
| `scripts/write-spa-shell.mjs` | its only caller was `build-desk.mjs` |

## 5. The three surfaces

### Vite dev, `:8080`
Least affected — it already runs client-side. After the change it serves `index.html`
directly instead of Start's dev middleware. `.env.development` (`VITE_API_BASE_URL=http://127.0.0.1:8001`)
is unchanged, and 8080 is already in the CORS allowlist. HMR behaviour should be
equivalent or better.

### FastAPI, `:8001`
`build.outDir: ".output/public"` means `api/ui_static.py` needs **no change** — it still
finds `index.html` and `assets/`. Two notes:

- `_shell.html` disappears. `ui_static.py:116` already falls back to `index_path` when
  `_shell.html` is absent, so this is handled. Optionally simplify that later; not required.
- **The API-prefix collision is unchanged and still applies.** `/execution`, `/gamma-density`,
  `/oi-var`, `/live` etc. are API prefixes (`ui_static.py:16-51`), so a hard browser load
  of those still returns JSON 404. This migration does not fix that and does not make it
  worse — same rule for new SPA routes.
- `.env.production` (`VITE_API_BASE_URL=` empty → same-origin) stays correct.

### Hosted static host — **read this before assuming it works**
`vite build` output is plain static files, so publishing to Cloudflare Pages / Netlify /
nginx is trivial *as a hosting problem*. The real blocker is the backend:

> **A hosted UI cannot reach a FastAPI bound to `127.0.0.1:8001` on your machine.**

The build is only half of it. To actually use a hosted UI you need one of:

1. **Cloudflare Tunnel** (or similar) exposing the API at a stable hostname, with that
   hostname baked into `VITE_API_BASE_URL` at build time, and the origin added to the
   CORS allowlist in `api/main.py:219`.
2. Bind uvicorn to a LAN/public interface — **not recommended.** This desk has no auth
   beyond Kite OAuth (per CLAUDE.md's security model), and it places real orders. Exposing
   it is a materially different security posture than the current single-user localhost
   assumption.

Option 1 is the only one worth planning. It is out of scope for the build migration
itself but must be decided before "hosted" counts as delivered. There is also a
WebSocket implication: `getWsUrl()` (`src/lib/api.ts:26`) derives `ws://`/`wss://` from
the same base URL, so the tunnel must forward WebSockets too.

**Recommendation:** treat FastAPI `:8001` + dev `:8080` as Phase 1 (self-contained,
frontend-only), and hosted as Phase 2 with the tunnel + CORS + auth question answered
separately.

## 6. Sequence

1. Branch off `fix/tanstack-start-build`.
2. Add `index.html`, `src/main.tsx`, rewrite `vite.config.ts`. Do not delete anything yet.
3. Edit `__root.tsx`. `npm run dev` — verify all 29 routes render on `:8080`, sidebar nav
   works, one Highcharts page and one Plotly page draw, the API health banner is green.
4. Stop the API. `npm run build` — must exit **zero** and produce `.output/public/index.html`
   plus `assets/`. Restart the API, load `:8001`, click through the sidebar.
5. Only once both surfaces pass: delete the seven files in §4, drop the three dependencies,
   `npm install`, rebuild, re-verify.
6. Update `CLAUDE.md` and `AGENTS.md`.

Step 5 last is deliberate — `.output/` is gitignored, so a bad build is not recoverable
from git, and keeping the old script around until the new path is proven means a
one-line `package.json` revert gets you back.

## 7. Risks, and how each is contained

**The containment principle: this migration changes only how the app is *built*, never
what it *is*.** No route file, component, hook, context, API call, or backend module
changes behaviour. `src/routes/__root.tsx` is the sole component edited, and only to
remove an SSR shell that production already does not use. That is what keeps the core
concept intact — the risks below are all build-time and all fail loudly at step 3 or 4
of §6, before anything is deleted.

| Risk | Containment | Detected by |
| --- | --- | --- |
| A route secretly depends on Start | Already disproven — `grep` for `createServerFn\|useServerFn\|serverOnly\|createIsomorphicFn` matches only `start.ts`/`server.ts` and one generated `declare module` line. If one surfaces anyway, it can be rewritten as a normal `fetch` to FastAPI, which is what every other call already does. | Build error, immediately |
| Duplicate React / hooks error | The replacement `vite.config.ts` must carry the dedupe the preset supplied. Explicitly listed in §4. | Blank page + hooks error on `:8080`, step 3 |
| Tailwind 4 stops finding classes | `styles.css` uses `@import "tailwindcss" source(none)` + `@source "../src"` — plugin-agnostic, independent of who registers the Vite plugin. | Visibly unstyled UI, step 3 |
| `routeTree.gen.ts` differs | It is generated and gitignored-in-spirit. If the router plugin emits something different, delete and regenerate — never hand-edit. | Route resolution failure, step 3 |
| Build output lands in the wrong place | `build.outDir: ".output/public"` is pinned precisely so `api/ui_static.py` needs zero edits. | `:8001` 404s, step 4 |
| A page renders on `:8080` but not `:8001` | This is the historical failure mode in this project, which is exactly why step 4 is a separate gate and step 5 (deletion) comes after it. | Step 4 |
| Bad build is unrecoverable | `.output/` is gitignored. Mitigated by keeping `build-desk.mjs` on disk until step 5 — a one-line `package.json` revert restores the old path. | — |
| Losing Lovable editor sync | **Intended, and irreversible in practice.** Confirm the Lovable project is being abandoned, not paused, before step 6. | — |
| Fonts fail | The Google Fonts `<link>` moves from `__root.tsx` to `index.html` verbatim. Identical behaviour. | Visual, step 3 |

### What is explicitly NOT touched

Stated so scope creep is visible if it happens:

- `broker/`, `execution/`, `risk/` — untouched. No order-placement path is involved.
- Any `api/` route handler. The only backend edit is 3 lines in `api/main.py` (docstring +
  2 CORS entries), and it is **optional** — the app works identically with them left in.
- `api/ui_static.py` — untouched, by design (see `outDir` above).
- All 29 route files except `__root.tsx`. All 100+ components. All hooks, contexts, `lib/api.ts`.
- The API contract, `VITE_API_BASE_URL` semantics, and `getWsUrl()` behaviour.

### Rollback

Before step 5: revert `package.json`'s `build` script, `npm run build`, done.
After step 5: `git revert` the branch — every deleted file is in git history. `.output/`
is the only unrecoverable artifact, and it is rebuildable from either path.

## 8. Verifying "zero Lovable dependency"

The exit is complete when this returns **only** the known false positives —
`data/kite_instruments.json` and `_review/openalgo/test/*.csv` (the NSE ticker), plus
this plan document:

```bash
grep -ril lovable . --exclude-dir=node_modules --exclude-dir=.output --exclude-dir=.git --exclude-dir=.venv
```

And this returns nothing:

```bash
ls "Pixel Perfect UI/node_modules/@lovable.dev" 2>/dev/null; grep -c lovable "Pixel Perfect UI/package-lock.json"
```

Note `package-lock.json` / `bun.lock` only clear after a fresh `npm install` (or `bun install`)
following the `package.json` edit — removing the dependency line alone leaves the lock entries
behind. Both lockfiles are present; whichever you actually use, the other should be regenerated
or deleted so they cannot disagree.

## 9. UI/UX workflow after the exit

How UI work gets done once Lovable is gone. Nothing here needs new tooling.

**Small changes** (copy, spacing, a column, a chart tweak): edited directly in
`src/`, verified on `:8080`, then built and re-checked on `:8001`.

**New pages or redesigns** — two-stage, because reviewing a design is faster than
reviewing a diff:

1. **Mockup as a Claude Artifact.** A standalone HTML page, styled to match the desk's
   existing tokens, that you look at and react to before any route code is written.
   Cheap to throw away and redo.
2. **Port the approved design into the real route.** Real components, real `lib/api.ts`
   calls, real state.

**Artifacts are a design surface, not a runtime.** They are single-file pages hosted on
claude.ai under a strict CSP: they cannot reach `127.0.0.1:8001`, cannot be served from
`.output/public`, and cannot be a 29-route app. They never replace the desk — they only
shorten the loop before committing to code.

**Design consistency** is maintained by the existing system, not by taste: Tailwind 4
tokens in `src/styles.css` (`@theme inline`), the shadcn/Radix primitives in
`src/components/ui/`, and DM Sans / JetBrains Mono. New work reuses these rather than
introducing parallel styling.

**One constraint to remember when adding routes:** the path must not collide with an API
prefix (`api/ui_static.py:16-51`), or direct browser loads return JSON 404 — see §5.
This predates the migration and survives it.

## 10. Not verified

- Whether every one of the 29 routes currently renders correctly on `:8001` **today** —
  the plan assumes current behaviour is the baseline to preserve, not that it is bug-free.
- Runtime behaviour of the new config. This document is static analysis only; nothing was
  built or run.
- Whether `@tanstack/router-plugin`'s `autoCodeSplitting` produces the same chunk layout
  the preset did. Cosmetic unless a chunk-size regression matters.

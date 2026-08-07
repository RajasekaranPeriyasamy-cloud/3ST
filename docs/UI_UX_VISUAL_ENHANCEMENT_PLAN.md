# UI/UX Visual Enhancement Plan — Pixel Perfect UI

Frontend-only. Nothing in this plan touches `api/`, `options/`, `execution/`, `broker/`, `risk/`,
`strategy_3st*.py`, or `backtest_engine.py`, and no phase changes a request/response contract any
hook already relies on — only how already-fetched data is rendered. Each phase is a short-lived
branch (see the git workflow now in place), independently mergeable and independently revertable.

## 1. Current state — what's actually there

Verified by reading the code, not assumed:

- **Design tokens are already solid.** `styles.css` defines a full OKLCH-based token set (background,
  card, primary, bull/bear/warn, chart-1..5, sidebar) for both a light "bright trading atrium" theme
  and a `.dark` variant, plus a dark-mode boot script that avoids a flash on load. `DM Sans` (UI) and
  `JetBrains Mono` (numbers) are registered as the font stack. This is a good foundation — the plan
  below extends it, it doesn't replace it.
- **Component foundation is modern**: shadcn/ui + Radix primitives + Tailwind v4, TanStack Router,
  React 19. No framework changes needed for any of this.
- **Three charting libraries are in simultaneous use**: Highcharts (`StraddleWatchChart`), Plotly
  (`GexSessionPlotly`, `GexStrikePlotly`, `OiMoversSessionPlotly`), and Recharts (RRG, vol surface,
  and others). Confirmed in `Pixel Perfect UI/src/components/charts/sessionChartTheme.ts`: the Plotly
  session charts are hardcoded to a fixed white background, `Segoe UI` font, and slate hex borders,
  with a comment stating they **intentionally stay light even when the app shell is `.dark`**. That's
  the single biggest visual inconsistency in the product today — the chrome around a chart uses the
  OKLCH/dark-mode system, the chart itself does not.
- **`WidgetShell.tsx`** (used only in Widget Desk mode) is genuinely polished: consistent card
  treatment, loading spinner, auth/error states, "Full view" / "Open" affordances. The ~22 full-page
  desk routes each build their own header/spacing by hand and don't share this shell, so navigating
  desk-to-desk doesn't feel like one continuous product the way Widget Desk mode does.
- **Sidebar** (`AppSidebar.tsx`) is a single flat list of 22 items under one "Trading" label, even
  though they cluster naturally (selection/backtest, options analytics, execution/live, utility).
  No visual grouping today.
- **Good instincts already present, worth extending rather than replacing**: the `breach-cell-up` /
  `breach-cell-down` / `breach-pill-*` utilities in `styles.css` (glowing bull/bear highlight on
  breach), the radial-gradient "desk glow" background, and the recent `ThemeToggle`/`useTheme` work.

## 2. Principles for this pass

- **Data density with clarity, not decoration.** This is a real-money trading desk; every visual
  change should make numbers easier to scan at a glance, not add visual noise.
- **One chart language.** Highcharts, Plotly, and Recharts should all read color/font/grid/tooltip
  styling from the same theme source, so a chart never looks like it wandered in from another app.
- **Motion with purpose.** Live-updating values (LTP, GEX, OI) get a brief, deliberate highlight on
  change — not decorative animation.
- **One desk shell.** Every full-page route gets the header/toolbar/loading/error treatment already
  prototyped in `WidgetShell`, so switching desks feels like switching tabs, not switching apps.

## 3. Phased plan

Each phase ships as its own branch off `main`, gets `pytest`/`eslint` run (frontend changes won't
touch anything `pytest` covers, but running it costs nothing and confirms the boundary held), and
merges independently.

### Phase 0 — Baseline (half a session)
- Screenshot all 22 routes, light + dark, as a before/after reference.
- Written scope boundary for this work: edits confined to `Pixel Perfect UI/src/**`; no hook's API
  call signature or response parsing changes, only its render output.

### Phase 1 — Shared chart theme (highest leverage; fixes the biggest inconsistency)
- Generalize `sessionChartTheme.ts` from a single hardcoded light palette into a theme object that
  reads the same CSS custom properties as the rest of the app (`--card`, `--border`, `--chart-1..5`,
  `--bull`, `--bear`, `--font-sans`, `--font-mono`), with a light and dark variant matching `.dark`.
- Wire it into all three libraries: Plotly configs (`GexSessionPlotly`, `GexStrikePlotly`,
  `OiMoversSessionPlotly`), Highcharts theme options (`StraddleWatchChart`), and Recharts
  `CartesianGrid`/`Tooltip` styling (RRG, vol surface, others).
- Outcome: every chart matches the shell it sits in, in both themes, for the first time.

### Phase 2 — Sidebar grouping (visual only, no routing changes)
- Group the existing 22 items into labeled `SidebarGroup`s (Selection & Backtest · Options Analytics
  · Execution & Live · Utility) using primitives already imported in `AppSidebar.tsx` — no new
  dependency, no route changes.
- Strengthen the active-item treatment (already has a background tint) with a left accent bar so the
  current desk is unmistakable at a glance, especially once there are 4-5 grouped sections instead of
  one long list.

### Phase 3 — Unified desk shell for full-page routes
- Extract a `DeskPageShell` from `WidgetShell`'s pattern (title, live/updated-at indicator, existing
  `ReportPageDownload` + `ThemeToggle` controls, consistent padding) and adopt it across the desk
  routes as a wrapper — purely presentational, the existing data-fetching hooks underneath are
  untouched.
- Standardize loading/empty/error states across desks (skeleton shimmer via the already-installed
  `tw-animate-css` instead of each route's own spinner/text treatment).
- This phase touches the most files (~20 route components) but each edit is mechanical (wrap existing
  JSX in the new shell) — lowest risk-per-file of any phase, just the largest in file count.

### Phase 4 — Data typography & live-value motion
- Small `<Num>` / `<Price>` presentational component enforcing `tabular-nums` + `JetBrains Mono` on
  every price/greek/OI value, so columns of numbers align instead of each route formatting ad hoc.
- Brief highlight-flash (CSS class toggled via a `usePrevious`-style hook) when a polled value changes
  tick-to-tick — reinforces the "live desk" feel that `useWidgetPoll` already provides data for but
  nothing currently visualizes.

### Phase 5 — Detail pass
- Consistent spacing/shadow/border rhythm across desks once Phase 3's shell is in place everywhere.
- Contrast check: a couple of the `oklch` bull/bear/warn values sit close in lightness — verify
  WCAG AA when used as small badge text, not just as backgrounds.
- Icon audit: a few `lucide-react` choices in `AppSidebar.tsx` are loose metaphors (e.g. `Gavel` for
  CAS Indicative) — low priority, but cheap to tighten once Phase 2's grouping is settled.

## 4. Guardrails checklist (apply to every phase before merging)

- No diff outside `Pixel Perfect UI/src/**` (plus this doc).
- No hook's request URL, params, or response-parsing logic changed — render-layer only.
- No new runtime dependency added without a specific reason (three chart libraries already cover
  every need here; this plan is about a shared theme layer across them, not a library swap — a full
  consolidation onto one charting library would be a much larger, separate decision and isn't
  recommended as part of a "don't disturb the core" pass).
- One branch per phase, `pytest tests/` run before merge (cheap confirmation nothing backend-shaped
  was touched), pushed to GitHub same day it's started — per the commit-per-session habit.

## 5. Suggested order & rough effort

| Phase | Focus | Est. sessions |
|---|---|---|
| 0 | Baseline screenshots + scope doc | 0.5 |
| 1 | Shared chart theme (Plotly/Highcharts/Recharts) | 1–2 |
| 2 | Sidebar grouping | 0.5 |
| 3 | Unified desk shell across ~22 routes | 2–3 |
| 4 | Number typography + live-value flash | 1 |
| 5 | Spacing/contrast/icon detail pass | 1 |

Start with Phase 1 — it's the highest-visibility fix (charts currently ignore dark mode entirely) for
the least amount of code touched.

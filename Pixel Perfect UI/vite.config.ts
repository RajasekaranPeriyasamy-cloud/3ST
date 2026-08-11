// Plain Vite SPA. Replaces @lovable.dev/vite-tanstack-config, which bundled these
// same plugins plus nitro — and whose nitro server build broke TanStack Start's
// prerender step, forcing the scripts/build-desk.mjs shell workaround.
// See docs/UI_LOVABLE_EXIT_PLAN.md.
//
// This app is client-rendered only: no SSR, no server functions. FastAPI serves the
// built output as static files (api/ui_static.py).
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    // Must precede the react plugin — it generates routeTree.gen.ts and rewrites
    // route files for code splitting before React's transform runs.
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],

  resolve: {
    // Vite 8 resolves tsconfig `paths` natively; the vite-tsconfig-paths plugin
    // the preset bundled is no longer needed (Vite warns about it at startup).
    tsconfigPaths: true,
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    // The preset deduped these. Without it a second copy of React can be pulled in
    // through a transitive dep, which surfaces as "invalid hook call" at runtime.
    dedupe: ["react", "react-dom", "@tanstack/react-router", "@tanstack/react-query"],
  },

  server: {
    host: "127.0.0.1",
    port: 8080,
    strictPort: true,
  },

  build: {
    // api/ui_static.py reads .output/public — keeping that path means the backend
    // needs no change. Vite's default (dist) would.
    outDir: ".output/public",
    emptyOutDir: true,
  },

  // Do NOT proxy UI page paths like /vanna-exposure to :8001 — that steals the
  // Vite route and returns the built SPA shell (HTML). API calls use
  // VITE_API_BASE_URL / resolveApiBaseUrl() in src/lib/api.ts instead.
});

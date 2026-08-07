#!/usr/bin/env node
/** Write index.html / _shell.html for FastAPI static hosting after vite build. */
import { readFileSync, readdirSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const publicDir = join(process.cwd(), ".output", "public");
const assetsDir = join(publicDir, "assets");

if (!existsSync(assetsDir)) {
  console.error("Missing .output/public/assets — run npm run build first.");
  process.exit(1);
}

const files = readdirSync(assetsDir);
const js = files.find((f) => f.startsWith("index-") && f.endsWith(".js"));
const css = files.find((f) => f.startsWith("styles-") && f.endsWith(".css"));

if (!js) {
  console.error("No index-*.js found in .output/public/assets");
  process.exit(1);
}

// Prerender often fails locally; TanStack Start then ships hydrateRoot(document).
// Without SSR HTML that hydration mismatches and the UI stays blank — use createRoot.
const jsPath = join(assetsDir, js);
let bundle = readFileSync(jsPath, "utf8");
if (bundle.includes("hydrateRoot")) {
  bundle = bundle.replace(/\.hydrateRoot\b/g, ".createRoot");
  writeFileSync(jsPath, bundle, "utf8");
  console.log(`Patched ${js} → createRoot (client-only static hosting)`);
}

// TanStack Start mounts on document (not #root). Keep body empty for client shell.
const html = `<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>3ST Algo Desk — Kite Control Panel</title>
  <meta name="description" content="Algo trading control panel for Zerodha Kite Connect." />
  ${css ? `<link rel="stylesheet" href="/assets/${css}" />` : ""}
</head>
<body>
  <script type="module" src="/assets/${js}"></script>
</body>
</html>
`;

for (const name of ["index.html", "_shell.html"]) {
  writeFileSync(join(publicDir, name), html, "utf8");
}

console.log(`Wrote SPA shell → .output/public/index.html (js=${js})`);

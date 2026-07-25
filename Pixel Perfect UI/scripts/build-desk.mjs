#!/usr/bin/env node
/** Build UI client assets and write SPA shell (prerender may fail; assets still land in .output/public). */
import { spawnSync } from "node:child_process";

const vite = spawnSync("npx", ["vite", "build"], {
  stdio: "inherit",
  shell: true,
  cwd: process.cwd(),
});

if (vite.status !== 0) {
  console.warn("[build-desk] vite build exited non-zero (prerender may have failed); writing SPA shell anyway.");
}

const shell = spawnSync("node", ["scripts/write-spa-shell.mjs"], {
  stdio: "inherit",
  shell: true,
  cwd: process.cwd(),
});

process.exit(shell.status ?? 1);

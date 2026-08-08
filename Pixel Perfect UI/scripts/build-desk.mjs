#!/usr/bin/env node
/**
 * Build the static desk bundle that FastAPI serves from `.output/public`
 * (see api/ui_static.py). Not the Cloudflare/Lovable deploy path.
 *
 * TanStack Start's own prerender step cannot run in this project: its
 * preview-server plugin imports `<serverOutputDir>/server.js` and calls
 * `.default.fetch()`, but the Lovable preset routes the server build through
 * nitro, which writes `.output/server/index.mjs` instead. Prerender therefore
 * 500s on every page and emits no shell.
 *
 * Without a real shell the client throws `Invariant failed` and the whole desk
 * renders blank, because the shell is what defines `self.$_TSR.router`. So we
 * produce the shell ourselves: build nitro with a Node-runnable preset, boot
 * that server once, fetch `/`, and keep only the parts a SPA shell needs —
 * stylesheets, the `$tsr` bootstrap, and the entry module. The rendered body is
 * dropped so the shell is route-agnostic (no flash of the landing page before
 * the client router resolves the real route).
 *
 * If any of that fails we fall back to scripts/write-spa-shell.mjs, which
 * writes a minimal shell — enough for the build to produce *something*, though
 * current TanStack versions will not boot from it.
 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const cwd = process.cwd();
const publicDir = join(cwd, ".output", "public");
const serverEntry = join(cwd, ".output", "server", "index.mjs");
const SHELL_PORT = process.env.DESK_SHELL_PORT || "3199";

function run(cmd, args, extraEnv = {}) {
  return spawnSync(cmd, args, {
    stdio: "inherit",
    shell: true,
    cwd,
    env: { ...process.env, ...extraEnv },
  });
}

// node-server so the nitro output can be booted here to render the shell. This
// script's artifact is the FastAPI-hosted static bundle; the deploy preset is
// unaffected because deploys do not go through it.
const vite = run("npx", ["vite", "build"], { NITRO_PRESET: "node-server" });
if (vite.status !== 0) {
  console.warn(
    "[build-desk] vite build exited non-zero (expected: prerender cannot boot the nitro server). Continuing.",
  );
}

/** Kill the shell server and everything it spawned, without touching libuv handles. */
function stopServer(child) {
  if (!child?.pid) return;
  try {
    if (process.platform === "win32") {
      spawnSync("taskkill", ["/pid", String(child.pid), "/f", "/t"], { stdio: "ignore" });
    } else {
      process.kill(-child.pid, "SIGTERM");
    }
  } catch {
    /* already gone */
  }
}

async function waitForServer(url, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(3000) });
      if (res.ok) return res;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return null;
}

/** Strip the rendered page down to a route-agnostic shell. */
function toShell(html) {
  const pick = (re) => [...html.matchAll(re)].map((m) => m[0]).join("\n  ");

  const stylesheets = pick(/<link[^>]+rel="stylesheet"[^>]*>/g);
  // The $tsr bootstrap defines self.$_TSR.router — the client invariants without it.
  const tsrScripts = pick(/<script[^>]*class="\$tsr"[^>]*>[\s\S]*?<\/script>/g);
  const entryScripts = pick(/<script[^>]*type="module"[^>]*src="[^"]*"[^>]*><\/script>/g);
  const title = (html.match(/<title>([\s\S]*?)<\/title>/) || [])[1] || "3ST Algo Desk";
  const description =
    (html.match(/<meta name="description" content="([^"]*)"/) || [])[1] || "";

  if (!tsrScripts || !entryScripts) return null;

  return `<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${title}</title>
  <meta name="description" content="${description}" />
  ${stylesheets}
</head>
<body>
  ${tsrScripts}
  ${entryScripts}
</body>
</html>
`;
}

/**
 * Prerender never runs, so TanStack ships hydrateRoot with no server HTML to
 * hydrate against. Swap it for createRoot or the app mounts to a blank page.
 */
function patchToCreateRoot() {
  const assetsDir = join(publicDir, "assets");
  const js = readdirSync(assetsDir).find((f) => f.startsWith("index-") && f.endsWith(".js"));
  if (!js) return false;
  const path = join(assetsDir, js);
  const bundle = readFileSync(path, "utf8");
  if (bundle.includes("hydrateRoot")) {
    writeFileSync(path, bundle.replace(/\.hydrateRoot\b/g, ".createRoot"), "utf8");
    console.log(`[build-desk] patched ${js} → createRoot`);
  }
  return true;
}

async function main() {
  if (!existsSync(serverEntry)) {
    console.warn("[build-desk] no nitro server entry; falling back to minimal shell.");
    return run("node", ["scripts/write-spa-shell.mjs"]).status ?? 1;
  }

  patchToCreateRoot();

  // Detached + unref'd: tearing this child down from inside the parent's event
  // loop trips a libuv assertion on Windows and fails the build with exit 9.
  const server = spawn("node", [serverEntry], {
    cwd,
    env: { ...process.env, PORT: SHELL_PORT },
    stdio: "ignore",
    detached: true,
  });
  server.unref();

  try {
    const res = await waitForServer(`http://127.0.0.1:${SHELL_PORT}/`);
    if (!res) {
      console.warn("[build-desk] SSR server did not start; falling back to minimal shell.");
      return run("node", ["scripts/write-spa-shell.mjs"]).status ?? 1;
    }

    const shell = toShell(await res.text());
    if (!shell) {
      console.warn("[build-desk] could not extract a shell; falling back to minimal shell.");
      return run("node", ["scripts/write-spa-shell.mjs"]).status ?? 1;
    }

    for (const name of ["index.html", "_shell.html"]) {
      writeFileSync(join(publicDir, name), shell, "utf8");
    }
    console.log(`[build-desk] wrote SPA shell from SSR render (${shell.length} bytes)`);
    return 0;
  } finally {
    stopServer(server);
  }
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error("[build-desk]", err);
    process.exit(1);
  },
);

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The product SPA is served by Flask out of `static/app/`, so every emitted URL
// has to be absolute under that prefix. A relative base would resolve against
// the current route -- and because the SPA uses history routing, `/pulse/a/b`
// would ask for `/pulse/a/assets/...` and 404 only on deep links, which is the
// kind of break that survives local testing.
const BASE = "/static/app/";

export default defineConfig({
  base: BASE,
  plugins: [react()],
  build: {
    outDir: "../static/app",
    // The output directory lives inside a tree Flask also serves by hand, so
    // emptying it is scoped to exactly what this build owns.
    emptyOutDir: true,
    // Committed to git, because Railway deploys from git and cannot build.
    // The freshness gate (`scripts/ops/web_build_freshness_gate.py`) is what
    // stops that from silently shipping yesterday's bundle.
    manifest: true,
    // Off, and this is a consequence of committing the output rather than a
    // general preference. The map for even this empty scaffold is ~1 MB, and
    // committed artifacts mean every build adds that to git history forever.
    // It also publishes full source for a product that sits behind a login
    // wall, and nothing consumes it today -- there is no error-reporting
    // service wired up to symbolicate against. Turn it back on together with
    // one, and prefer uploading the map to it over committing the map here.
    sourcemap: false,
    rollupOptions: {
      output: {
        // Content-hashed names are what make the assets safe to cache
        // immutably, and what make a stale artifact detectable at all.
        entryFileNames: "assets/[name].[hash].js",
        chunkFileNames: "assets/[name].[hash].js",
        assetFileNames: "assets/[name].[hash].[ext]",
      },
    },
  },
  server: {
    port: 5173,
    // Dev-only. The API is same-origin in production, so this proxy exists to
    // reproduce that from the Vite dev server rather than to introduce CORS --
    // the app has zero CORS configuration repo-wide and that is deliberate.
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: false },
    },
  },
});

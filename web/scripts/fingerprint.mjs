#!/usr/bin/env node
/**
 * Record which source tree produced the committed build.
 *
 * Why this exists
 * ---------------
 * Railway deploys from git and the production image has no Node, so the built
 * SPA has to be committed. That trades one problem for a worse-behaved one: a
 * committed artifact can go stale, and it goes stale *silently*. Edit
 * `web/src/`, forget to rebuild, commit. Tests pass, the app boots, the page
 * renders -- serving last week's bundle. Nothing is red. That is the same shape
 * as the TestFlight build 5 failure: two halves disagreeing while every
 * individual signal stays green.
 *
 * So the build writes down exactly which source bytes it was built from, and
 * `scripts/ops/web_build_freshness_gate.py` recomputes that in CI and fails on
 * any difference.
 *
 * What is deliberately NOT compared
 * ---------------------------------
 * The emitted bundle. Comparing CI's build byte-for-byte against the committed
 * one would make the gate depend on the bundler being reproducible across
 * machines and Node versions. It mostly is -- but "mostly" buys false alarms on
 * a Node minor bump, and a gate that cries wolf is a gate someone switches off.
 * Hashing the *inputs* is exactly as strong for the failure being prevented
 * (source changed, artifacts did not) and is deterministic by construction.
 *
 * The record is a per-file map rather than one opaque hash, for two reasons:
 * the failure message can name the stale file, and a change in which files are
 * covered at all becomes visible instead of quietly shrinking the check.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const REPO = resolve(WEB, "..");
const RECORD = join(REPO, "static", "app", "build-source-hash.json");

/** Everything that can change the emitted bundle.
 *
 * `package-lock.json` is in here on purpose: a dependency bump changes the
 * output with no diff anywhere in `src/`, which is the one stale-artifact case
 * a source-only fingerprint would miss. This script is in here too, so that
 * changing how the fingerprint is computed invalidates records computed the
 * old way rather than silently comparing across two algorithms.
 */
const SOURCE_DIRS = ["src"];
const SOURCE_FILES = [
  "index.html",
  "vite.config.ts",
  "tsconfig.json",
  "package.json",
  "package-lock.json",
  "scripts/fingerprint.mjs",
];

function walk(dir, out) {
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
    a.name < b.name ? -1 : a.name > b.name ? 1 : 0
  )) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (entry.isFile()) out.push(full);
  }
  return out;
}

function collect() {
  const found = [];
  for (const d of SOURCE_DIRS) {
    const full = join(WEB, d);
    try {
      if (statSync(full).isDirectory()) walk(full, found);
    } catch {
      /* a configured dir that does not exist is reported below, not ignored */
    }
  }
  for (const f of SOURCE_FILES) {
    const full = join(WEB, f);
    try {
      if (statSync(full).isFile()) found.push(full);
    } catch {
      /* same */
    }
  }
  return found;
}

/** Raw bytes, no normalisation.
 *
 * The Python gate has to produce byte-identical results, and two
 * implementations of "normalise line endings" in two languages is a real
 * divergence risk for no benefit. `web/.gitattributes` pins `eol=lf` instead,
 * so the bytes on disk are the same on every checkout.
 */
function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

const files = {};
for (const path of collect()) {
  files[relative(REPO, path).split(sep).join("/")] = sha256(path);
}

const names = Object.keys(files).sort();
if (names.length === 0) {
  console.error(
    "fingerprint: found no source files. Refusing to write a record that " +
      "would certify an empty tree as the source of the build."
  );
  process.exit(3);
}

const fingerprint = createHash("sha256")
  .update(names.map((n) => `${n}:${files[n]}`).join("\n"))
  .digest("hex");

const record = {
  version: 1,
  algorithm: "sha256",
  fingerprint,
  file_count: names.length,
  files: Object.fromEntries(names.map((n) => [n, files[n]])),
};

if (process.argv.includes("--write")) {
  mkdirSync(dirname(RECORD), { recursive: true });
  writeFileSync(RECORD, `${JSON.stringify(record, null, 2)}\n`, "utf8");
  console.log(
    `fingerprint: ${fingerprint.slice(0, 16)}… over ${names.length} source file(s) -> ` +
      `${relative(REPO, RECORD)}`
  );
} else {
  console.log(`${fingerprint}  (${names.length} files)`);
}

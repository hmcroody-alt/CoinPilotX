#!/usr/bin/env node
/**
 * Merge staged English keys into the English catalogs.
 *
 * Screen-migration work stages its new keys as namespace-shaped JSON under
 * `scripts/.i18n-staging/` rather than editing the catalogs directly, so that
 * several migrations can run at once without racing on the same two files.
 * This script folds those staged files into `catalogs/en/core.json` and
 * `catalogs/en/extended.json`, routing each namespace to its tier.
 *
 * It refuses to overwrite an existing leaf. A collision means two migrations
 * chose the same key for different copy, and silently letting the last writer
 * win would change shipped English somewhere else in the app.
 *
 * Usage:  node scripts/merge-i18n-staging.mjs [--dry-run]
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const STAGING = path.join(HERE, ".i18n-staging");
const CATALOGS = path.join(ROOT, "src", "i18n", "catalogs", "en");

const TIER_OF = {
  common: "core",
  auth: "core",
  errors: "core",
  social: "extended",
  messaging: "extended",
  commerce: "extended",
  discovery: "extended",
  settings: "extended"
};

const dryRun = process.argv.includes("--dry-run");

const isBranch = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

/** Every leaf path in an object, as dotted strings. */
function leaves(node, prefix = [], out = []) {
  for (const [key, value] of Object.entries(node)) {
    if (isBranch(value)) leaves(value, [...prefix, key], out);
    else out.push([[...prefix, key].join("."), value]);
  }
  return out;
}

/** Merge `source` into `target`, collecting collisions rather than clobbering. */
function merge(target, source, prefix, collisions) {
  for (const [key, value] of Object.entries(source)) {
    const here = [...prefix, key];
    if (isBranch(value)) {
      if (target[key] !== undefined && !isBranch(target[key])) {
        collisions.push(here.join("."));
        continue;
      }
      target[key] = target[key] ?? {};
      merge(target[key], value, here, collisions);
      continue;
    }
    if (target[key] !== undefined) {
      if (target[key] !== value) collisions.push(here.join("."));
      continue;
    }
    target[key] = value;
  }
}

/*
 * Key order is left exactly as found: existing keys keep their positions and
 * new ones are appended where they were merged. Sorting the whole file would
 * be tidier in the abstract but would rewrite every line of a catalog that
 * ten other locales are mirrored against, burying the actual change.
 */

const files = fs
  .readdirSync(STAGING)
  .filter((name) => name.endsWith(".json") && !name.startsWith("core.en"));

if (files.length === 0) {
  console.log("nothing staged.");
  process.exit(0);
}

const catalogs = {
  core: JSON.parse(fs.readFileSync(path.join(CATALOGS, "core.json"), "utf8")),
  extended: JSON.parse(fs.readFileSync(path.join(CATALOGS, "extended.json"), "utf8"))
};

const collisions = [];
const added = { core: 0, extended: 0 };

for (const name of files) {
  const staged = JSON.parse(fs.readFileSync(path.join(STAGING, name), "utf8"));
  for (const [namespace, tree] of Object.entries(staged)) {
    if (namespace.startsWith("$")) continue;
    const tier = TIER_OF[namespace];
    if (!tier) {
      console.error(`  ${name}: unknown namespace "${namespace}"`);
      process.exitCode = 1;
      continue;
    }
    const before = leaves(catalogs[tier]).length;
    catalogs[tier][namespace] = catalogs[tier][namespace] ?? {};
    merge(catalogs[tier][namespace], tree, [namespace], collisions);
    added[tier] += leaves(catalogs[tier]).length - before;
  }
  console.log(`  read ${name} (${leaves(staged).length} leaves)`);
}

if (collisions.length) {
  console.error(`\n  ${collisions.length} collision(s) — refusing to write:`);
  collisions.slice(0, 40).forEach((key) => console.error(`    ${key}`));
  process.exitCode = 1;
} else if (dryRun) {
  console.log(`\n  dry run — would add ${added.core} core and ${added.extended} extended leaves.`);
} else {
  for (const tier of ["core", "extended"]) {
    const file = path.join(CATALOGS, `${tier}.json`);
    fs.writeFileSync(file, `${JSON.stringify(catalogs[tier], null, 2)}\n`);
  }
  console.log(`\n  merged — ${added.core} new core leaves, ${added.extended} new extended leaves.`);
}

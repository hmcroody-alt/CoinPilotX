#!/usr/bin/env node
/**
 * Fold a completed translation worklist back into a locale's catalogs.
 *
 * Reads `scripts/.i18n-staging/done/<locale>.json` (same shape as the todo file
 * produced by `i18n-todo.mjs`) and writes each namespace into that locale's
 * `core.json` or `extended.json` according to the tier map.
 *
 * Existing values are never overwritten. The worklist only ever contains keys
 * the locale was missing, so a collision means the file was hand-edited or a
 * stale worklist is being replayed — either way, stopping is right.
 *
 * Usage:  node scripts/merge-i18n-locale.mjs <locale> [...] | --all [--dry-run]
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const CATALOGS = path.join(ROOT, "src", "i18n", "catalogs");
const DONE_DIR = path.join(HERE, ".i18n-staging", "done");

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

const isBranch = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function leaves(node, prefix = [], out = []) {
  for (const [key, value] of Object.entries(node)) {
    if (isBranch(value)) leaves(value, [...prefix, key], out);
    else out.push([...prefix, key].join("."));
  }
  return out;
}

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

const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const named = args.filter((arg) => !arg.startsWith("--"));
const locales = args.includes("--all")
  ? fs.readdirSync(DONE_DIR).filter((n) => n.endsWith(".json")).map((n) => n.replace(/\.json$/, ""))
  : named;

if (locales.length === 0) {
  console.error("usage: node scripts/merge-i18n-locale.mjs <locale> [...] | --all");
  process.exit(1);
}

for (const locale of locales) {
  const file = path.join(DONE_DIR, `${locale}.json`);
  if (!fs.existsSync(file)) {
    console.error(`  ${locale}: no worklist at ${path.relative(ROOT, file)}`);
    process.exitCode = 1;
    continue;
  }

  const done = JSON.parse(fs.readFileSync(file, "utf8"));
  const catalogs = {
    core: JSON.parse(fs.readFileSync(path.join(CATALOGS, locale, "core.json"), "utf8")),
    extended: JSON.parse(fs.readFileSync(path.join(CATALOGS, locale, "extended.json"), "utf8"))
  };

  const collisions = [];
  let added = 0;

  for (const [namespace, tree] of Object.entries(done)) {
    if (namespace.startsWith("$")) continue;
    const tier = TIER_OF[namespace];
    if (!tier) {
      console.error(`  ${locale}: unknown namespace "${namespace}"`);
      process.exitCode = 1;
      continue;
    }
    const before = leaves(catalogs[tier]).length;
    catalogs[tier][namespace] = catalogs[tier][namespace] ?? {};
    merge(catalogs[tier][namespace], tree, [namespace], collisions);
    added += leaves(catalogs[tier]).length - before;
  }

  if (collisions.length) {
    console.error(`  ${locale}: ${collisions.length} collision(s) — not written`);
    collisions.slice(0, 10).forEach((key) => console.error(`      ${key}`));
    process.exitCode = 1;
    continue;
  }

  if (dryRun) {
    console.log(`  ${locale}: would add ${added} leaves.`);
    continue;
  }

  for (const tier of ["core", "extended"]) {
    fs.writeFileSync(
      path.join(CATALOGS, locale, `${tier}.json`),
      `${JSON.stringify(catalogs[tier], null, 2)}\n`
    );
  }
  console.log(`  ${locale}: merged ${added} leaves.`);
}

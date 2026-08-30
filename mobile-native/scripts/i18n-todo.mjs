#!/usr/bin/env node
/**
 * Build a translation worklist for one locale.
 *
 * Writes `scripts/.i18n-staging/todo/<locale>.json`: every key family present
 * in English but missing from that locale, nested by namespace, with the
 * English string as the value. A translator (human or agent) replaces the
 * values in place and nothing else, then `merge-i18n-locale.mjs` folds the
 * result back into the locale's two catalog files.
 *
 * The point of generating this rather than asking a translator to diff the
 * catalogs by hand is **plural forms**. English has `_one`/`_other`; Japanese
 * needs only `_other`, Arabic needs all six, Haitian Creole needs one form.
 * Emitting the exact key names the locale requires removes the single most
 * common source of catalog defects.
 *
 * Usage:  node scripts/i18n-todo.mjs <locale> [<locale> ...]
 *         node scripts/i18n-todo.mjs --all
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const CATALOGS = path.join(ROOT, "src", "i18n", "catalogs");
const OUT_DIR = path.join(HERE, ".i18n-staging", "todo");

const TIERS = ["core", "extended"];
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;

/* Mirrors PLURAL_CATEGORIES in validate-i18n.mjs — the CLDR categories each
 * language actually needs. Advisory forms are deliberately not requested. */
const REQUIRED = {
  en: ["one", "other"],
  es: ["one", "other"],
  fr: ["one", "other"],
  ht: ["other"],
  pt: ["one", "other"],
  de: ["one", "other"],
  ar: ["zero", "one", "two", "few", "many", "other"],
  hi: ["one", "other"],
  ja: ["other"],
  ko: ["other"],
  zh: ["other"]
};

const isBranch = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

function flatten(node, prefix = [], out = {}) {
  for (const [key, value] of Object.entries(node)) {
    if (key.startsWith("$")) continue;
    if (isBranch(value)) flatten(value, [...prefix, key], out);
    else out[[...prefix, key].join(".")] = value;
  }
  return out;
}

function setPath(target, dotted, value) {
  const parts = dotted.split(".");
  const last = parts.pop();
  let node = target;
  for (const part of parts) {
    node[part] = node[part] ?? {};
    node = node[part];
  }
  node[last] = value;
}

const read = (locale, tier) =>
  JSON.parse(fs.readFileSync(path.join(CATALOGS, locale, `${tier}.json`), "utf8"));

const args = process.argv.slice(2);
const locales = args.includes("--all")
  ? Object.keys(REQUIRED).filter((code) => code !== "en")
  : args;

if (locales.length === 0) {
  console.error("usage: node scripts/i18n-todo.mjs <locale> [...] | --all");
  process.exit(1);
}

fs.mkdirSync(OUT_DIR, { recursive: true });

const english = {};
for (const tier of TIERS) Object.assign(english, flatten(read("en", tier)));

/* Collapse English to families, remembering the singular and plural wording so
 * a translator sees the sentence in the number that key actually renders. */
const families = new Map();
for (const [key, value] of Object.entries(english)) {
  const family = key.replace(PLURAL_SUFFIX, "");
  const suffix = key.slice(family.length + 1);
  const entry = families.get(family) ?? { plural: false, samples: {} };
  if (suffix) {
    entry.plural = true;
    entry.samples[suffix] = value;
  } else {
    entry.samples.base = value;
  }
  families.set(family, entry);
}

for (const locale of locales) {
  const categories = REQUIRED[locale];
  if (!categories) {
    console.error(`  ${locale}: not a supported locale`);
    process.exitCode = 1;
    continue;
  }

  const present = {};
  for (const tier of TIERS) Object.assign(present, flatten(read(locale, tier)));
  const haveFamilies = new Set(Object.keys(present).map((key) => key.replace(PLURAL_SUFFIX, "")));

  const todo = {};
  let count = 0;

  for (const [family, entry] of families) {
    if (haveFamilies.has(family)) continue;
    if (!entry.plural) {
      setPath(todo, family, entry.samples.base);
      count += 1;
      continue;
    }
    for (const category of categories) {
      const source =
        entry.samples[category] ??
        (category === "one" ? entry.samples.one : undefined) ??
        entry.samples.other ??
        entry.samples.one ??
        entry.samples.base;
      setPath(todo, `${family}_${category}`, source);
      count += 1;
    }
  }

  const file = path.join(OUT_DIR, `${locale}.json`);
  fs.writeFileSync(file, `${JSON.stringify(todo, null, 2)}\n`);
  console.log(`  ${locale}: ${count} strings to translate -> ${path.relative(ROOT, file)}`);
}

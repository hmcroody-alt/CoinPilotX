#!/usr/bin/env node
/**
 * Catalog validator.
 *
 * Run in CI and before shipping a language:
 *
 *   node scripts/validate-i18n.mjs
 *   node scripts/validate-i18n.mjs --locale ar --verbose
 *
 * The app already measures coverage at runtime (`src/i18n/coverage.ts`) to show
 * a percentage in the language picker. This script exists for the failures a
 * percentage cannot express, and which are all invisible in code review:
 *
 *   - A translation that drops `{{count}}` renders a sentence with a hole in it.
 *   - A translation that invents `{{name}}` renders the placeholder literally.
 *   - Arabic supplying only `_one`/`_other` renders the wrong form for 3, 11, 100.
 *   - A stray U+200F in an Arabic string corrupts the layout and is unprintable.
 *   - A catalog whose `$version` has drifted will be discarded on upgrade.
 *
 * It reads the JSON directly rather than importing the app's loader, so it runs
 * under plain Node with no transpiler and no React Native shims.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const CATALOGS = path.join(ROOT, "src", "i18n", "catalogs");
const DEFAULT_LOCALE = "en";
const TIERS = ["core", "extended"];

/** Plural suffixes are a grammar artifact, not separate strings to translate. */
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;

/**
 * CLDR plural categories per language.
 *
 * The check this powers is asymmetric on purpose. A locale that defines a
 * category its grammar does not have is harmless — the engine will never select
 * it. A locale *missing* a category its grammar does have is a visible bug:
 * Arabic without `_few` renders "3 إعداد" where it should read "3 إعدادات".
 *
 * `advisory` holds categories that are real but only selected for values this
 * app is unlikely to render. In French, Spanish and Portuguese `many` fires
 * only on exact millions — `2000000` takes "2 millions d'abonnés" rather than
 * "2000000 abonnés". Counts that large are abbreviated ("2M") long before they
 * reach a plural, and `keyVariants` in `src/i18n/engine.ts` already falls back
 * from a missing `_many` to `_other`, so the degradation is a slightly stiff
 * sentence rather than a hole. Reported, not failed: worth a translator's time,
 * not worth blocking a release.
 */
const PLURAL_CATEGORIES = {
  en: { required: ["one", "other"] },
  es: { required: ["one", "other"], advisory: ["many"] },
  fr: { required: ["one", "other"], advisory: ["many"] },
  ht: { required: ["other"] },
  pt: { required: ["one", "other"], advisory: ["many"] },
  de: { required: ["one", "other"] },
  ar: { required: ["zero", "one", "two", "few", "many", "other"] },
  hi: { required: ["one", "other"] },
  ja: { required: ["other"] },
  ko: { required: ["other"] },
  zh: { required: ["other"] }
};

/**
 * Bidi control characters.
 *
 * Direction is handled structurally by `src/i18n/rtl.ts`, so these never belong
 * in catalog data. They are also zero-width: a reviewer cannot see one, and a
 * single stray RLM silently reverses the rendering of a whole line.
 */
const BIDI_CONTROLS = /[‎‏‪-‮⁦-⁩]/;

const PLACEHOLDER = /\{\{(\w+)\}\}/g;

const args = process.argv.slice(2);
const verbose = args.includes("--verbose");
const onlyLocale = args.includes("--locale") ? args[args.indexOf("--locale") + 1] : null;

const locales = fs
  .readdirSync(CATALOGS, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .filter((locale) => !onlyLocale || locale === onlyLocale || locale === DEFAULT_LOCALE)
  .sort((a, b) => (a === DEFAULT_LOCALE ? -1 : b === DEFAULT_LOCALE ? 1 : a.localeCompare(b)));

const problems = [];
const warnings = [];
const fail = (locale, message) => problems.push(`${locale}: ${message}`);
const warn = (locale, message) => warnings.push(`${locale}: ${message}`);

/** Flattens a bundle to `path -> string`, skipping `$version`-style metadata. */
function flatten(node, prefix = "", out = {}) {
  for (const [key, value] of Object.entries(node)) {
    if (key.startsWith("$")) continue;
    const keyPath = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[keyPath] = value;
    else if (value && typeof value === "object") flatten(value, keyPath, out);
  }
  return out;
}

/**
 * Returns the parsed bundle, or `null` having already reported why it could not
 * be read. The distinction between absent and unparseable is kept here rather
 * than left to the caller: reporting a syntax error as "missing core.json" sends
 * someone hunting for a file that is sitting right in front of them.
 */
function read(locale, tier) {
  const file = path.join(CATALOGS, locale, `${tier}.json`);
  if (!fs.existsSync(file)) {
    fail(locale, `missing ${tier}.json`);
    return null;
  }
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    fail(locale, `${tier}.json is not valid JSON — ${error.message}`);
    return null;
  }
}

/* ------------------------------------------------------------------ *
 * Reference: the default catalog defines what "complete" means.
 * ------------------------------------------------------------------ */

const reference = {};
for (const tier of TIERS) {
  const bundle = read(DEFAULT_LOCALE, tier);
  if (!bundle) {
    console.error(`FATAL: cannot read the ${DEFAULT_LOCALE} ${tier} catalog.`);
    process.exit(2);
  }
  reference[tier] = { bundle, flat: flatten(bundle) };
}

const referenceVersion = reference.core.bundle.$version;

const placeholdersIn = (value) => new Set(Array.from(value.matchAll(PLACEHOLDER)).map((match) => match[1]));

/**
 * English placeholders, in two indexes.
 *
 * `referenceExact` is the honest comparison and is used wherever it exists: the
 * locale's key has an English key of exactly the same name, so their placeholder
 * sets should match.
 *
 * `referenceFamily` is the fallback for plural forms English does not have. A
 * locale's `count_few` has no English `count_few` to compare against, so it is
 * checked against the union over the family's English forms — which is also why
 * the union is needed at all: English `count_one` may read "one setting" and
 * omit `{{count}}` while `count_other` includes it.
 *
 * Keeping these separate matters. Folding a suffix-less key into its family
 * makes the family's `{{count}}` mandatory for it, and a base key routinely has
 * no count to interpolate — `messaging.inbox.requests` is the section heading
 * "Requests", sitting beside `requests_one`/`requests_other` which are the
 * counted forms. Under a family-only comparison every locale fails that key,
 * English included, which is the signature of a bad rule rather than bad data.
 */
const referenceExact = {};
const referenceFamily = {};
for (const tier of TIERS) {
  for (const [key, value] of Object.entries(reference[tier].flat)) {
    const found = placeholdersIn(value);
    referenceExact[key] = found;
    const family = key.replace(PLURAL_SUFFIX, "");
    const existing = referenceFamily[family];
    referenceFamily[family] = existing ? new Set([...existing, ...found]) : found;
  }
}

/* ------------------------------------------------------------------ *
 * Per-locale checks
 * ------------------------------------------------------------------ */

const summary = [];

for (const locale of locales) {
  const flat = {};
  let version = null;

  for (const tier of TIERS) {
    const bundle = read(locale, tier);
    if (!bundle) continue; // `read` has already reported the reason.
    if (bundle.$locale && bundle.$locale !== locale) {
      fail(locale, `${tier}.json declares $locale "${bundle.$locale}"`);
    }
    if (bundle.$tier && bundle.$tier !== tier) {
      fail(locale, `${tier}.json declares $tier "${bundle.$tier}"`);
    }
    // A catalog on an older version is discarded by the migration check at
    // launch, so the language silently falls back to English at runtime while
    // looking fully translated on disk.
    if (bundle.$version !== referenceVersion) {
      fail(locale, `${tier}.json is version ${bundle.$version}, expected ${referenceVersion}`);
    }
    version = bundle.$version;
    Object.assign(flat, flatten(bundle));
  }

  const families = new Set(Object.keys(flat).map((key) => key.replace(PLURAL_SUFFIX, "")));
  const referenceFamilies = new Set(
    TIERS.flatMap((tier) => Object.keys(reference[tier].flat)).map((key) => key.replace(PLURAL_SUFFIX, ""))
  );

  const missing = [...referenceFamilies].filter((family) => !families.has(family)).sort();
  const orphaned = [...families].filter((family) => !referenceFamilies.has(family)).sort();

  if (missing.length) fail(locale, `${missing.length} missing key families`);
  // Orphans are not a build failure: a key removed from English leaves them
  // behind, and dead strings hurt nobody. They do mean wasted translator time.
  if (orphaned.length) warn(locale, `${orphaned.length} orphaned key families`);
  if (verbose) {
    missing.slice(0, 40).forEach((family) => console.log(`    missing  ${locale}  ${family}`));
    orphaned.slice(0, 40).forEach((family) => console.log(`    orphan   ${locale}  ${family}`));
  }

  /* Plural completeness. */
  const categories = PLURAL_CATEGORIES[locale];
  if (!categories) {
    warn(locale, "no CLDR plural categories declared in this script — add them");
  } else {
    const pluralFamilies = new Set(
      Object.keys(flat)
        .filter((key) => PLURAL_SUFFIX.test(key))
        .map((key) => key.replace(PLURAL_SUFFIX, ""))
    );
    const absent = (family, list) => (list ?? []).filter((c) => flat[`${family}_${c}`] === undefined);
    let advisoryFamilies = 0;
    for (const family of pluralFamilies) {
      const missingRequired = absent(family, categories.required);
      if (missingRequired.length) {
        fail(locale, `${family} is missing plural form(s) ${missingRequired.join(", ")}`);
      }
      if (absent(family, categories.advisory).length) advisoryFamilies += 1;
    }
    // Rolled up rather than listed: it is one translator decision per language,
    // not one per key, and 30 identical lines would bury the real errors.
    if (advisoryFamilies) {
      warn(
        locale,
        `${advisoryFamilies} plural families omit the advisory form(s) ${categories.advisory.join(", ")}`
      );
    }
  }

  /* Placeholder integrity and bidi hygiene. */
  let emptied = 0;
  const idiomatic = [];
  for (const [key, value] of Object.entries(flat)) {
    if (!value.trim()) {
      fail(locale, `${key} is empty`);
      emptied += 1;
      continue;
    }
    if (BIDI_CONTROLS.test(value)) {
      fail(locale, `${key} contains a Unicode bidi control character`);
    }
    // `{{ count }}` and `{{Count}}` both parse as a different placeholder and
    // render literally. Catch the near-misses the strict regex would skip.
    if (/\{\{\s+\w+|\w+\s+\}\}/.test(value)) {
      fail(locale, `${key} has whitespace inside a placeholder`);
    }

    const expected = referenceExact[key] ?? referenceFamily[key.replace(PLURAL_SUFFIX, "")];
    if (!expected) continue;
    const actual = placeholdersIn(value);
    const invented = [...actual].filter((name) => !expected.has(name));
    if (invented.length) fail(locale, `${key} uses unknown placeholder(s) ${invented.join(", ")}`);

    const dropped = [...expected].filter((name) => !actual.has(name));
    if (dropped.length) {
      const message = `${key} drops placeholder(s) ${dropped.join(", ")}`;
      // Dropping the count in a zero/one/two form is idiomatic in several
      // languages — Arabic writes "لا توجد طلبات" and "طلب رسالة واحد" rather
      // than interpolating 0 or 1 — so it is reported rather than failed: a
      // human should confirm it, not a build. Arabic alone produces 59 of these
      // and listing each one would bury the errors that matter, so the summary
      // gets a count and `--verbose` gets the keys.
      if (/_(zero|one|two)$/.test(key)) idiomatic.push(message);
      else fail(locale, message);
    }
  }

  if (idiomatic.length) {
    warn(locale, `${idiomatic.length} zero/one/two form(s) omit the count — idiomatic, but confirm`);
    if (verbose) idiomatic.forEach((message) => console.log(`    lenient  ${locale}  ${message}`));
  }

  const total = referenceFamilies.size;
  const translated = total - missing.length;
  summary.push({
    locale,
    version,
    percent: total === 0 ? 100 : Math.floor((translated / total) * 100),
    translated,
    total,
    orphaned: orphaned.length,
    empty: emptied
  });
}

/* ------------------------------------------------------------------ *
 * Report
 * ------------------------------------------------------------------ */

const width = Math.max(...summary.map((row) => row.locale.length), 6);
console.log("\n  locale  coverage        keys   orphans");
console.log("  " + "-".repeat(40));
for (const row of summary) {
  const bar = String(row.percent).padStart(3) + "%";
  console.log(
    `  ${row.locale.padEnd(width)}  ${bar}   ${String(row.translated).padStart(4)}/${row.total}   ${
      row.orphaned || ""
    }`
  );
}

if (warnings.length) {
  console.log(`\n  ${warnings.length} warning(s):`);
  warnings.forEach((warning) => console.log(`    ! ${warning}`));
}

if (problems.length) {
  console.log(`\n  ${problems.length} error(s):`);
  problems.forEach((problem) => console.log(`    x ${problem}`));
  console.log("");
  process.exit(1);
}

console.log(`\n  OK — ${summary.length} locales, catalog version ${referenceVersion}.\n`);

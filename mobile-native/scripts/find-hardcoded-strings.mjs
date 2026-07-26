#!/usr/bin/env node
/**
 * Hardcoded user-visible string detector.
 *
 *   node scripts/find-hardcoded-strings.mjs              # ranked worklist
 *   node scripts/find-hardcoded-strings.mjs --file src/screens/FeedScreen.tsx
 *   node scripts/find-hardcoded-strings.mjs --json       # machine-readable
 *   node scripts/find-hardcoded-strings.mjs --max 0      # gate: fail if any
 *
 * `validate-i18n.mjs` answers "is every key translated in every language". This
 * answers the prior question: "is every user-visible string a key at all". A
 * catalog can be at 100% coverage while half the app renders English literals
 * that were never extracted, and nothing in the type system notices — which is
 * exactly the failure the localization work exists to prevent.
 *
 * This is a heuristic, and deliberately a lossy one. It reads text, not an AST,
 * so it cannot know that `title` on a chart axis is user-visible while `name` on
 * a route is not. It is tuned to be *useful* rather than complete: the output is
 * a worklist to burn down and a trend to watch, and the false-positive rate is
 * kept low enough that the list stays worth reading. It is not a correctness
 * proof, and `--max` should be pointed at a ratchet, not at zero, until the
 * migration is finished.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "src");

const args = process.argv.slice(2);
const flag = (name) => (args.includes(name) ? args[args.indexOf(name) + 1] : null);
const asJson = args.includes("--json");
const onlyFile = flag("--file");
const max = flag("--max") === null ? null : Number(flag("--max"));

/**
 * Props whose value is rendered to the user.
 *
 * Kept as an explicit list rather than a pattern. `name` is a display string on
 * an avatar and a route identifier on a navigator; `value` is display text on a
 * stat tile and machine data on a form control. Guessing either way produces
 * noise, so only props that are display-only everywhere in this codebase appear.
 */
const DISPLAY_PROPS = [
  "title",
  "subtitle",
  "label",
  "placeholder",
  "heading",
  "headline",
  "caption",
  "message",
  "description",
  "confirmLabel",
  "cancelLabel",
  "submitLabel",
  "emptyText",
  "emptyTitle",
  "emptyMessage",
  "helperText",
  "errorText",
  "accessibilityLabel",
  "accessibilityHint"
];

/**
 * Directories and files that legitimately hold English literals.
 *
 * The catalogs *are* the English strings. The i18n engine's fallback path has to
 * name things in English to humanize a key. Tests assert on literal copy on
 * purpose. Flagging any of these would train the reader to ignore the tool.
 */
const SKIP_PATH = /(?:^|\/)(?:__tests__|__mocks__|i18n\/catalogs|node_modules|\.expo)(?:\/|$)/;
const SKIP_FILE = /\.(?:test|spec|d)\.tsx?$/;

/** Source lines that are not shipping UI text. */
const SKIP_LINE = [
  /^\s*(?:\/\/|\*|\/\*)/, // comments
  /^\s*import\s/,
  /^\s*export\s+(?:\*|\{)/,
  /\bconsole\.(?:log|warn|error|info|debug)\s*\(/,
  /\b(?:testID|accessibilityRole|nativeID|keyboardType|autoComplete|textContentType|resizeMode|iconName|icon|source|uri|href|name|route|screen|key|id|type|variant|mode|size|color|backgroundColor|borderColor|tintColor|fontFamily|fontWeight|textAlign|flexDirection|justifyContent|alignItems|position|overflow)\s*[:=]/
];

/**
 * A literal worth reporting.
 *
 * Two or more words, or one word of 4+ characters, starting with a letter. This
 * is the line between copy and configuration: "Save changes" and "Cancel" are
 * copy; "row", "md", "#fff", "ios", "chevron-right" are not. Single short words
 * are dropped because that bucket is overwhelmingly enum values, and a tool that
 * reports 900 items with 800 false positives gets muted on day one.
 */
const looksLikeCopy = (text) => {
  const value = text.trim();
  if (value.length < 4) return false;
  if (!/^[A-Za-z]/.test(value)) return false;
  if (/^[a-z0-9-]+$/.test(value) && !value.includes(" ")) return false; // kebab/enum
  if (/^[a-z]+[A-Z]/.test(value) && !value.includes(" ")) return false; // camelCase
  if (/^(?:https?:|www\.|[\w.]+\/)/.test(value)) return false; // urls, paths
  if (/^\w+([.:]\w+)+$/.test(value)) return false; // dotted identifiers, i18n keys
  if (!/[A-Z]/.test(value) && !value.includes(" ")) return false; // bare lowercase word
  return /\s/.test(value) || value.length >= 4;
};

const files = [];
(function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    const relative = path.relative(ROOT, full);
    if (SKIP_PATH.test(relative)) continue;
    if (entry.isDirectory()) walk(full);
    else if (/\.tsx$/.test(entry.name) && !SKIP_FILE.test(entry.name)) files.push(full);
  }
})(SRC);

const targets = onlyFile
  ? files.filter((file) => path.relative(ROOT, file) === onlyFile.replace(/^\.\//, ""))
  : files;

const PROPS = DISPLAY_PROPS.join("|");
/** `title="Copy"` and `title: "Copy"` — the plain forms. */
const displayPropPattern = new RegExp(`\\b(${PROPS})\\s*=\\s*"([^"]+)"`, "g");
const displayKeyPattern = new RegExp(`\\b(${PROPS})\\s*:\\s*"([^"]+)"`, "g");
/**
 * `title={...}` and `title: \`...\`` — the expression forms.
 *
 * These carry the strings a plain-literal sweep misses, and they are the ones
 * that matter most: `subtitle={on ? "Two-factor is on" : "..."}` hides two
 * pieces of copy behind a ternary, and a template literal is by definition a
 * string with an interpolation in it — exactly the case that needs a
 * `{{placeholder}}` key rather than concatenation.
 */
const displayExpressionPattern = new RegExp(`\\b(${PROPS})\\s*[:=]\\s*(\\{|\`)`, "g");
/**
 * JSX text between tags: `<Text ...>Some copy</Text>`.
 *
 * The lookbehind rejects `=>`, which otherwise makes every arrow function
 * returning a generic — `(post: Post) => Promise<void>` — look like a text node
 * containing the word "Promise".
 */
const jsxTextPattern = /(?<!=)>([^<>{}\n]+)</g;
/** `Alert.alert("Title", "Body")` — the most common untranslated dialog. */
const alertPattern = /\bAlert\.alert\(/g;

/** Every `"..."` and `` `...` `` literal in a fragment, template holes blanked. */
function literalsIn(fragment) {
  const out = [];
  for (const [, text] of fragment.matchAll(/"([^"\\]{2,})"/g)) out.push(text);
  for (const [, text] of fragment.matchAll(/`([^`\\]{2,})`/g)) {
    // `Turn off ${label}?` reads as copy only once the hole is neutralised;
    // left raw, the `${` trips the identifier filters and the string is lost.
    out.push(text.replace(/\$\{[^}]*\}/g, "{}"));
  }
  return out;
}

/**
 * The slice of `source` starting at `from` up to the matching close of `open`.
 *
 * Runs over the whole file rather than one line, because the expressions that
 * hide the most copy are exactly the ones that wrap: a prettier-formatted
 * `Alert.alert(...)` puts its title and body on separate lines, and a
 * line-scoped scan sees the `(` and nothing else. The finding is still reported
 * against the line the match *starts* on, which is where a reader would look.
 *
 * `LIMIT` stops a stray unbalanced brace — inside a regex or a string this
 * scanner does not parse — from swallowing the rest of the file.
 */
const LIMIT = 1500;
const CLOSING = { "{": "}", "(": ")" };

function balancedSlice(source, from, open) {
  const stop = Math.min(source.length, from + LIMIT);
  if (open === "`") {
    const end = source.indexOf("`", from + 1);
    return source.slice(from, end === -1 || end > stop ? stop : end + 1);
  }
  const close = CLOSING[open];
  let depth = 0;
  for (let index = from; index < stop; index += 1) {
    if (source[index] === open) depth += 1;
    else if (source[index] === close) {
      depth -= 1;
      if (depth === 0) return source.slice(from, index + 1);
    }
  }
  return source.slice(from, stop);
}

const findings = [];

for (const file of targets) {
  const relative = path.relative(ROOT, file);
  const source = fs.readFileSync(file, "utf8");
  const lines = source.split("\n");

  /** Character offset of the start of each line, for offset -> line number. */
  const offsets = [];
  let cursor = 0;
  for (const line of lines) {
    offsets.push(cursor);
    cursor += line.length + 1;
  }
  const lineAt = (offset) => {
    let low = 0;
    let high = offsets.length - 1;
    while (low < high) {
      const mid = Math.ceil((low + high) / 2);
      if (offsets[mid] <= offset) low = mid;
      else high = mid - 1;
    }
    return low;
  };

  // Deduped per file, not per line: a wrapped expression is now attributed to
  // the line it opens on, so the same string found by the plain and expression
  // passes can carry two different line numbers.
  const seen = new Set();
  const record = (index, kind, text) => {
    const value = text.trim();
    if (!looksLikeCopy(value)) return;
    if (seen.has(value)) return;
    seen.add(value);
    findings.push({ file: relative, line: index + 1, kind, text: value });
  };
  const skipped = (index) => SKIP_LINE.some((pattern) => pattern.test(lines[index]));

  /* Expression passes: whole-file, so wrapped calls are read in full. */
  for (const match of source.matchAll(displayExpressionPattern)) {
    const index = lineAt(match.index);
    if (skipped(index)) continue;
    const start = match.index + match[0].length - 1;
    literalsIn(balancedSlice(source, start, match[2])).forEach((text) => record(index, match[1], text));
  }
  for (const match of source.matchAll(alertPattern)) {
    const index = lineAt(match.index);
    if (skipped(index)) continue;
    const start = match.index + match[0].length - 1;
    literalsIn(balancedSlice(source, start, "(")).forEach((text) => record(index, "Alert", text));
  }

  /* Plain passes: line-scoped, since a literal cannot span a newline. */
  lines.forEach((line, index) => {
    if (skipped(index)) return;
    for (const [, prop, text] of line.matchAll(displayPropPattern)) record(index, prop, text);
    for (const [, prop, text] of line.matchAll(displayKeyPattern)) record(index, prop, text);
    // JSX text is only meaningful on a line that actually opens a tag, which
    // keeps `a > b` comparisons and generics out of the results.
    if (/<[A-Za-z]/.test(line)) {
      for (const [, text] of line.matchAll(jsxTextPattern)) record(index, "JSX text", text);
    }
  });
}

findings.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);

if (asJson) {
  console.log(JSON.stringify(findings, null, 2));
  // `process.exit` and not `exitCode`: stdout is a non-blocking pipe when this
  // is piped anywhere, so exiting here truncates the payload at the buffer
  // boundary — around 64KB, which this report passes. Setting the code instead
  // lets Node flush and exit on its own.
  process.exitCode = findings.length > (max ?? Infinity) ? 1 : 0;
} else {
  /* ---------------------------------------------------------------- *
   * Report: ranked by file, because migration happens a screen at a time.
   * ---------------------------------------------------------------- */

  const byFile = new Map();
  for (const finding of findings) {
    if (!byFile.has(finding.file)) byFile.set(finding.file, []);
    byFile.get(finding.file).push(finding);
  }
  const ranked = [...byFile.entries()].sort((a, b) => b[1].length - a[1].length);

  if (onlyFile || args.includes("--verbose")) {
    for (const [file, items] of ranked) {
      console.log(`\n  ${file}  (${items.length})`);
      for (const item of items) {
        console.log(`    ${String(item.line).padStart(5)}  ${item.kind.padEnd(18)}  ${item.text}`);
      }
    }
  } else {
    console.log("\n  Files with the most hardcoded user-visible strings:\n");
    for (const [file, items] of ranked.slice(0, 25)) {
      console.log(`  ${String(items.length).padStart(5)}  ${file}`);
    }
    if (ranked.length > 25) console.log(`  ${" ".repeat(5)}  ... and ${ranked.length - 25} more files`);
  }

  const clean = targets.length - byFile.size;
  console.log(
    `\n  ${findings.length} strings across ${byFile.size} files; ${clean}/${targets.length} files clean.`
  );
  if (!onlyFile) console.log("  Re-run with --file <path> to see one file, or --verbose for all.\n");

  if (max !== null && findings.length > max) {
    console.log(`  FAIL: ${findings.length} exceeds the --max budget of ${max}.\n`);
    process.exitCode = 1;
  }
}

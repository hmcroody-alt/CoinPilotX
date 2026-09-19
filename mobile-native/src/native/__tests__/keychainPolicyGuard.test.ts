/**
 * Every keychain write must state its accessibility class, not inherit one.
 *
 * `expo-secure-store` defaults to `kSecAttrAccessibleWhenUnlocked`
 * (`SecureStoreOptions.swift`: `keychainAccessible: SecureStoreAccessible = .whenUnlocked`),
 * which makes an item unreadable whenever the screen is locked. That default is
 * invisible at the call site — a `setItemAsync(key, value)` with no third
 * argument looks identical to a considered choice — and the failure it produces
 * is silent, because every read in this app is wrapped in a `.catch()` that
 * degrades to "nothing stored".
 *
 * `api/push.ts` shipped in exactly that state: its cached push registration was
 * written with no options, so `readCachedPushRegistration` would have returned
 * null on a locked device and the token-refresh branch would have skipped
 * revoking the stale endpoint. It never bit, because all four callers are
 * foreground user actions — which is the point of this guard. The next keychain
 * item may not be so lucky, and "it happens to be read in the foreground today"
 * is a property of the callers, not of the item.
 *
 * So this is a policy check rather than a behaviour test: it reads the source of
 * every `SecureStore.setItemAsync` in `src/` and requires a third argument whose
 * options carry `keychainAccessible`. The reasoning for each individual item is
 * in `docs/apple/DEVICE_SECURITY.md`.
 */
import * as fs from "fs";
import * as path from "path";

const SRC = path.resolve(__dirname, "../..");

/** Every call site as of this guard landing. Used only to prove the parse is not vacuous. */
const KNOWN_CALL_SITES = 10;

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "node_modules" || entry.name === "__dbg__") continue;
      walk(full, out);
    } else if (/\.(ts|tsx)$/.test(entry.name) && !entry.name.includes(".backup-")) {
      out.push(full);
    }
  }
  return out;
}

/**
 * The argument list of the call starting at `open`, split at top-level commas.
 *
 * Written as a bracket walk rather than a regex because two of the real call
 * sites span several lines and one passes a `JSON.stringify(...)` whose own
 * parentheses and comma would end a regex match early — which would silently
 * read the *second* argument as the options object and pass a call that has no
 * options at all.
 */
function argumentsAt(source: string, open: number): string[] {
  const args: string[] = [];
  let depth = 0;
  let quote: string | null = null;
  let start = open + 1;
  for (let i = open; i < source.length; i += 1) {
    const ch = source[i];
    if (quote) {
      if (ch === "\\") i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      quote = ch;
      continue;
    }
    if (ch === "(" || ch === "[" || ch === "{") depth += 1;
    else if (ch === ")" || ch === "]" || ch === "}") {
      depth -= 1;
      if (depth === 0) {
        args.push(source.slice(start, i));
        return args;
      }
    } else if (ch === "," && depth === 1) {
      args.push(source.slice(start, i));
      start = i + 1;
    }
  }
  throw new Error("unterminated setItemAsync call");
}

/**
 * Whether `expression` — a third argument — ends up carrying `keychainAccessible`.
 *
 * Handles the three shapes the codebase uses: an inline object literal, a bare
 * identifier naming a module-scope options const, and a spread of one such const
 * with extra fields (`{ ...BIOMETRIC_KEYCHAIN_OPTIONS, authenticationPrompt }`).
 */
function statesAccessibility(expression: string, source: string): boolean {
  if (/keychainAccessible/.test(expression)) return true;
  const identifiers = expression.match(/[A-Za-z_$][A-Za-z0-9_$]*/g) || [];
  return identifiers.some((name) => {
    const declaration = new RegExp(`const\\s+${name}\\b[^=]*=\\s*\\{`).exec(source);
    if (!declaration) return false;
    const body = argumentsAt(source, declaration.index + declaration[0].length - 1).join(",");
    return /keychainAccessible/.test(body);
  });
}

/** Call sites in `source` that do not state an accessibility class. */
function offendersIn(source: string): string[] {
  const offenders: string[] = [];
  const call = /SecureStore\.setItemAsync\s*\(/g;
  let match: RegExpExecArray | null;
  while ((match = call.exec(source))) {
    const open = match.index + match[0].length - 1;
    const args = argumentsAt(source, open);
    const options = args[2];
    if (!options || !statesAccessibility(options, source)) {
      offenders.push(source.slice(match.index, open + 40).split("\n")[0]);
    }
  }
  return offenders;
}

function countCallsIn(source: string): number {
  return (source.match(/SecureStore\.setItemAsync\s*\(/g) || []).length;
}

describe("keychain accessibility policy", () => {
  const files = walk(SRC).map((file) => ({ rel: path.relative(SRC, file), source: fs.readFileSync(file, "utf8") }));

  it("every SecureStore write states an accessibility class", () => {
    const offenders: string[] = [];
    for (const { rel, source } of files) {
      for (const call of offendersIn(source)) offenders.push(`${rel}: ${call}`);
    }
    expect(offenders).toEqual([]);
  });

  it("finds the writes it claims to be checking", () => {
    // Without this the test above passes trivially the moment the call pattern
    // changes — a rename to `SecureStore.set(...)`, or a helper that wraps the
    // write, would leave zero call sites and a green, meaningless assertion.
    const total = files.reduce((sum, { source }) => sum + countCallsIn(source), 0);
    expect(total).toBeGreaterThanOrEqual(KNOWN_CALL_SITES);
  });

  it("rejects a write with no options, and accepts each shape in use", () => {
    // The negative control. `offendersIn` is the whole guard, so it has to be
    // shown capable of returning something before an empty result means anything.
    const bare = `SecureStore.setItemAsync(KEY, JSON.stringify(value)).catch(() => undefined);`;
    expect(offendersIn(bare)).toHaveLength(1);

    const inline = `SecureStore.setItemAsync(KEY, value, { keychainAccessible: X });`;
    const named = `const OPTS = { keychainAccessible: X, keychainService: "s" };\nSecureStore.setItemAsync(KEY, value, OPTS);`;
    const spread = `const OPTS = { keychainAccessible: X };\nSecureStore.setItemAsync(KEY, value, { ...OPTS, authenticationPrompt: p });`;
    for (const source of [inline, named, spread]) expect(offendersIn(source)).toEqual([]);

    // A named const that does *not* set accessibility must still be caught,
    // otherwise the identifier branch would launder every future omission.
    const hollow = `const OPTS = { keychainService: "s" };\nSecureStore.setItemAsync(KEY, value, OPTS);`;
    expect(offendersIn(hollow)).toHaveLength(1);
  });
});

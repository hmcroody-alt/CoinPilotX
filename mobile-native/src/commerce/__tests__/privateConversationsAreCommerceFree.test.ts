/**
 * The brief's one absolute rule, held by the import graph rather than by memory.
 *
 * > NO product recommendations inside private conversations.
 *
 * There are two ways to keep that. One is to render commerce inside `ChatScreen`
 * behind a condition that is always false. The other is for `ChatScreen` to have
 * no commerce component in scope at all — no import, no transitive import, so
 * nothing to accidentally render, nothing for a future refactor to un-gate, and
 * nothing for a flag to turn on by mistake.
 *
 * This file asserts the second one, and it asserts it *transitively*, which is
 * the half that matters. A direct `import { CommerceFeedCard }` in `ChatScreen`
 * is the version of this mistake that code review catches. The version review
 * misses is a shared helper three levels down — a `ConversationControlCenter`,
 * a media grid, a link preview — quietly pulling `src/commerce` in behind it.
 *
 * ## Why this is a file-system walk and not `jest.mock`
 *
 * A mock-based version ("render ChatScreen, assert no commerce testID appears")
 * proves the weaker claim: that commerce did not render *in the states this test
 * happened to drive*. The screen has dozens of states. Reading the module graph
 * proves the strong claim for all of them at once, and it does it without a
 * renderer, a fixture conversation, or a single mock that could drift.
 *
 * ## Scope
 *
 * The walk follows *relative* imports only. Package imports are deliberately not
 * resolved: `src/commerce` is reachable only by relative path from inside this
 * app, so a node_modules traversal would add minutes of I/O to prove nothing.
 */
import fs from "fs";
import path from "path";

const SRC = path.resolve(__dirname, "..", "..");

/** The surfaces where a private message stream is rendered. */
const PRIVATE_SURFACES = [
  "screens/ChatScreen.tsx",
  "messaging/assistantConnection.ts",
  "media/ConversationMediaGalleryHost.tsx"
];

/** Anything under here is a commerce placement component or its state. */
const COMMERCE_DIR = path.join(SRC, "commerce");

const EXTENSIONS = [".ts", ".tsx", ".native.ts", ".native.tsx", ".js", ".jsx"];

/**
 * Resolve a relative specifier the way Metro would, or null if it is not a file
 * we can follow.
 *
 * Returning null rather than throwing on a miss is deliberate: a specifier this
 * function cannot resolve is one it also cannot use to *reach* commerce, so a
 * miss is safe. Throwing would turn an unrelated import-style change somewhere
 * in the messaging tree into a failure of the privacy test, which teaches people
 * to delete the privacy test.
 */
function resolveRelative(fromFile: string, specifier: string): string | null {
  const base = path.resolve(path.dirname(fromFile), specifier);
  for (const extension of EXTENSIONS) {
    const candidate = `${base}${extension}`;
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  if (fs.existsSync(base) && fs.statSync(base).isDirectory()) {
    for (const extension of EXTENSIONS) {
      const candidate = path.join(base, `index${extension}`);
      if (fs.existsSync(candidate)) return candidate;
    }
  }
  if (fs.existsSync(base) && fs.statSync(base).isFile()) return base;
  return null;
}

/**
 * Every relative specifier in a file: static imports, `export … from`, and
 * `require`/dynamic `import()`.
 *
 * The dynamic forms are included because they are exactly how a component gets
 * pulled into a screen without appearing in the import block at the top, which
 * is the only place a reader looks.
 */
function relativeSpecifiers(source: string): string[] {
  const found: string[] = [];
  const patterns = [
    /(?:^|\n)\s*(?:import|export)[\s\S]{0,400}?from\s+["'](\.[^"']+)["']/g,
    /(?:^|\n)\s*import\s+["'](\.[^"']+)["']/g,
    /\brequire\(\s*["'](\.[^"']+)["']\s*\)/g,
    /\bimport\(\s*["'](\.[^"']+)["']\s*\)/g
  ];
  for (const pattern of patterns) {
    let match = pattern.exec(source);
    while (match) {
      found.push(match[1]);
      match = pattern.exec(source);
    }
  }
  return found;
}

/**
 * Breadth-first over the relative import graph, returning the first path that
 * reaches `src/commerce` — or null.
 *
 * The *path* is returned rather than a boolean so a failure names the chain
 * (`ChatScreen → ConversationControlCenter → …`) instead of just announcing
 * that one exists somewhere. A privacy test that cannot say where the leak is
 * gets suppressed.
 */
function pathIntoCommerce(entry: string): string[] | null {
  const seen = new Set<string>([entry]);
  const queue: { file: string; trail: string[] }[] = [{ file: entry, trail: [entry] }];

  while (queue.length) {
    const { file, trail } = queue.shift()!;
    let source: string;
    try {
      source = fs.readFileSync(file, "utf8");
    } catch {
      continue;
    }
    for (const specifier of relativeSpecifiers(source)) {
      const resolved = resolveRelative(file, specifier);
      if (!resolved) continue;
      // Tests are not shipped and are not part of any screen's runtime graph.
      if (/__tests__|\.test\.[jt]sx?$/.test(resolved)) continue;
      if (resolved.startsWith(COMMERCE_DIR + path.sep)) return [...trail, resolved];
      if (seen.has(resolved)) continue;
      seen.add(resolved);
      queue.push({ file: resolved, trail: [...trail, resolved] });
    }
  }
  return null;
}

function relative(file: string): string {
  return path.relative(SRC, file);
}

describe("private conversations never reach the commerce layer", () => {
  it.each(PRIVATE_SURFACES)("%s imports nothing from src/commerce, at any depth", (surface) => {
    const entry = path.join(SRC, surface);
    expect(fs.existsSync(entry)).toBe(true);

    const leak = pathIntoCommerce(entry);
    // The chain is spelled out in the failure because "something imports
    // commerce" is not actionable and "this is the import that did it" is.
    expect(leak ? leak.map(relative).join(" → ") : null).toBeNull();
  });

  it("finds the chain when there is one, so a pass is not a walk that gave up", () => {
    // The negative control lives in the test rather than in a temporary edit to
    // the real screen: the walker is the only thing being proved here, and a
    // walker that silently resolves nothing would pass every assertion above
    // while checking nothing at all.
    //
    // `MessengerScreen` is the surface that *is* allowed to place commerce — the
    // inbox, not a conversation — so it doubles as the fixture. If this ever
    // returns null, either the strip was removed or the walk is broken, and both
    // are things to look at.
    const leak = pathIntoCommerce(path.join(SRC, "screens", "MessengerScreen.tsx"));
    expect(leak).not.toBeNull();
    expect(relative(leak![leak!.length - 1]).startsWith(`commerce${path.sep}`)).toBe(true);
  });
});

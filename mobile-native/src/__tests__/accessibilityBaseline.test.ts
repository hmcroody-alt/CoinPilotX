/**
 * Static accessibility guard for interactive elements.
 *
 * Why static rather than render-based: an unnamed button is a source-level
 * defect, and rendering every screen would need each one's navigation, auth and
 * network context stubbed. Parsing the source catches the whole surface cheaply.
 *
 * Why a parser rather than a regex: a JSX opening tag routinely spans several
 * lines, so line-oriented matching attributes props to the wrong element. This
 * brace/paren-matches to the real end of each opening tag.
 *
 * The rule: every Pressable/Touchable must either expose an accessible name
 * (an accessibilityLabel, or a text descendant RN can derive the name from) or
 * be explicitly removed from the accessibility tree. A control that satisfies
 * neither is announced by VoiceOver as an unnamed "button".
 */
import { readFileSync } from "fs";
import { join } from "path";

const SRC = join(__dirname, "..");

/**
 * Screens audited and cleaned. New unnamed controls here are regressions.
 * Extend this list as other areas are swept.
 */
const GUARDED_FILES = [
  "screens/MarketplaceScreen.tsx",
  "screens/SellerApplicationScreen.tsx",
  "screens/SellerStoreScreen.tsx",
  "screens/BuyerOrdersScreen.tsx",
  "screens/CoursesLearningScreen.tsx",
  "screens/ContentPlannerScreen.tsx",
  "screens/SavedScreen.tsx",
  "screens/EventsScreen.tsx",
  "screens/GroupsScreen.tsx",
  "screens/CameraStudioScreen.tsx",
  "screens/HomeScreen.tsx",
  "screens/PremiumScreen.tsx",
  "screens/UserDashboardScreen.tsx",
  "screens/ProfileEditScreen.tsx",
  "screens/NotificationCenterScreen.tsx",
  "components/HomePulseComposer.tsx",
  "components/FeedComposer.tsx"
];

const INTERACTIVE = ["Pressable", "TouchableOpacity", "TouchableHighlight"];

/** Index just past the '>' that closes the opening tag starting at `start`. */
function findTagEnd(src: string, start: number): number {
  let depthBrace = 0;
  let depthParen = 0;
  let quote: string | null = null;
  for (let i = start; i < src.length; i += 1) {
    const c = src[i];
    if (quote) {
      if (c === "\\") i += 1;
      else if (c === quote) quote = null;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      quote = c;
      continue;
    }
    if (c === "{") depthBrace += 1;
    else if (c === "}") depthBrace -= 1;
    else if (c === "(") depthParen += 1;
    else if (c === ")") depthParen -= 1;
    else if (c === ">" && depthBrace === 0 && depthParen === 0) return i + 1;
  }
  return -1;
}

/** Index just past the element's closing tag, accounting for nesting. */
function findElementEnd(src: string, tagEnd: number, name: string): number {
  if (src[tagEnd - 2] === "/") return tagEnd;
  const openRe = new RegExp(`<${name}[\\s/>]`, "g");
  const closeRe = new RegExp(`</${name}\\s*>`, "g");
  let depth = 1;
  let i = tagEnd;
  while (i < src.length && depth > 0) {
    openRe.lastIndex = i;
    closeRe.lastIndex = i;
    const open = openRe.exec(src);
    const close = closeRe.exec(src);
    if (!close) return src.length;
    if (open && open.index < close.index) {
      const end = findTagEnd(src, open.index + open[0].length - 1);
      if (end !== -1 && src[end - 2] !== "/") depth += 1;
      i = end !== -1 ? end : open.index + open[0].length;
    } else {
      depth -= 1;
      i = close.index + close[0].length;
    }
  }
  return i;
}

/** True when the body yields text VoiceOver can read as the element's name. */
function hasTextChild(body: string): boolean {
  return /<Text[\s>]/.test(body) || />\s*[A-Za-z0-9][^<>{}]{1,}\s*</.test(body);
}

type Unnamed = { file: string; line: number; snippet: string };

function findUnnamed(relPath: string): Unnamed[] {
  const src = readFileSync(join(SRC, relPath), "utf8");
  const tagRe = new RegExp(`<(${INTERACTIVE.join("|")})[\\s/>]`, "g");
  const out: Unnamed[] = [];
  let m: RegExpExecArray | null;
  while ((m = tagRe.exec(src)) !== null) {
    const name = m[1];
    const tagEnd = findTagEnd(src, m.index + m[0].length - 1);
    if (tagEnd === -1) continue;
    const tag = src.slice(m.index, tagEnd);
    const hidden =
      tag.includes("accessibilityElementsHidden") ||
      tag.includes('importantForAccessibility="no');
    if (hidden || tag.includes("accessibilityLabel")) continue;
    const body = src.slice(tagEnd, findElementEnd(src, tagEnd, name));
    if (hasTextChild(body)) continue;
    out.push({
      file: relPath,
      line: src.slice(0, m.index).split("\n").length,
      snippet: tag.replace(/\s+/g, " ").slice(0, 100)
    });
  }
  return out;
}

describe("accessibility baseline", () => {
  it("exposes an accessible name on every interactive control in audited screens", () => {
    const offenders = GUARDED_FILES.flatMap(findUnnamed);
    const report = offenders
      .map((o) => `  ${o.file}:${o.line}  ${o.snippet}`)
      .join("\n");
    expect(
      offenders.length === 0
        ? ""
        : `Interactive controls with no accessible name (add accessibilityLabel, ` +
            `a <Text> child, or accessibilityElementsHidden if decorative):\n${report}`
    ).toBe("");
  });

  it("announces audited controls as buttons so users know they are actionable", () => {
    // A named control that lacks a role is read as plain text by VoiceOver,
    // giving no cue that it can be activated.
    const missingRole: string[] = [];
    for (const rel of GUARDED_FILES) {
      const src = readFileSync(join(SRC, rel), "utf8");
      const tagRe = new RegExp(`<(${INTERACTIVE.join("|")})[\\s/>]`, "g");
      let m: RegExpExecArray | null;
      while ((m = tagRe.exec(src)) !== null) {
        const tagEnd = findTagEnd(src, m.index + m[0].length - 1);
        if (tagEnd === -1) continue;
        const tag = src.slice(m.index, tagEnd);
        if (
          tag.includes("accessibilityElementsHidden") ||
          tag.includes('importantForAccessibility="no')
        ) {
          continue;
        }
        if (!tag.includes("accessibilityRole")) {
          const line = src.slice(0, m.index).split("\n").length;
          missingRole.push(`  ${rel}:${line}  ${tag.replace(/\s+/g, " ").slice(0, 90)}`);
        }
      }
    }
    expect(missingRole.join("\n")).toBe("");
  });
});

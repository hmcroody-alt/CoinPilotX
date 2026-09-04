/**
 * Stage 3 — the entitlement drift guard.
 *
 * Client-side tier inference is not a bug that gets fixed once. It grows back,
 * because writing `["active","premium"].includes(status)` is faster than
 * finding the module that already knows the answer, and because it *looks*
 * right in review — the array is plausible, the screen renders, the build is
 * green. What it costs shows up later and somewhere else: this app had six
 * such deciders using four mutually inconsistent arrays, so a lifetime member
 * got a badge on their profile and none in the navigation drawer, and `"trial"`
 * was premium to the backend and free to every screen.
 *
 * So the rule is enforced instead of documented. Two of them, in fact:
 *
 *   Rule A  No file may derive membership from an entitlement status *string*.
 *   Rule B  No file may read a raw status field unless it is on a list.
 *
 * Rule A is the one that matters — it catches the decision. Rule B narrows the
 * blast radius by keeping the raw fields out of files that have no business
 * holding them, so a future Rule A violation has nothing local to feed on.
 *
 * Neither rule bans server-computed booleans (`premium_active`, `is_premium`,
 * `membership.is_premium`). Those are the server's answer already; forwarding
 * an answer is not inferring one.
 *
 * Every allowlist below is an explicit list of files. There is deliberately no
 * directory wildcard: a wildcard would let a new file dropped into `screens/`
 * bypass the boundary on the day it is created.
 */

import fs from "fs";
import path from "path";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const NATIVE_SRC = path.join(REPO_ROOT, "mobile-native", "src");
const AUDIO_MANIFEST = path.join(REPO_ROOT, "config", "realtime-audio-protected-paths.json");

/**
 * Files frozen by the real-time audio change policy, not approved by it.
 *
 * `MusicScreen.tsx` and `LiveHostSessionScreen.tsx` both still infer membership
 * from a status string, and both are protected paths. `docs/realtime_audio_
 * change_policy.md` names "Premium" among the mission types that must not edit
 * one, and the exception route requires physical audible regression testing and
 * CODEOWNERS approval — neither of which an entitlement mission can honestly
 * produce for a change to a cosmetic badge and an upload hint.
 *
 * They are listed here so the debt is visible and counted rather than silently
 * skipped. The test below asserts they really are in the audio manifest, so
 * this exemption dissolves the moment the audio lock is lifted from them.
 */
const AUDIO_FROZEN = [
  "mobile-native/src/screens/MusicScreen.tsx",
  "mobile-native/src/screens/LiveHostSessionScreen.tsx"
];

/** Rule A exemptions: nothing beyond the audio-frozen pair. */
const INFERENCE_ALLOWED = [...AUDIO_FROZEN];

/**
 * Rule B exemptions: files that legitimately carry a raw status field.
 *
 * Three legitimate reasons appear here and no others — declaring the field on a
 * payload type, carrying it verbatim through a session/profile shape, and
 * handing it to `membershipMark` to render someone else's badge.
 */
const RAW_FIELD_ALLOWED = [
  // Payload and session type declarations.
  "mobile-native/src/api/auth.ts",
  "mobile-native/src/api/account.ts",
  "mobile-native/src/api/profile.ts",
  "mobile-native/src/api/premium.ts",
  // Session shape: carries the field, decides nothing with it.
  "mobile-native/src/session/auth.ts",
  "mobile-native/src/session/sessionStore.ts",
  // Display marks on *other people's* profiles, via `membershipMark`.
  "mobile-native/src/components/ProfileHeader.tsx",
  "mobile-native/src/screens/ProfileScreen.tsx",
  ...AUDIO_FROZEN
];

/** Status words that describe membership. A test over these is a decision. */
const STATUS_WORDS = [
  "active",
  "premium",
  "founder",
  "lifetime",
  "trial",
  "trialing",
  "grace",
  "grandfathered",
  "pro",
  "verified"
];

const RAW_STATUS_FIELDS = ["premium_status", "subscription_status", "provider_status"];

/** Every .ts/.tsx under mobile-native/src, excluding tests. */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      // Tests name the forbidden patterns in order to assert on them; this file
      // is itself under __tests__, so its own marker strings never trip the scan.
      if (entry.name === "__tests__" || entry.name === "node_modules") continue;
      sourceFiles(full, out);
    } else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Blank out comments so prose about the rule does not trip the rule.
 *
 * Replaced with spaces rather than deleted so line numbers in a failure message
 * still point at the real line.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(/\/\/[^\n]*/g, (line) => line.replace(/./g, " "));
}

const ALL_SOURCES = sourceFiles(NATIVE_SRC);
const relative = (abs: string) => path.relative(REPO_ROOT, abs).split(path.sep).join("/");

/**
 * The status vocabulary is shared with things that are not entitlements.
 *
 * "active" also describes an order, a livestream, a call leg and a guest slot;
 * `["published","live","active"].includes(publication)` is a store listing, not
 * a membership decision. Matching on the words alone flagged seven such lines
 * and would have taught the next person to silence the guard rather than obey
 * it — the failure mode that ends with the rule switched off.
 *
 * So a line has to look like *entitlement* status before it can violate Rule A:
 * a status-word test AND some entitlement noun on the same line. Every one of
 * the six historical deciders satisfies both halves; none of the livestream or
 * marketplace status tests satisfy the second.
 */
const ENTITLEMENT_CONTEXT =
  /premium|founder|lifetime|subscription|entitle|\btier\b|\bplan\b|membership/i;

/** `["active","premium"].includes(...)` — an array of status words used as a test. */
const ARRAY_TEST = new RegExp(
  `\\[[^\\]\\n]*"(?:${STATUS_WORDS.join("|")})"[^\\]\\n]*\\]\\s*\\.includes\\s*\\(`,
  "i"
);

/** `.toLowerCase().includes("premium")` — the substring form of the same mistake. */
const SUBSTRING_TEST = new RegExp(
  `\\.includes\\s*\\(\\s*"(?:${STATUS_WORDS.join("|")})"\\s*\\)`,
  "i"
);

/** `status === "premium"`, `plan !== "lifetime"` — the comparison form. */
const EQUALITY_TEST = new RegExp(
  `(?:premium_status|subscription_status|provider_status|\\bplan\\b|\\btier\\b)\\s*(?:\\|\\||&&|\\)|\\s)*[!=]==\\s*"(?:${STATUS_WORDS.join("|")})"`,
  "i"
);

const RAW_FIELD = new RegExp(`\\b(?:${RAW_STATUS_FIELDS.join("|")})\\b`);

/** A status-word array on a line that is talking about entitlements. */
const inferenceByArray = (line: string) =>
  ARRAY_TEST.test(line) && ENTITLEMENT_CONTEXT.test(line);

/**
 * A substring test applied to a raw status field.
 *
 * The receiver has to be the status field itself. `name.includes("Premium")`
 * tests a route name and `lockReason.includes("Premium")` tests display copy —
 * neither decides anything about membership.
 */
const inferenceBySubstring = (line: string) => SUBSTRING_TEST.test(line) && RAW_FIELD.test(line);

const inferenceByEquality = (line: string) => EQUALITY_TEST.test(line);

type Finding = { file: string; line: number; text: string; rule: string };

function scan(matches: (line: string) => boolean, rule: string, allowed: string[]): Finding[] {
  const allowSet = new Set(allowed);
  const findings: Finding[] = [];
  ALL_SOURCES.forEach((abs) => {
    const rel = relative(abs);
    if (allowSet.has(rel)) return;
    stripComments(fs.readFileSync(abs, "utf8"))
      .split("\n")
      .forEach((text, index) => {
        if (matches(text)) {
          findings.push({ file: rel, line: index + 1, text: text.trim(), rule });
        }
      });
  });
  return findings;
}

const describeFindings = (findings: Finding[]) =>
  findings.map((f) => `${f.file}:${f.line}  [${f.rule}]  ${f.text}`).join("\n");

describe("Rule A — membership is never inferred from a status string", () => {
  it("has no status-word array used as a membership test", () => {
    const findings = scan(inferenceByArray, "status-word array", INFERENCE_ALLOWED);
    expect(
      describeFindings(findings) ||
        "clean — every membership decision goes through entitlements/canonicalTier"
    ).toBe("clean — every membership decision goes through entitlements/canonicalTier");
  });

  it("has no substring test against a status field", () => {
    const findings = scan(inferenceBySubstring, "status substring", INFERENCE_ALLOWED);
    expect(describeFindings(findings)).toBe("");
  });

  it("has no direct comparison of a status field to a status word", () => {
    const findings = scan(inferenceByEquality, "status equality", INFERENCE_ALLOWED);
    expect(describeFindings(findings)).toBe("");
  });

  it("still catches the shapes it was built to catch", () => {
    // A guard that passes because it stopped matching anything is worse than no
    // guard. These are the exact lines this app used to contain, so if a future
    // refinement of the patterns silences them, it fails here rather than in
    // production six months later.
    const historical = [
      `["active", "premium", "founder", "lifetime"].includes(String(profile.premium_status || "").toLowerCase())`,
      `["active", "premium", "founder"].includes(String(user.premium_status || "").toLowerCase())`,
      `["active", "trialing", "premium", "founder"].includes(String(value || "").toLowerCase())`,
      `["active", "verified", "pro", "premium"].includes(String(authState.user?.premium_status || "").toLowerCase())`
    ];
    historical.forEach((line) => expect({ line, caught: inferenceByArray(line) }).toEqual({ line, caught: true }));

    const substring = `String(profile?.premium_status || "").toLowerCase().includes("premium")`;
    expect({ substring, caught: inferenceBySubstring(substring) }).toEqual({ substring, caught: true });

    const equality = `if (subscription_status === "active") return true;`;
    expect({ equality, caught: inferenceByEquality(equality) }).toEqual({ equality, caught: true });
  });

  it("does not fire on unrelated status vocabularies", () => {
    // Livestream, call and marketplace states share the word "active". Flagging
    // them would train the next person to disable the rule instead of obey it.
    const unrelated = [
      `if (!["published", "live", "active"].includes(publication)) {`,
      `if (!["accepted", "connecting", "connected", "active", "reconnecting"].includes(normalized)) return;`,
      `if (["active", "approved", "live"].includes(raw)) return "live";`,
      `if (name.includes("Premium")) return t("common:navSubtitles.membership");`,
      `if (module.lockReason?.includes("Premium")) {`
    ];
    unrelated.forEach((line) =>
      expect({
        line,
        caught: inferenceByArray(line) || inferenceBySubstring(line) || inferenceByEquality(line)
      }).toEqual({ line, caught: false })
    );
  });

  it("keeps the entitlements module itself free of inference", () => {
    // The owner is not exempt from its own rule. `membershipMark` holds one set
    // of status words, copied from the server's visibility predicate, and it is
    // reached through a named function rather than an inline array — so it does
    // not match the patterns above, and must not start to.
    const entitlements = ALL_SOURCES.filter((abs) =>
      relative(abs).startsWith("mobile-native/src/entitlements/")
    );
    expect(entitlements.length).toBeGreaterThanOrEqual(3);
    entitlements.forEach((abs) => {
      const body = stripComments(fs.readFileSync(abs, "utf8"));
      expect({ file: relative(abs), arrayTest: ARRAY_TEST.test(body) }).toEqual({
        file: relative(abs),
        arrayTest: false
      });
    });
  });
});

describe("Rule B — raw status fields stay on their list", () => {
  it("is not read outside the files declared to carry it", () => {
    const findings = scan((line) => RAW_FIELD.test(line), "raw status field", RAW_FIELD_ALLOWED);
    expect(
      describeFindings(findings) ||
        "clean — raw status fields are confined to their declared carriers"
    ).toBe("clean — raw status fields are confined to their declared carriers");
  });

  it("names only files that exist", () => {
    [...INFERENCE_ALLOWED, ...RAW_FIELD_ALLOWED].forEach((rel) => {
      // A stale allowlist entry is worse than none: it stops protecting whatever
      // replaced the file, and does so silently.
      expect({ rel, exists: fs.existsSync(path.join(REPO_ROOT, rel)) }).toEqual({
        rel,
        exists: true
      });
    });
  });
});

describe("the audio-frozen exemption is real, not a convenience", () => {
  it("exempts only files the audio manifest actually protects", () => {
    const manifest = fs.readFileSync(AUDIO_MANIFEST, "utf8");
    AUDIO_FROZEN.forEach((rel) => {
      // If the audio lock is ever lifted from one of these, this fails and the
      // inference inside it has to be fixed like any other.
      expect({ rel, protectedPath: manifest.includes(`"${rel}"`) }).toEqual({
        rel,
        protectedPath: true
      });
    });
  });

  it("exempts only files that really do still violate Rule A", () => {
    // If one of these is cleaned up or deleted, it must come off the list. An
    // exemption for a file that no longer needs it is a hole nobody is watching.
    AUDIO_FROZEN.forEach((rel) => {
      const body = stripComments(fs.readFileSync(path.join(REPO_ROOT, rel), "utf8"));
      const violates = body
        .split("\n")
        .some((line) => inferenceByArray(line) || inferenceBySubstring(line));
      expect({ rel, violates }).toEqual({ rel, violates: true });
    });
  });

  it("does not grow", () => {
    // Two files, both pre-existing. A third would mean this mission started
    // using the audio lock as a place to hide new debt.
    expect(AUDIO_FROZEN).toHaveLength(2);
  });
});

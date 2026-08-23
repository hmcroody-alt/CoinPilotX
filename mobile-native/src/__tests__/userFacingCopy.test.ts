/**
 * The developer-vocabulary gate.
 *
 * Tier 0.3 began as a copy fix for the Verification Center, where a seller was
 * told their records "stay server-authoritative through existing PulseSoc
 * verification systems" and shown the literal string
 * "Requests use `/api/dashboard/account/verification/request`". The audit that
 * followed found the same vocabulary on more than forty other files, so the fix
 * was widened from one screen to the class.
 *
 * A class of defect cannot be closed by fixing its instances — the instances
 * come back. So this is a test rather than a style note. It reads the source,
 * pulls out every string literal that reads as prose, and fails if any of them
 * names a server, a route, an endpoint, a payload or who owns a record. The
 * governing principle is the one Tier 0.1 established: a prose reminder is what
 * failed; an assertion is what replaces it.
 *
 * WHAT COUNTS AS PROSE
 * The detector is deliberately loose about what it flags and strict about what
 * it excuses, because a false positive costs somebody thirty seconds and a
 * false negative ships. Interpolations are stripped before matching, so an
 * identifier like `${normalized}` inside a template literal is not mistaken for
 * the word "normalize" in a sentence.
 *
 * WHAT IS EXEMPT, AND WHY EACH ONE IS SAFE
 * Every exemption below is structural — something about *where* the string
 * lives, not a list of strings somebody decided to forgive. A list of forgiven
 * strings would grow; these cannot.
 */
import { readdirSync, readFileSync, statSync } from "fs";
import { extname, join, relative } from "path";

const SRC = join(__dirname, "..");

/** The vocabulary. Each of these appeared in shipped, rendered copy. */
const BANNED: Array<[string, RegExp]> = [
  ["server-authoritative", /\bserver[- ]authoritative\b/i],
  ["server-owned", /\bserver[- ]owned\b/i],
  ["server-side", /\bserver[- ]side\b/i],
  ["backend", /\bbackend\b/i],
  ["endpoint", /\bendpoints?\b/i],
  ["API", /\bAPIs?\b/],
  ["an /api/ path", /\/api\//],
  ["provider-backed", /\bprovider[- ]backed\b/i],
  ["provider boundary", /\bprovider boundar(y|ies)\b/i],
  ["contract boundary", /\bcontract boundar(y|ies)\b/i],
  ["normalize", /\bnormaliz\w+\b/i],
  ["payload", /\bpayloads?\b/i],
  ["schema", /\bschemas?\b/i],
  ["mutation", /\bmutations?\b/i],
  ['"native does not…"', /\bnative does not\b/i]
];

/**
 * Whole files whose audience is not the person using the app.
 *
 * Kept to an explicit list of one, and each entry has to earn it by being a
 * surface a normal user cannot reach. Developer Settings is behind a developer
 * build; telling a developer that a toggle logs "every API request's route,
 * method, and duration" is the correct words for the correct reader.
 */
const NON_USER_SURFACES = ["screens/settings/DeveloperSettingsScreen.tsx"];

/**
 * Object fields that hold engineering notes rather than copy.
 *
 * These are the MOCK-DATA gap constants — `ACTIVITY_MOCK_DATA_GAPS`,
 * `INBOX_MOCK_DATA_GAPS`, `ORDERS_MOCK_DATA_GAPS`, `MARKETPLACE_MOCK_DATA_GAPS`
 * and their siblings. Their `field` is rendered nowhere; their `backendWork`,
 * `needs`, `perspective` and `gatedBy` describe work for whoever picks the gap
 * up. The verdict record quotes one of them approvingly as the implementation
 * spec for Tier 0.4, so this vocabulary is not merely tolerated there — it is
 * the point of the field.
 *
 * The discriminator is structural: these notes live under a field name that no
 * component reads. Anything under `label`, `detail`, `title`, `body`, `message`
 * or the like is copy and is not exempt, which is why `api/premium.ts` was
 * caught and `api/commerceInbox.ts` was not.
 */
const INTERNAL_NOTE_FIELDS = /\b(needs|backendWork|serverWork|perspective|rationale|why|gatedBy|todo|blockedBy)\s*:\s*$/;

/** Console output is read in a terminal by whoever is debugging. */
const CONSOLE_CALL = /console\.(log|warn|error|info|debug)\s*\(/;

/**
 * Fixture message bodies for simulator QA. `qaMessages` only answers for
 * conversation ids 9001–9006, which no real conversation has; the strings are
 * pretend messages from pretend people, not app copy.
 */
const FIXTURE_BUILDER = /\bqaMessage\s*\(/;

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      if (name === "node_modules" || name === "__tests__") continue;
      sourceFiles(full, out);
      continue;
    }
    if (![".ts", ".tsx"].includes(extname(name))) continue;
    if (/\.test\.tsx?$/.test(name)) continue;
    out.push(full);
  }
  return out;
}

/**
 * Prose, as opposed to a path, a key, an identifier or a class name. Four words
 * and twenty characters is the threshold at which a string starts reading as
 * something addressed to a person.
 */
function isProse(value: string) {
  const text = value.replace(/\$\{[^}]*\}/g, " ").trim();
  if (text.length < 20) return false;
  if (/^[/.#@]/.test(text)) return false;
  if (/^https?:/.test(text)) return false;
  if (text.split(/\s+/).length < 4) return false;
  if (/^[a-z0-9_.]+$/.test(text)) return false;
  return true;
}

const STRING_PATTERNS = [/"([^"\\]*(?:\\.[^"\\]*)*)"/g, /'([^'\\]*(?:\\.[^'\\]*)*)'/g, /`([^`\\]*(?:\\.[^`\\]*)*)`/g];

/**
 * Bare JSX text children — `<Text style={styles.muted}>Some sentence.</Text>`.
 *
 * The string patterns above only see quoted literals, so for a year this gate
 * read right past the most direct way there is to render a sentence. Forty-one
 * shipped violations were sitting in that gap, on the same screens the gate was
 * written to protect.
 *
 * The match runs from an opening tag's `>` to a closing tag's `</`, and both
 * halves of that are load-bearing. Requiring `</` is what keeps TypeScript
 * generics out: `postUndxAction<T>(path: string, payload: Record<string,
 * unknown>)` puts a `>` and a `<` around the words "path" and "payload", and
 * reads as a finding for the word `payload` if you stop at the bare `<`. A type
 * argument is never followed by a closing tag, so the `/` is the discriminator
 * — structural, like every other exemption here, rather than a note that says
 * to forgive `undxActions.ts`.
 *
 * Interpolated children are skipped rather than stitched, because `{` and `}`
 * are outside the character class. `<Text>Case #{ticket.id}</Text>` is not
 * inspected. That is the same bias the rest of the file takes: the detector
 * would rather look at less than guess at what a fragment says.
 *
 * Matched against the whole file, not line by line, because a long sentence is
 * usually the thing that got wrapped onto its own line — two of the findings
 * were written that way.
 */
const JSX_TEXT_CHILD = />([^<>{}]+)<\//g;

type Finding = { file: string; line: number; term: string; text: string };

function scan(): Finding[] {
  const findings: Finding[] = [];
  for (const file of sourceFiles(SRC)) {
    const rel = relative(SRC, file).split("\\").join("/");
    if (NON_USER_SURFACES.includes(rel)) continue;
    const source = readFileSync(file, "utf8");
    const lines = source.split("\n");
    lines.forEach((line, index) => {
      const trimmed = line.trim();
      if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
      if (CONSOLE_CALL.test(line) || FIXTURE_BUILDER.test(line)) return;
      // A note field, or a console call, may put its value on the next line.
      const previous = (lines[index - 1] || "").trim();
      if (INTERNAL_NOTE_FIELDS.test(previous) || CONSOLE_CALL.test(previous)) return;
      for (const pattern of STRING_PATTERNS) {
        for (const match of line.matchAll(pattern)) {
          const before = line.slice(0, match.index);
          if (INTERNAL_NOTE_FIELDS.test(before)) continue;
          const text = match[1];
          if (!isProse(text)) continue;
          const hit = BANNED.find(([, expression]) => expression.test(text.replace(/\$\{[^}]*\}/g, " ")));
          if (hit) findings.push({ file: rel, line: index + 1, term: hit[0], text });
        }
      }
    });
    for (const match of source.matchAll(JSX_TEXT_CHILD)) {
      const text = match[1].replace(/\s+/g, " ").trim();
      if (!isProse(text)) continue;
      const line = source.slice(0, match.index).split("\n").length;
      // A `<Text>` written out inside a comment is documentation, not copy.
      if (/^\s*(\/\/|\*|\/\*)/.test(lines[line - 1] || "")) continue;
      const hit = BANNED.find(([, expression]) => expression.test(text.replace(/\$\{[^}]*\}/g, " ")));
      if (hit) findings.push({ file: rel, line, term: hit[0], text });
    }
  }
  return findings;
}

describe("user-facing copy", () => {
  const findings = scan();

  /** If the walker stops finding files, everything below passes vacuously. */
  it("is reading the source it is guarding", () => {
    expect(sourceFiles(SRC).length).toBeGreaterThan(200);
    expect(scan.toString()).toContain("BANNED");
    // Both passes have to stay wired in. Dropping either one is silent.
    expect(scan.toString()).toContain("STRING_PATTERNS");
    expect(scan.toString()).toContain("JSX_TEXT_CHILD");
  });

  it("never says server, endpoint, payload or who owns a record", () => {
    const report = findings.map((f) => `${f.file}:${f.line} says "${f.term}" — ${f.text}`);
    expect(report).toEqual([]);
  });

  /**
   * The exemptions have to keep working, and they have to keep being narrow.
   * If someone widens `INTERNAL_NOTE_FIELDS` to something like `detail`, the
   * gate would go quiet while the defect returned, so the field list is pinned.
   */
  it("exempts engineering notes and nothing that is rendered", () => {
    expect(INTERNAL_NOTE_FIELDS.test("    backendWork:")).toBe(true);
    expect(INTERNAL_NOTE_FIELDS.test("    gatedBy:")).toBe(true);
    for (const rendered of ["detail:", "label:", "title:", "body:", "message:", "subtitle:", "placeholder:"]) {
      expect(INTERNAL_NOTE_FIELDS.test(`    ${rendered}`)).toBe(false);
    }
    expect(NON_USER_SURFACES).toEqual(["screens/settings/DeveloperSettingsScreen.tsx"]);
  });

  /**
   * The detector itself. `${normalized}` is an identifier, not the word
   * "normalize" in a sentence, and reading it as one produced a false positive
   * on the signup email step during the audit.
   */
  it("reads interpolations as values rather than as words", () => {
    const line = "Confirmation link sent to ${normalized}.";
    // Still prose — it is a sentence shown to somebody signing up.
    expect(isProse(line)).toBe(true);
    // But `normalized` is a variable name, not the word in a sentence, so
    // nothing here is a finding.
    expect(BANNED.some(([, re]) => re.test(line.replace(/\$\{[^}]*\}/g, " ")))).toBe(false);
    // The same word written out really is a finding.
    expect(BANNED.some(([, re]) => re.test("We normalized the response before showing it."))).toBe(true);
  });

  /**
   * The gap this pass was added to close. A sentence written straight into a
   * `<Text>` is not quoted, so the string patterns never saw it — which is how
   * forty-one of these shipped. The last two cases are the reason the pattern
   * insists on a closing tag rather than any `<`.
   */
  it("reads a bare JSX text child, and does not read a type argument", () => {
    const child = '<Text style={styles.muted}>Reports, tickets, scans, and moderation stay server-authoritative.</Text>';
    const [caught] = [...child.matchAll(JSX_TEXT_CHILD)].map((match) => match[1]);
    expect(caught).toBe("Reports, tickets, scans, and moderation stay server-authoritative.");
    expect(isProse(caught)).toBe(true);
    expect(BANNED.filter(([, re]) => re.test(caught)).map(([name]) => name)).toContain("server-authoritative");

    // Wrapped onto its own line, which is how the longer sentences were written.
    const wrapped = "<Text style={styles.bodyText}>\n  Loading server-authoritative dashboard state.\n</Text>";
    const [wrappedText] = [...wrapped.matchAll(JSX_TEXT_CHILD)].map((match) => match[1].replace(/\s+/g, " ").trim());
    expect(BANNED.some(([, re]) => re.test(wrappedText))).toBe(true);

    // A generic puts `>` and `<` either side of the word "payload". It is a
    // type annotation, not a rendered sentence, and no closing tag follows it.
    const generic = "function postUndxAction<T>(path: string, payload: Record<string, unknown>) {";
    expect([...generic.matchAll(JSX_TEXT_CHILD)]).toEqual([]);
  });

  it("still recognises the copy it was built to catch", () => {
    const original = "Badge, identity, and document review stay server-authoritative through existing PulseSoc verification systems.";
    expect(isProse(original)).toBe(true);
    expect(BANNED.filter(([, re]) => re.test(original)).map(([name]) => name)).toContain("server-authoritative");
    expect(BANNED.some(([, re]) => re.test("Requests use `/api/dashboard/account/verification/request`."))).toBe(true);
  });
});

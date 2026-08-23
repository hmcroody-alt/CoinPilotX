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
 * pulls out everything that reads as prose, and fails if any of it names a
 * server, a route, an endpoint, a payload or who owns a record. The governing
 * principle is the one Tier 0.1 established: a prose reminder is what failed;
 * an assertion is what replaces it.
 *
 * TWO KINDS OF TEXT, TWO BARS
 * A quoted string literal might be copy, but it might equally be a key, a
 * filename, a generated id or a status code, so it has to clear four words and
 * twenty characters before it is read as prose. That bar put short titles and
 * kickers structurally out of reach: "Native gateway" is two words and
 * fourteen characters, and no amount of vocabulary would ever have caught it.
 *
 * A bare JSX text child does not have that ambiguity. `<Text>Native
 * gateway</Text>` has already been placed on screen by the renderer; there is
 * no other role it could be playing. So rendered text is held to a much lower
 * bar — three characters, starting with a letter — and that is what makes the
 * short strings reachable. The looseness is paid for structurally rather than
 * by a list of exceptions.
 *
 * Interpolations are stripped before matching in both cases, so an identifier
 * like `${normalized}` is not mistaken for the word "normalize" in a sentence.
 *
 * WHAT IS EXEMPT, AND WHY EACH ONE IS SAFE
 * Every exemption below is structural — something about *where* the string
 * lives, not a list of strings somebody decided to forgive. A list of forgiven
 * strings would grow; these cannot.
 */
import { readdirSync, readFileSync, statSync } from "fs";
import { extname, join, relative } from "path";

const SRC = join(__dirname, "..");

/**
 * The vocabulary. Each of these appeared in shipped, rendered copy.
 *
 * "native" is the load-bearing one and the one that took the most care. It is
 * how this codebase refers to itself internally — the app is the native client,
 * as against the web app — so it is everywhere in identifiers, imports and
 * comments, and none of that is a defect. What makes it bannable anyway is that
 * *to the reader* it is meaningless: someone using PulseSoc on their phone has
 * no other PulseSoc to contrast it with, so "Native alerts" says nothing that
 * "Alerts" does not. The word is only excluded where it is read, never where it
 * is written, and comments are already skipped. The one carve-out is the proper
 * noun: "React Native" names a framework and is legitimate in the legal and
 * licensing copy that has to name it.
 *
 * `authority` is deliberately NOT banned outright. "data protection authority"
 * is the correct and legally required phrase in the GDPR copy, so only the
 * constructions that name who holds power over a record are excluded.
 */
const BANNED: Array<[string, RegExp]> = [
  ["server-authoritative", /\bserver[- ]authoritative\b/i],
  ["server-owned", /\bserver[- ]owned\b/i],
  ["server-side", /\bserver[- ]side\b/i],
  ["server-managed", /\bserver[- ]managed\b/i],
  ["server-authorized", /\bserver[- ]authoriz\w+\b/i],
  ["backend", /\bbackend\b/i],
  ["endpoint", /\bendpoints?\b/i],
  ["API", /\bAPIs?\b/],
  ["an /api/ path", /\/api\//],
  ["provider-backed", /\bprovider[- ]backed\b/i],
  ["provider-owned", /\bprovider[- ]owned\b/i],
  ["provider boundary", /\bprovider boundar(y|ies)\b/i],
  ["contract boundary", /\bcontract boundar(y|ies)\b/i],
  ["authority boundary", /\bauthority boundar(y|ies)\b/i],
  ["authoritative", /\bauthoritative\b/i],
  ["who holds authority", /\b(server|backend|provider|contract|native)\s+authority\b/i],
  ["gateway", /\bgateways?\b/i],
  ["native", /(?<!\breact[ -])\bnative(ly)?\b/i],
  ["normalize", /\bnormaliz\w+\b/i],
  ["payload", /\bpayloads?\b/i],
  ["schema", /\bschemas?\b/i],
  ["mutation", /\bmutations?\b/i]
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
 * Files the real-time-audio policy holds, deferred rather than excused.
 *
 * These have the same copy defects as everything else. What stops this gate
 * from covering them is that `docs/realtime_audio_change_policy.md` forbids a
 * mission whose subject is not real-time audio from editing any protected path
 * — a copy fix in one of these needs an audio change declaration, an audible
 * regression test on a device, and CODEOWNERS approval, and none of that is in
 * scope for a vocabulary sweep. Turning the gate on for them without doing that
 * work would leave a red test that the next person is tempted to fix by editing
 * a protected file.
 *
 * The set is read from the manifest rather than copied, so it tracks the policy
 * instead of drifting from it, and it is derived from the manifest's own
 * `unrelated_mission_policy` rule. Notably this is narrow: ChatScreen and
 * MusicScreen appear elsewhere in that file only as legacy `expo-av` call-site
 * allowances, which is not a protection, so they stay gated here.
 */
const AUDIO_MANIFEST = join(__dirname, "..", "..", "..", "config", "realtime-audio-protected-paths.json");

function audioProtectedPaths(): Set<string> {
  const manifest = JSON.parse(readFileSync(AUDIO_MANIFEST, "utf8"));
  const paths = new Set<string>();
  for (const category of manifest.categories ?? []) {
    for (const path of category.paths ?? []) paths.add(path);
  }
  for (const path of manifest.dependency_watch?.files ?? []) paths.add(path);
  for (const path of manifest.import_boundary?.allowed_importers ?? []) paths.add(path);
  return paths;
}

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

/**
 * A note long enough to be written as several concatenated strings is still one
 * note. Checking only the immediately preceding line exempted the first line of
 * `paymentsHub`'s `why:` and then flagged its third. The walk stops at the
 * first line that is not a `+` continuation, so it cannot wander out of the
 * expression it started in.
 */
function insideInternalNote(lines: string[], index: number) {
  let cursor = index - 1;
  while (cursor >= 0 && /\+\s*$/.test(lines[cursor].trim())) cursor -= 1;
  return cursor >= 0 && INTERNAL_NOTE_FIELDS.test(lines[cursor].trim());
}

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

const strip = (value: string) => value.replace(/\$\{[^}]*\}/g, " ").trim();

/**
 * Prose, as opposed to a path, a key, an identifier or a class name. Four words
 * and twenty characters is the threshold at which a *quoted string* starts
 * reading as something addressed to a person. Lowering it was measured and
 * rejected: at two words it starts flagging generated ids, filenames and status
 * code values, which are strings that happen to contain the vocabulary rather
 * than copy that uses it.
 */
function isProse(value: string) {
  const text = strip(value);
  if (text.length < 20) return false;
  if (/^[/.#@]/.test(text)) return false;
  if (/^https?:/.test(text)) return false;
  if (text.split(/\s+/).length < 4) return false;
  if (/^[a-z0-9_.]+$/.test(text)) return false;
  return true;
}

/**
 * The bar for a bare JSX text child, which is lower because the ambiguity that
 * justifies the higher one is absent — this text is on screen by construction.
 * What is left to exclude is not copy-versus-key but text-versus-syntax: the
 * naive `>...<` match also spans TypeScript generics like `Record<string,
 * unknown>` and JSX prop tails. Requiring a leading letter and rejecting the
 * operators that only appear in code removes those.
 */
function isRenderedText(value: string) {
  const text = value.trim();
  if (text.length < 3) return false;
  if (!/^[A-Za-z]/.test(text)) return false;
  if (/=/.test(text)) return false;
  if (/=>|\|\||&&/.test(text)) return false;
  if (!/[A-Za-z]{2}/.test(text)) return false;
  return true;
}

const STRING_PATTERNS = [/"([^"\\]*(?:\\.[^"\\]*)*)"/g, /'([^'\\]*(?:\\.[^'\\]*)*)'/g, /`([^`\\]*(?:\\.[^`\\]*)*)`/g];

/**
 * Text between a closing `>` and the next `<`. Interpolations are removed
 * first, so `Opening {targetRoute}.` is read as the words around the value
 * rather than being split at the brace and lost.
 */
const JSX_TEXT = />([^<>]+)</g;

type Finding = { file: string; line: number; term: string; text: string };

function scan(): Finding[] {
  const findings: Finding[] = [];
  const deferred = audioProtectedPaths();
  for (const file of sourceFiles(SRC)) {
    const rel = relative(SRC, file).split("\\").join("/");
    if (NON_USER_SURFACES.includes(rel)) continue;
    if (deferred.has(`mobile-native/src/${rel}`)) continue;
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((line, index) => {
      const trimmed = line.trim();
      if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
      if (CONSOLE_CALL.test(line) || FIXTURE_BUILDER.test(line)) return;
      // A note field, or a console call, may put its value on the next line.
      if (insideInternalNote(lines, index) || CONSOLE_CALL.test((lines[index - 1] || "").trim())) return;
      for (const pattern of STRING_PATTERNS) {
        for (const match of line.matchAll(pattern)) {
          const before = line.slice(0, match.index);
          if (INTERNAL_NOTE_FIELDS.test(before)) continue;
          const text = match[1];
          if (!isProse(text)) continue;
          const hit = BANNED.find(([, expression]) => expression.test(strip(text)));
          if (hit) findings.push({ file: rel, line: index + 1, term: hit[0], text });
        }
      }
      for (const match of line.replace(/\{[^{}]*\}/g, " ").matchAll(JSX_TEXT)) {
        const text = match[1].trim();
        if (!isRenderedText(text)) continue;
        const hit = BANNED.find(([, expression]) => expression.test(text));
        if (hit) findings.push({ file: rel, line: index + 1, term: hit[0], text });
      }
    });
  }
  return findings;
}

describe("user-facing copy", () => {
  const findings = scan();

  /** If the walker stops finding files, everything below passes vacuously. */
  it("is reading the source it is guarding", () => {
    expect(sourceFiles(SRC).length).toBeGreaterThan(200);
    expect(scan.toString()).toContain("BANNED");
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
   * The audio deferral is the widest exemption here, so it is pinned to the
   * manifest that justifies it. If someone adds a screen to the protected list
   * for an unrelated reason it silently leaves this gate, and that should be a
   * deliberate act rather than a side effect — hence the count.
   */
  it("defers only files on the real-time audio surface", () => {
    const deferred = audioProtectedPaths();
    const skipped = sourceFiles(SRC)
      .map((file) => `mobile-native/src/${relative(SRC, file).split("\\").join("/")}`)
      .filter((path) => deferred.has(path));

    // Every deferred file is part of calls, Live, or the audio engine itself.
    // A screen unrelated to real-time audio appearing here would mean the
    // manifest had grown a reason this gate never agreed to.
    const surface = /^mobile-native\/src\/(calls|live|live-audio|core)\/|^mobile-native\/src\/api\/(calls|live)\.ts$|^mobile-native\/src\/components\/reels\/ReelLiveViewerSurface\.tsx$|^mobile-native\/src\/screens\/(CallScreen|LiveScreen|LiveHostSessionScreen)\.tsx$/;
    expect(skipped.filter((path) => !surface.test(path))).toEqual([]);
    expect(skipped.length).toBe(30);

    // Legacy expo-av call sites are an allowance, not a protection, so the
    // ordinary screens that hold one stay covered.
    expect(deferred.has("mobile-native/src/screens/ChatScreen.tsx")).toBe(false);
    expect(deferred.has("mobile-native/src/screens/MusicScreen.tsx")).toBe(false);
  });

  /**
   * The lower bar for rendered text is the thing that makes short titles
   * reachable, and it is also the thing most likely to start flagging syntax.
   */
  it("reads bare JSX text at a lower bar than a quoted string", () => {
    // The case the four-word bar could never reach.
    expect(isProse("Native gateway")).toBe(false);
    expect(isRenderedText("Native gateway")).toBe(true);
    expect(BANNED.some(([, re]) => re.test("Native gateway"))).toBe(true);
    // Generics and prop tails run through the same `>...<` match and must not.
    // The first is what the span between one generic's `>` and the next `<`
    // actually looks like: `(path: string, payload: Record<...>)`.
    for (const syntax of ["(path: string, payload: Record", "= true", "value => value", "open && ready"]) {
      expect(isRenderedText(syntax)).toBe(false);
    }
  });

  /** "React Native" is a framework's name, and the licensing copy has to say it. */
  it("bans native as a description but not as a proper noun", () => {
    const native = BANNED.find(([name]) => name === "native")![1];
    expect(native.test("Native alerts")).toBe(true);
    expect(native.test("This screen renders natively.")).toBe(true);
    expect(native.test("Built with React Native.")).toBe(false);
  });

  /** A note written as several concatenated strings is still one note. */
  it("exempts every line of a multi-line engineering note", () => {
    const note = ["    why:", '      "denied by a " +', '      "native-iOS 403.",'];
    expect(insideInternalNote(note, 1)).toBe(true);
    expect(insideInternalNote(note, 2)).toBe(true);
    // And it stops at the end of the expression rather than running on.
    expect(insideInternalNote([...note, '    label: "Native alerts"'], 3)).toBe(false);
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

  it("still recognises the copy it was built to catch", () => {
    const original = "Badge, identity, and document review stay server-authoritative through existing PulseSoc verification systems.";
    expect(isProse(original)).toBe(true);
    expect(BANNED.filter(([, re]) => re.test(original)).map(([name]) => name)).toContain("server-authoritative");
    expect(BANNED.some(([, re]) => re.test("Requests use `/api/dashboard/account/verification/request`."))).toBe(true);
  });
});

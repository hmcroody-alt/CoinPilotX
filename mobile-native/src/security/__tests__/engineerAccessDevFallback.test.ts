import { readFileSync } from "fs";
import { join } from "path";

/**
 * The development fallback resolves its enablement once, at module load, from
 * build-time constants. Every test that cares about a *different* build must
 * therefore re-require the module under different globals rather than calling a
 * setter — there is deliberately no setter, because a runtime switch is exactly
 * the thing that could be flipped in a shipped app.
 */
function loadFallback(options: { dev: boolean; flag?: string }) {
  const previousDev = (global as { __DEV__?: boolean }).__DEV__;
  const previousFlag = process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK;
  (global as { __DEV__?: boolean }).__DEV__ = options.dev;
  if (options.flag === undefined) delete process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK;
  else process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK = options.flag;

  let loaded!: typeof import("../engineerAccessDevFallback");
  jest.isolateModules(() => {
    loaded = require("../engineerAccessDevFallback");
  });

  (global as { __DEV__?: boolean }).__DEV__ = previousDev;
  if (previousFlag === undefined) delete process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK;
  else process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK = previousFlag;
  return loaded;
}

const CORRECT = "70041852";

describe("engineerAccessDevFallback — development build", () => {
  const fallback = () => loadFallback({ dev: true });

  it("is compiled in", () => {
    expect(fallback().engineerDevFallbackEnabled()).toBe(true);
  });

  it("accepts the exact passcode", () => {
    expect(fallback().devFallbackAccepts(CORRECT)).toBe(true);
  });

  it.each(["  70041852", "70041852  ", "\t70041852\n", " 70041852 "])(
    "accepts %j once trimmed",
    (entered) => {
      expect(fallback().devFallbackAccepts(entered)).toBe(true);
    }
  );

  it.each(["70041853", "07004185", "12345678", "700418520", "7004185"])(
    "rejects the incorrect value %j",
    (entered) => {
      expect(fallback().devFallbackAccepts(entered)).toBe(false);
    }
  );

  it.each(["", "7", "7004185", "700418 52"])("rejects the incomplete input %j", (entered) => {
    expect(fallback().devFallbackAccepts(entered)).toBe(false);
  });

  it.each([null, undefined, {}, [], NaN])("returns false rather than throwing for %p", (entered) => {
    // A malformed value must not take the error path, where it could be
    // mistaken for a server outage.
    expect(() => fallback().devFallbackAccepts(entered)).not.toThrow();
    expect(fallback().devFallbackAccepts(entered)).toBe(false);
  });

  it("issues a grant no longer than the server ceiling", () => {
    expect(fallback().LOCAL_GRANT_TTL_SECONDS).toBeLessThanOrEqual(1800);
  });

  it("marks its scope so a screen can tell it from a real capability", () => {
    expect(fallback().LOCAL_GRANT_SCOPE).toContain("local_dev");
  });
});

describe("engineerAccessDevFallback — public production build", () => {
  it("is absent when neither __DEV__ nor the opt-in flag is set", () => {
    const fallback = loadFallback({ dev: false });
    expect(fallback.engineerDevFallbackEnabled()).toBe(false);
  });

  it("rejects the correct passcode outright", () => {
    // The failure this guards: a production IPA in which the interim passcode
    // still opens Business OS. The comparison is never reached.
    const fallback = loadFallback({ dev: false });
    expect(fallback.devFallbackAccepts(CORRECT)).toBe(false);
  });

  it.each(["", "0", "false", "off", "no"])(
    "stays absent for the non-truthy flag value %j",
    (flag) => {
      const fallback = loadFallback({ dev: false, flag });
      expect(fallback.engineerDevFallbackEnabled()).toBe(false);
      expect(fallback.devFallbackAccepts(CORRECT)).toBe(false);
    }
  );

  it.each(["1", "true", "on", "yes"])("is compiled in for the opt-in value %j", (flag) => {
    const fallback = loadFallback({ dev: false, flag });
    expect(fallback.engineerDevFallbackEnabled()).toBe(true);
    expect(fallback.devFallbackAccepts(CORRECT)).toBe(true);
  });
});

describe("engineerAccessDevFallback — build-time containment", () => {
  // Comments stripped: the doc block deliberately *names* the computed form it
  // is warning against, and that mention must not read as a violation.
  const source = readFileSync(join(__dirname, "..", "engineerAccessDevFallback.ts"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");

  it("reads the opt-in flag as a statically spelled member expression", () => {
    // Expo's babel plugin only inlines process.env.EXPO_PUBLIC_X when the key is
    // a string literal. A computed lookup reads undefined on device, which would
    // silently make the flag dead — the defect that motivated this spelling.
    expect(source).toContain("process.env.EXPO_PUBLIC_ENGINEER_LOCAL_FALLBACK");
    expect(source).not.toMatch(/process\.env\s*\[/);
  });

  it("resolves enablement once, with no runtime switch to flip", () => {
    expect(source).not.toMatch(/export function set|ENABLED\s*=\s*[^:]*;[\s\S]*ENABLED\s*=/);
  });

  it("requires __DEV__ or the opt-in flag, never a bare true", () => {
    expect(source).toMatch(/__DEV__ === true \|\| isFlagValueOn\(/);
  });
});

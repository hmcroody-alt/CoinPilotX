/**
 * The real-time audio boundary, enforced.
 *
 * The verified-working audio foundation depends on one fact: exactly one module
 * touches the device audio session, and exactly one module publishes the
 * microphone. Nothing in a screenshot, a type signature, or a code review
 * reliably preserves that. A single `Audio.setAudioModeAsync` added to an
 * unrelated media screen can take the session away from a live call, and the
 * symptom is silence in production, not a failing build.
 *
 * So the rules live in `config/realtime-audio-protected-paths.json` and are read
 * from there by this test and by
 * `tests/protection/test_realtime_audio_architecture.py`. Both readers use the
 * same manifest on purpose: the rules cannot drift from what CI enforces,
 * because there is only one copy of them.
 *
 * Every allowlist here is an explicit list of files. There is deliberately no
 * wildcard covering a directory — a directory allowlist would let a new file
 * dropped into `core/` bypass the whole boundary on the day it is created.
 */
import fs from "fs";
import path from "path";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const MANIFEST_PATH = path.join(REPO_ROOT, "config", "realtime-audio-protected-paths.json");
const NATIVE_SRC = path.join(REPO_ROOT, "mobile-native", "src");

type ForbiddenRule = {
  id: string;
  title: string;
  markers: string[];
  allowed_paths: string[];
  reason: string;
  frozen_at_baseline?: boolean;
  max_allowed_paths?: number;
};

type Manifest = {
  manifest_version: number;
  categories: { id: string; paths: string[]; also_enforced_in?: string[] }[];
  forbidden_apis: ForbiddenRule[];
  import_boundary: {
    reason: string;
    modules: string[];
    allowed_importers: string[];
  };
  required_lease_discipline: {
    files: string[];
    must_contain: string[];
    must_not_contain: string[];
  };
  required_output_enable_discipline: {
    files: string[];
    must_contain: string[];
    patch_files: string[];
    patch_must_contain: string[];
  };
  dependency_watch: {
    must_be_exactly_pinned: string[];
    baseline_versions: Record<string, string>;
  };
};

const manifest: Manifest = JSON.parse(fs.readFileSync(MANIFEST_PATH, "utf8"));

/** Every .ts/.tsx under mobile-native/src, excluding test files. */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      // Tests are excluded because they legitimately name the forbidden APIs in
      // order to mock or assert on them. This file is itself under __tests__,
      // so the manifest's own marker strings never trip the scan.
      if (entry.name === "__tests__" || entry.name === "node_modules") continue;
      sourceFiles(full, out);
    } else if (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx")) {
      out.push(full);
    }
  }
  return out;
}

const ALL_SOURCES = sourceFiles(NATIVE_SRC);
const relative = (abs: string) => path.relative(REPO_ROOT, abs).split(path.sep).join("/");

describe("real-time audio protected boundary", () => {
  it("reads a manifest that exists and names every rule it enforces", () => {
    expect(manifest.manifest_version).toBe(1);
    expect(manifest.forbidden_apis.length).toBeGreaterThanOrEqual(6);
    manifest.forbidden_apis.forEach((rule) => {
      // A rule with no markers enforces nothing; a rule with no reason cannot be
      // argued with in review. Both are treated as broken rules.
      expect(rule.markers.length).toBeGreaterThan(0);
      expect(rule.reason.length).toBeGreaterThan(0);
    });
  });

  it("points every protected path at a file that actually exists", () => {
    const missing: string[] = [];
    manifest.categories.forEach((category) => {
      category.paths.forEach((p) => {
        if (!fs.existsSync(path.join(REPO_ROOT, p))) missing.push(`${category.id} -> ${p}`);
      });
    });
    // A manifest entry pointing at a deleted file is worse than no entry: the
    // change gate silently stops protecting whatever replaced it.
    expect(missing).toEqual([]);
  });

  describe.each(manifest.forbidden_apis.map((rule) => [rule.id, rule] as const))(
    "%s",
    (_id, rule) => {
      it(`confines ${rule.title.toLowerCase()} to its allowlist`, () => {
        const allowed = new Set(rule.allowed_paths);
        const violations: string[] = [];

        for (const file of ALL_SOURCES) {
          const rel = relative(file);
          if (allowed.has(rel)) continue;
          const text = fs.readFileSync(file, "utf8");
          for (const marker of rule.markers) {
            if (text.includes(marker)) violations.push(`${rel} uses ${marker}`);
          }
        }

        // The message carries the reason so a developer who trips this learns
        // why the boundary exists instead of just how to silence the test.
        expect(violations.join("\n") + (violations.length ? `\n\n${rule.reason}` : "")).toBe("");
      });

      it("keeps the allowlist explicit and file-scoped", () => {
        rule.allowed_paths.forEach((p) => {
          expect(p).not.toContain("*");
          expect(p.endsWith(".ts") || p.endsWith(".tsx") || p.endsWith(".py")).toBe(true);
          expect(fs.existsSync(path.join(REPO_ROOT, p))).toBe(true);
        });
      });
    }
  );

  it("does not let the frozen expo-av allowlist grow", () => {
    const rule = manifest.forbidden_apis.find((r) => r.id === "expo_av_global_audio_mode");
    expect(rule).toBeDefined();
    // These six files already mutated the global audio mode at the verified
    // baseline. They are frozen rather than rewritten because this hard-lock
    // must not change working runtime behavior. A seventh entry means someone
    // widened the boundary instead of routing through an existing owner.
    expect(rule!.frozen_at_baseline).toBe(true);
    expect(rule!.allowed_paths.length).toBeLessThanOrEqual(rule!.max_allowed_paths ?? 6);
  });

  it("keeps the audio core reachable only from the approved adapters", () => {
    const { modules, allowed_importers, reason } = manifest.import_boundary;
    const allowed = new Set(allowed_importers);
    const violations: string[] = [];

    for (const file of ALL_SOURCES) {
      const rel = relative(file);
      if (allowed.has(rel)) continue;
      const text = fs.readFileSync(file, "utf8");
      for (const moduleName of modules) {
        // Match the module's last path segment in an import specifier, so a
        // relative path of any depth is caught: "./x", "../core/x",
        // "../../core/x". Requiring a fixed prefix would be trivially evaded
        // by moving the importing file one directory deeper.
        const leaf = moduleName.split("/").pop()!;
        const pattern = new RegExp(`from\\s+["'][^"']*\\b${leaf}["']`);
        if (pattern.test(text)) violations.push(`${rel} imports ${moduleName}`);
      }
    }

    expect(violations.join("\n") + (violations.length ? `\n\n${reason}` : "")).toBe("");
  });

  it("keeps every approved importer in the boundary a real file", () => {
    // A stale entry here is a hole: it names a file that no longer exists, and
    // the next file created at that path inherits an exemption nobody granted.
    manifest.import_boundary.allowed_importers.forEach((rel) => {
      expect([rel, fs.existsSync(path.join(REPO_ROOT, rel))]).toEqual([rel, true]);
    });
  });

  it("requires both room adapters to release audio by lease, not by owner name", () => {
    const discipline = manifest.required_lease_discipline;
    discipline.files.forEach((rel) => {
      const text = fs.readFileSync(path.join(REPO_ROOT, rel), "utf8");
      discipline.must_contain.forEach((needle) => {
        expect([rel, needle, text.includes(needle)]).toEqual([rel, needle, true]);
      });
      discipline.must_not_contain.forEach((needle) => {
        // `audioOwnerIdRef` is the pre-baseline owner-name pattern. Its return
        // means a delayed cleanup can once again release a session that a newer
        // feature has since acquired — the exact bug the lease generation fixed.
        expect([rel, needle, text.includes(needle)]).toEqual([rel, needle, false]);
      });
    });
  });

  it("keeps both public RTC adapters pinned to Agora", () => {
    const call = fs.readFileSync(path.join(REPO_ROOT, "mobile-native/src/calls/useNativeCallRoom.ts"), "utf8");
    expect(call).toContain('from "./useAgoraCallRoom"');
    expect(call).toContain("useAgoraCallRoom()");

    const live = fs.readFileSync(path.join(REPO_ROOT, "mobile-native/src/live/useLiveBroadcastRoom.ts"), "utf8");
    expect(live).toContain('from "./useAgoraLiveBroadcastRoom"');
    expect(live).toContain("useAgoraLiveBroadcastRoom(");

    expect(`${call}\n${live}`).not.toMatch(/livekit|registerGlobals|livekitClient/i);
  });

  // The other half of "sameness by copy". Comments and identifier names differ on
  // purpose - the copy is renamed - so this compares executable shape rather than
  // text: strip comments, fold the Realtime/Live naming, collapse whitespace. If
  // anyone reorders publication and camera startup on one side only, this fails
  // here instead of during a live broadcast.
  it("keeps the Live publisher copy step-for-step identical to the call original", () => {
    const strip = (text: string) =>
      text
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/\/\/.*$/gm, "")
        .replace(/[Rr]ealtime|[Ll]ive/g, "")
        .replace(/\s+/g, "");
    const original = strip(
      fs.readFileSync(path.join(REPO_ROOT, "mobile-native/src/core/realtimePublisherMedia.ts"), "utf8")
    );
    const copy = strip(
      fs.readFileSync(path.join(REPO_ROOT, "mobile-native/src/live-audio/livePublisherMedia.ts"), "utf8")
    );
    expect(copy).toEqual(original);
  });

  it("pins every audio-critical dependency to a version the baseline verified", () => {
    const pkg = JSON.parse(
      fs.readFileSync(path.join(REPO_ROOT, "mobile-native", "package.json"), "utf8")
    );
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    manifest.dependency_watch.must_be_exactly_pinned.forEach((name) => {
      const actual = deps[name];
      const expected = manifest.dependency_watch.baseline_versions[name];
      // Equality against the recorded baseline, not a range check: the point is
      // that the media stack cannot move without someone editing the manifest,
      // which the change gate then treats as a protected change.
      expect([name, actual]).toEqual([name, expected]);
    });
  });

  it("keeps the iOS microphone and background-audio configuration intact", () => {
    const app = JSON.parse(fs.readFileSync(path.join(REPO_ROOT, "mobile-native", "app.json"), "utf8"));
    const infoPlist = app.expo?.ios?.infoPlist ?? {};
    // Without the usage description iOS denies the microphone outright; without
    // the audio background mode a backgrounded call goes silent. Neither failure
    // is visible in a simulator run or a unit test of the audio engine.
    expect(typeof infoPlist.NSMicrophoneUsageDescription).toBe("string");
    expect(String(infoPlist.NSMicrophoneUsageDescription).length).toBeGreaterThan(0);
    expect(infoPlist.UIBackgroundModes).toContain("audio");
  });
});

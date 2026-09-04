/**
 * Stage 47 — the architecture rule as a test.
 *
 * The mission's first non-negotiable is that there is exactly one media engine
 * and that screens call it rather than owning media logic. That rule survives
 * review once and then decays: the next feature under deadline adds its own
 * `downloadAsync`, and nobody notices because the build is green and the feature
 * works. This suite is what notices.
 *
 * Each check names the one file allowed to hold the capability. Adding a second
 * owner fails here with a message that says which file to call instead — so the
 * failure is a signpost, not a puzzle.
 */
import { readdirSync, readFileSync, statSync } from "fs";
import { join, relative } from "path";

const SRC = join(__dirname, "..", "..");

function sourceFiles(): string[] {
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) {
        if (name === "node_modules") continue;
        walk(path);
        continue;
      }
      if (!/\.tsx?$/.test(name)) continue;
      // Tests may name a capability in order to assert about it.
      if (path.includes("__tests__") || /\.test\.tsx?$/.test(name)) continue;
      found.push(path);
    }
  };
  walk(SRC);
  return found;
}

const FILES = sourceFiles().map((path) => ({ path: relative(SRC, path), text: readFileSync(path, "utf8") }));

function owners(pattern: RegExp): string[] {
  return FILES.filter((file) => pattern.test(file.text)).map((file) => file.path).sort();
}

describe("single-owner capabilities", () => {
  it("only mediaDownloader downloads media to disk", () => {
    expect(owners(/\bcreateDownloadResumable\b/)).toEqual(["media/mediaDownloader.ts"]);
  });

  it("only mediaActions writes to the photo library", () => {
    expect(owners(/from ["']expo-media-library["']/)).toEqual(["media/mediaActions.ts"]);
    expect(owners(/\bsaveToLibraryAsync\b/)).toEqual(["media/mediaActions.ts"]);
  });

  it("only mediaActions opens the OS share sheet for a file", () => {
    expect(owners(/from ["']expo-sharing["']/)).toEqual(["media/mediaActions.ts"]);
  });

  it("only mediaCache owns the on-disk media cache layout", () => {
    expect(owners(/pulsesoc-media/)).toEqual(["media/mediaCache.ts"]);
  });

  it("only mediaCache decides where a cached media file lives", () => {
    expect(owners(/export function cacheFileUriFor/)).toEqual(["media/mediaCache.ts"]);
  });
});

describe("screens call the service, they do not own media logic", () => {
  const SCREEN_LIKE = FILES.filter((file) => /^(screens|components)\//.test(file.path));

  it("no screen or component fetches media bytes itself", () => {
    const offenders = SCREEN_LIKE.filter((file) => /\bcreateDownloadResumable\b|\bdownloadResumable\b/.test(file.text));
    expect(offenders.map((file) => file.path)).toEqual([]);
  });

  it("no screen or component implements its own save-to-gallery", () => {
    const offenders = SCREEN_LIKE.filter((file) => /expo-media-library|saveToLibraryAsync/.test(file.text));
    expect(offenders.map((file) => file.path)).toEqual([]);
  });

  it("no screen or component implements its own file share sheet", () => {
    const offenders = SCREEN_LIKE.filter((file) => /from ["']expo-sharing["']/.test(file.text));
    expect(offenders.map((file) => file.path)).toEqual([]);
  });
});

describe("media telemetry cannot leak private URLs", () => {
  it("the event type has no field that could carry one", () => {
    const telemetry = FILES.find((file) => file.path === "media/mediaTelemetry.ts");
    expect(telemetry).toBeDefined();
    const eventType = /export type MediaEvent = \{([\s\S]*?)\n\};/.exec(telemetry!.text)?.[1] || "";
    expect(eventType).not.toBe("");
    for (const field of ["url", "uri", "href", "caption", "body", "filename"]) {
      expect(eventType).not.toMatch(new RegExp(`\\b${field}\\??:`, "i"));
    }
  });
});

describe("the shared viewer is the media integration point", () => {
  it("delegates save and share to the shared actions module", () => {
    const viewer = FILES.find((file) => file.path === "components/NativeMediaViewer.tsx");
    expect(viewer).toBeDefined();
    expect(viewer!.text).toMatch(/from ["']\.\.\/media\/mediaActions["']/);
    expect(viewer!.text).toMatch(/saveMediaToGallery\(/);
    expect(viewer!.text).toMatch(/shareMedia\(/);
  });

  it("purges every account's cached media on sign-out", () => {
    const cleanup = FILES.find((file) => file.path === "media/mediaSessionCleanup.ts");
    expect(cleanup).toBeDefined();
    expect(cleanup!.text).toMatch(/clearAllMediaCaches\(\)/);
    expect(cleanup!.text).toMatch(/setMediaCacheScope\(null\)/);
  });

  it("scopes the cache from the single session-state constructor", () => {
    const auth = FILES.find((file) => file.path === "session/auth.ts");
    expect(auth).toBeDefined();
    expect(auth!.text).toMatch(/setMediaCacheScope\(/);
  });
});

describe("realtime audio stays untouched", () => {
  it("the media foundation never configures an audio session", () => {
    const foundation = FILES.filter((file) => /^media\/media(Cache|Downloader|Actions|Telemetry|SessionCleanup)\.ts$/.test(file.path));
    expect(foundation.length).toBe(5);
    for (const file of foundation) {
      expect(file.text).not.toMatch(/setAudioModeAsync|AVAudioSession|setCategory/);
    }
  });
});

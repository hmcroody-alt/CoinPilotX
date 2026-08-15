/**
 * What each spatial flag does when the build says nothing about it.
 *
 * ## Why this file exists
 *
 * The Reels horizontal pager and its full-screen navigator were reported as
 * repeatedly reverting. They never were: the code was present and correct in
 * every build that shipped without them. The flags were read with the
 * default-OFF rule, no EAS profile set them, no `.env` existed, and the repo's
 * `.gitignore` excludes `.env` and `.env.*` — so the feature was on only for a
 * build whose operator happened to export the variables by hand, and off for
 * every other build. Nothing failed. The build was green, the suite was green,
 * and the behaviour was simply absent.
 *
 * That is the failure this file is aimed at. It is deliberately a test of
 * *silence*: it clears the environment and the overrides and asks each accessor
 * what it does when nobody has said anything, which is the exact condition the
 * shipping builds were in. `flags.test.ts` covers the master/sub AND-ing and
 * the override plumbing; this one covers only the defaults, because the defaults
 * are what regressed and they are invisible everywhere else.
 *
 * If a flag's posture is meant to change, this file changes with it — in one
 * place, on purpose, with the reason written down. What must not happen again is
 * the posture changing because nobody noticed it was load-bearing.
 */
import {
  __clearSpatialFlagOverrides,
  immersiveNavigatorEnabled,
  messagesVisualRefreshEnabled,
  spatialConsoleEnabled,
  spatialCreateEnabled,
  spatialHomeFeedEnabled,
  spatialMotionEnabled,
  spatialReelsEnabled,
  tiltNavigationEnabled,
  tiltParallaxEnabled
} from "../flags";

/** Every variable the flags module reads, so "unset" can be made true. */
const SPATIAL_VARS = [
  "EXPO_PUBLIC_SPATIAL_CONSOLE",
  "EXPO_PUBLIC_SPATIAL_HOME_FEED",
  "EXPO_PUBLIC_SPATIAL_REELS",
  "EXPO_PUBLIC_SPATIAL_CREATE",
  "EXPO_PUBLIC_MESSAGES_VISUAL_REFRESH",
  "EXPO_PUBLIC_IMMERSIVE_NAVIGATOR",
  "EXPO_PUBLIC_SPATIAL_MOTION",
  "EXPO_PUBLIC_TILT_NAVIGATION",
  "EXPO_PUBLIC_TILT_PARALLAX"
] as const;

const saved: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const name of SPATIAL_VARS) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  __clearSpatialFlagOverrides();
});

afterEach(() => {
  for (const name of SPATIAL_VARS) {
    if (saved[name] === undefined) delete process.env[name];
    else process.env[name] = saved[name];
  }
  __clearSpatialFlagOverrides();
});

describe("a build that sets no spatial variables at all", () => {
  it("still ships Reels horizontal paging and the full-screen navigator", () => {
    // This is the assertion the regression would have tripped. Read it as: a
    // plain `xcodebuild` with an empty environment gets the shipped experience.
    expect(spatialConsoleEnabled()).toBe(true);
    expect(spatialReelsEnabled()).toBe(true);
    expect(immersiveNavigatorEnabled()).toBe(true);
  });

  it("does not switch on anything that is still rolling out", () => {
    // The master defaulting ON must not drag the unfinished surfaces with it.
    // Home in particular: the Home feed keeps its existing layout and gets no
    // spatial paging, which is a hard requirement of the Home spec.
    expect(spatialHomeFeedEnabled()).toBe(false);
    expect(spatialCreateEnabled()).toBe(false);
    expect(messagesVisualRefreshEnabled()).toBe(false);
  });

  it("subscribes no motion sensor and enables no tilt navigation", () => {
    // Tilt is optional, Reels-only, and must never drive the navigator. Off by
    // default means a default build subscribes nothing at all.
    expect(spatialMotionEnabled()).toBe(false);
    expect(tiltNavigationEnabled()).toBe(false);
    expect(tiltParallaxEnabled()).toBe(false);
  });
});

describe("rollback is still a flag flip and never a revert", () => {
  it.each(["0", "false", "off", "no", "OFF", "  false  "])(
    "turns Reels paging off for %p",
    value => {
      process.env.EXPO_PUBLIC_SPATIAL_REELS = value;
      expect(spatialReelsEnabled()).toBe(false);
      // Disabling one surface leaves the rest of the console alone.
      expect(spatialConsoleEnabled()).toBe(true);
      expect(immersiveNavigatorEnabled()).toBe(true);
    }
  );

  it("takes the whole console down from the master", () => {
    process.env.EXPO_PUBLIC_SPATIAL_CONSOLE = "0";
    expect(spatialConsoleEnabled()).toBe(false);
    expect(spatialReelsEnabled()).toBe(false);
    expect(immersiveNavigatorEnabled()).toBe(false);
  });

  it("does not accept a misspelled rollback", () => {
    // A shipped feature should not disappear because somebody typed "flase" in
    // a build profile. Turning it off has to be spelled correctly.
    process.env.EXPO_PUBLIC_SPATIAL_REELS = "flase";
    expect(spatialReelsEnabled()).toBe(true);
  });

  it("still honours an explicit on", () => {
    process.env.EXPO_PUBLIC_SPATIAL_REELS = "1";
    process.env.EXPO_PUBLIC_IMMERSIVE_NAVIGATOR = "true";
    expect(spatialReelsEnabled()).toBe(true);
    expect(immersiveNavigatorEnabled()).toBe(true);
  });
});

/**
 * Architecture guard: the Home Feed is a motion-free zone.
 *
 * ## Why this is a static scan and not a render test
 *
 * Spatial paging and phone-tilt navigation were built on the Home Feed, shipped
 * to physical-device testing, and then withdrawn: Reels is the only browsing
 * surface that gets the motion experience. The Feed went back to its original
 * vertical list.
 *
 * The risk that outlives that decision is not "does Home tilt today" — it does
 * not, and a render test would say so. The risk is that somebody reintroduces
 * the coupling later, in a build where the flags are off, and nobody notices
 * until a device with the flags on turns the Feed sideways in a user's hand.
 * A render test with a mocked sensor cannot fail that way: it would happily
 * pass against a HomeScreen that mounts `useTiltNavigation` behind a flag which
 * reads false in CI. Reading the source is what actually pins the claim.
 *
 * The rule enforced is therefore stronger than "motion is off on Home": Home
 * does not import the motion layer at all. There is no flag to get wrong,
 * because there is no call site.
 *
 * ## What is deliberately NOT banned
 *
 * `SpatialPager` itself. The dormant spatial-feed implementation stays behind
 * `spatialHomeFeedEnabled` (OFF, and staying OFF) so the work remains
 * recoverable — mission §7.3 forbids deleting it. What it may not be is
 * motion-driven: as mounted from Home it is a touch-only pager with no tilt
 * hook, no immersive navigator and no parallax wrapper.
 */
import fs from "fs";
import path from "path";

const SRC = path.join(__dirname, "..", "..");
const HOME_SCREEN = path.join(SRC, "screens", "HomeScreen.tsx");

const homeSource = fs.readFileSync(HOME_SCREEN, "utf8");

/**
 * Modules that turn a surface into a motion surface.
 *
 * Matched against import specifiers rather than free text so a mention in a
 * comment — and this file expects Home to keep explaining why the pager is
 * dormant — does not read as a dependency.
 */
const MOTION_MODULES = [
  "spatial/motion/useTiltNavigation",
  "spatial/motion/motionSettings",
  "spatial/motion/motionAvailability",
  "spatial/motion/motionStateMachine",
  "spatial/motion/MotionOnboarding",
  "spatial/useImmersiveNavigator",
  "spatial/ImmersiveRevealStrip",
  "expo-sensors"
];

function importSpecifiers(source: string): string[] {
  const specifiers: string[] = [];
  const staticImport = /^\s*import\s[^;]*?from\s+["']([^"']+)["']/gm;
  const bareImport = /^\s*import\s+["']([^"']+)["']/gm;
  const requireCall = /require\(\s*["']([^"']+)["']\s*\)/g;
  for (const pattern of [staticImport, bareImport, requireCall]) {
    let match = pattern.exec(source);
    while (match) {
      specifiers.push(match[1]);
      match = pattern.exec(source);
    }
  }
  return specifiers;
}

/** Resolve "../spatial/x" against HomeScreen into a src-relative path. */
function normalize(specifier: string, fromFile: string): string {
  if (!specifier.startsWith(".")) return specifier;
  const resolved = path.resolve(path.dirname(fromFile), specifier);
  return path.relative(SRC, resolved).split(path.sep).join("/");
}

describe("Home Feed rollback: no motion layer", () => {
  const homeImports = importSpecifiers(homeSource).map((specifier) => normalize(specifier, HOME_SCREEN));

  it.each(MOTION_MODULES)("HomeScreen does not import %s", (moduleName) => {
    expect(homeImports).not.toContain(moduleName);
  });

  it("HomeScreen mounts neither the tilt hook nor the immersive navigator", () => {
    expect(homeSource).not.toMatch(/\buseTiltNavigation\s*\(/);
    expect(homeSource).not.toMatch(/\buseImmersiveNavigator\s*\(/);
    expect(homeSource).not.toMatch(/<ImmersiveRevealStrip\b/);
  });

  it("HomeScreen keeps no tilt preview transform", () => {
    // The parallax wrapper read `tilt.previewProgress` into a translateX.
    expect(homeSource).not.toMatch(/previewProgress/);
    expect(homeSource).not.toMatch(/notifySwipeSettled/);
  });

  it("keeps the dormant spatial pager so the implementation stays recoverable", () => {
    // Mission §7.3: rollback capability, not deletion. If this ever fails,
    // the pager was removed rather than switched off — a different decision
    // than the one this mission made.
    expect(homeSource).toMatch(/spatialHomeFeedEnabled\(\)/);
    expect(homeSource).toMatch(/<SpatialPager\b/);
  });

  it("the dormant pager is touch-driven only", () => {
    const pagerProps = homeSource.slice(
      homeSource.indexOf("<SpatialPager"),
      homeSource.indexOf("/>", homeSource.indexOf("<SpatialPager"))
    );
    expect(pagerProps).not.toMatch(/onDragStart/);
    expect(pagerProps).not.toMatch(/controllerRef/);
    expect(pagerProps).toMatch(/onIndexSettled/);
  });
});

describe("motion layer is reachable from Reels alone", () => {
  /** Every non-test source file under src/, with its src-relative path. */
  function walk(dir: string, out: string[] = []): string[] {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "__tests__" || entry.name === "node_modules") continue;
        walk(full, out);
      } else if (/\.tsx?$/.test(entry.name)) {
        out.push(full);
      }
    }
    return out;
  }

  const files = walk(SRC);

  it("useTiltNavigation has exactly one call site, and it is ReelsScreen", () => {
    const callers = files.filter((file) => {
      if (file.includes(`${path.sep}spatial${path.sep}motion${path.sep}`)) return false;
      return /\buseTiltNavigation\s*\(/.test(fs.readFileSync(file, "utf8"));
    });
    expect(callers.map((file) => path.relative(SRC, file).split(path.sep).join("/"))).toEqual([
      "screens/ReelsScreen.tsx"
    ]);
  });

  it("useImmersiveNavigator has exactly one call site, and it is ReelsScreen", () => {
    const callers = files.filter((file) => {
      if (file.endsWith(`${path.sep}useImmersiveNavigator.ts`)) return false;
      return /\buseImmersiveNavigator\s*\(/.test(fs.readFileSync(file, "utf8"));
    });
    expect(callers.map((file) => path.relative(SRC, file).split(path.sep).join("/"))).toEqual([
      "screens/ReelsScreen.tsx"
    ]);
  });

  it("expo-sensors is imported only by the motion availability probe", () => {
    const importers = files.filter((file) =>
      importSpecifiers(fs.readFileSync(file, "utf8")).includes("expo-sensors")
    );
    expect(importers.map((file) => path.relative(SRC, file).split(path.sep).join("/"))).toEqual([
      "spatial/motion/motionAvailability.ts"
    ]);
  });

  it("no surface offers a motion scope choice any more", () => {
    // `scope` was a user-facing setting ("Feed and Reels" / "Feed only" /
    // "Reels only"). It is now a read-only migration concern: the only place
    // the word may survive is motionSettings, which still has to recognise the
    // values sitting on real devices. A scope picker reappearing anywhere else
    // means the Feed question was asked again.
    //
    // `\bMotionScope\b` deliberately does not match inside `LegacyMotionScope`:
    // pointing a reader at the migration type is the documented thing to do,
    // and only the withdrawn user-facing type and its testID are banned.
    const offenders = files.filter((file) => {
      if (file.endsWith(`${path.sep}motionSettings.ts`)) return false;
      const source = fs.readFileSync(file, "utf8");
      return /\bMotionScope\b/.test(source) || /spatial-motion-scope/.test(source);
    });
    expect(offenders.map((file) => path.relative(SRC, file).split(path.sep).join("/"))).toEqual([]);
  });

  it("the Spatial Motion settings copy names Reels and never the Feed", () => {
    const settings = fs.readFileSync(
      path.join(SRC, "screens", "settings", "AccessibilitySettingsScreen.tsx"),
      "utf8"
    );
    const section = settings.slice(settings.indexOf("function SpatialMotionSection"));
    // Not a style rule: copy promising motion "in your feed" is a promise the
    // build no longer keeps, and it is the kind of thing that survives a
    // rollback because nothing executes it.
    expect(section).not.toMatch(/\bfeed\b/i);
    expect(section).toMatch(/\bReels?\b/);
  });

  it("Messages and Create never mount motion", () => {
    for (const name of ["screens/MessengerScreen.tsx", "spatial/SpatialCreateConsole.tsx"]) {
      const source = fs.readFileSync(path.join(SRC, name), "utf8");
      expect(source).not.toMatch(/\buseTiltNavigation\s*\(/);
      expect(source).not.toMatch(/\buseImmersiveNavigator\s*\(/);
    }
  });
});

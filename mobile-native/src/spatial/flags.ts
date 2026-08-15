/**
 * Spatial Console feature flags.
 *
 * Every spatial-console behavior in the app is gated through this module and
 * nothing else. All flags default OFF: with no env vars set, the app renders
 * the legacy experience byte-for-byte, which is the rollback guarantee.
 *
 * Rollout is via `EXPO_PUBLIC_*` env vars (EAS build profiles or `.env`),
 * inlined at bundle time and parsed at call time by `isFlagValueOn` — the same
 * accepted-value rule the rest of the app uses. See `FLAG_READERS` below for why
 * the reads are written out one per flag instead of looked up by name.
 *
 * `spatialConsoleEnabled` is the master switch: every sub-flag requires it.
 * Turning off the master is total rollback; turning off a sub-flag rolls back
 * that surface alone. See docs/spatial-console-rollback.md.
 */
import { isFlagValueOn } from "../core/envFlag";

type SpatialFlagName =
  | "spatialConsoleEnabled"
  | "spatialHomeFeedEnabled"
  | "spatialReelsEnabled"
  | "spatialCreateEnabled"
  | "messagesVisualRefreshEnabled"
  | "immersiveNavigatorEnabled"
  | "spatialMotionEnabled"
  | "tiltNavigationEnabled"
  | "tiltParallaxEnabled";

/**
 * Each flag's value, read through a STATIC `process.env` member expression.
 *
 * The static form is load-bearing, not style. `babel-preset-expo` replaces
 * `process.env.EXPO_PUBLIC_X` with its build-time literal during the bundle
 * transform; it cannot see through a computed `process.env[name]`. A release
 * bundle has no populated `process.env` at runtime, so the computed lookup this
 * map replaces resolved to `undefined` for every flag — meaning no spatial
 * surface could be switched on in a release build no matter what the EAS
 * profile, `.env` or shell exported.
 *
 * That failure was silent in exactly the way the rollback guarantee is designed
 * to look: every flag read false, the legacy experience rendered, and nothing
 * errored. It was confirmed by grepping a shipped bundle — names read
 * statically elsewhere in the app are absent from it (inlined away), while
 * these names survived as runtime keys that nothing ever resolved.
 *
 * Entries are thunks so the value is still read at call time rather than
 * captured at module load, which is what lets a test toggle and re-ask without
 * re-importing the module graph.
 *
 * Each thunk hands its raw value straight to `isFlagValueOn` on the same line.
 * That is the shape the architecture guard in `core/__tests__/envFlag.test.ts`
 * recognises as "static spelling, shared rule" — the accepted-value logic lives
 * in one place and this module only decides *which* literal to read.
 */
const FLAG_READERS: Record<SpatialFlagName, () => boolean> = {
  spatialConsoleEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_SPATIAL_CONSOLE),
  spatialHomeFeedEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_SPATIAL_HOME_FEED),
  spatialReelsEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_SPATIAL_REELS),
  spatialCreateEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_SPATIAL_CREATE),
  messagesVisualRefreshEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_MESSAGES_VISUAL_REFRESH),
  immersiveNavigatorEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_IMMERSIVE_NAVIGATOR),
  spatialMotionEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_SPATIAL_MOTION),
  tiltNavigationEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_TILT_NAVIGATION),
  tiltParallaxEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_TILT_PARALLAX)
};

/**
 * Test-only overrides. Production code never calls the setter; jest suites use
 * it to exercise both sides of each gate without mutating process.env.
 */
const overrides = new Map<SpatialFlagName, boolean>();

export function __setSpatialFlagOverride(name: SpatialFlagName, value: boolean | undefined) {
  if (value === undefined) overrides.delete(name);
  else overrides.set(name, value);
}

export function __clearSpatialFlagOverrides() {
  overrides.clear();
}

function flagOn(name: SpatialFlagName): boolean {
  const override = overrides.get(name);
  if (override !== undefined) return override;
  return FLAG_READERS[name]();
}

/** Master switch. Off = entire spatial console rolled back to legacy. */
export function spatialConsoleEnabled(): boolean {
  return flagOn("spatialConsoleEnabled");
}

/** Home feed becomes a horizontal spatial pager (header/network/status untouched). */
export function spatialHomeFeedEnabled(): boolean {
  return spatialConsoleEnabled() && flagOn("spatialHomeFeedEnabled");
}

/** Reels central player pages horizontally instead of vertically. */
export function spatialReelsEnabled(): boolean {
  return spatialConsoleEnabled() && flagOn("spatialReelsEnabled");
}

/** Create button opens the spatial create console instead of the composer jump. */
export function spatialCreateEnabled(): boolean {
  return spatialConsoleEnabled() && flagOn("spatialCreateEnabled");
}

/** Messenger visual refinements (title, search, spacing, compose FAB). */
export function messagesVisualRefreshEnabled(): boolean {
  return spatialConsoleEnabled() && flagOn("messagesVisualRefreshEnabled");
}

/**
 * Directional bottom-nav visibility on the Reels route: a committed swipe left
 * hides it, a committed swipe right reveals it, a tap recovers it. Off means
 * the full-screen pager keeps working with navigation permanently visible —
 * and note that off gates *hiding* only, so `reveal()` still works, because a
 * recovery affordance that is itself flagged can strand a user behind an
 * invisible dock. See `spatial/navigatorVisibility.ts` for the direction rule.
 */
export function immersiveNavigatorEnabled(): boolean {
  return spatialConsoleEnabled() && flagOn("immersiveNavigatorEnabled");
}

/**
 * Motion master switch. Off = no sensor subscription anywhere, no motion
 * onboarding, no Spatial Motion settings surface. Requires the console master.
 */
export function spatialMotionEnabled(): boolean {
  return spatialConsoleEnabled() && flagOn("spatialMotionEnabled");
}

/** Sustained tilt may commit page navigation. Requires the motion master. */
export function tiltNavigationEnabled(): boolean {
  return spatialMotionEnabled() && flagOn("tiltNavigationEnabled");
}

/** Slight tilt drives parallax depth preview only. Requires the motion master. */
export function tiltParallaxEnabled(): boolean {
  return spatialMotionEnabled() && flagOn("tiltParallaxEnabled");
}

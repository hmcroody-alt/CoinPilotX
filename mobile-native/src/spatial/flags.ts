/**
 * Spatial Console feature flags.
 *
 * Every spatial-console behavior in the app is gated through this module and
 * nothing else. All flags default OFF: with no env vars set, the app renders
 * the legacy experience byte-for-byte, which is the rollback guarantee.
 *
 * Rollout is via `EXPO_PUBLIC_*` env vars (EAS build profiles or `.env`),
 * read at call time by `envFlagOn` — the same mechanism production already
 * uses for other gated features.
 *
 * `spatialConsoleEnabled` is the master switch: every sub-flag requires it.
 * Turning off the master is total rollback; turning off a sub-flag rolls back
 * that surface alone. See docs/spatial-console-rollback.md.
 */
import { envFlagOn } from "../core/envFlag";

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

const ENV_VARS: Record<SpatialFlagName, string> = {
  spatialConsoleEnabled: "EXPO_PUBLIC_SPATIAL_CONSOLE",
  spatialHomeFeedEnabled: "EXPO_PUBLIC_SPATIAL_HOME_FEED",
  spatialReelsEnabled: "EXPO_PUBLIC_SPATIAL_REELS",
  spatialCreateEnabled: "EXPO_PUBLIC_SPATIAL_CREATE",
  messagesVisualRefreshEnabled: "EXPO_PUBLIC_MESSAGES_VISUAL_REFRESH",
  immersiveNavigatorEnabled: "EXPO_PUBLIC_IMMERSIVE_NAVIGATOR",
  spatialMotionEnabled: "EXPO_PUBLIC_SPATIAL_MOTION",
  tiltNavigationEnabled: "EXPO_PUBLIC_TILT_NAVIGATION",
  tiltParallaxEnabled: "EXPO_PUBLIC_TILT_PARALLAX"
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
  return envFlagOn(ENV_VARS[name]);
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

/** Bottom-nav auto-hide after the first settled horizontal swipe. */
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

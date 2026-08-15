/**
 * Home discovery suggestion flags.
 *
 * Home is the app's most-used surface, so every part of the discovery feed is
 * gated here and nowhere else. All flags default OFF: with no env vars set,
 * `homeDiscoveryEnabled()` is false, the placement engine is never called, and
 * Home renders exactly the rows `injectAds` produced before this feature
 * existed. That is the rollback guarantee, and it is a flag flip rather than a
 * revert — no commit has to come out to get the old Home back.
 *
 * This is a separate module from `spatial/flags.ts` on purpose. The spatial
 * console master switch turns off a *visual redesign*; discovery suggestions are
 * a content feature that ships on the ordinary Home feed and must be able to
 * roll forward and back without moving that switch in either direction.
 *
 * The `EXPO_PUBLIC_*` reads are written out one per flag as static member
 * expressions. That form is load-bearing, not style: `babel-preset-expo` inlines
 * `process.env.EXPO_PUBLIC_X` at bundle time only when the key is a literal, and
 * a release bundle has no populated `process.env` at runtime. A computed lookup
 * reads `undefined` for every flag on device while working perfectly in jest —
 * the exact failure documented at length in `spatial/flags.ts`.
 */
import { isFlagValueOn } from "../core/envFlag";

type DiscoveryFlagName =
  | "homeDiscoveryEnabled"
  | "discoveryReelsEnabled"
  | "discoveryPeopleEnabled"
  | "discoveryStatusesEnabled"
  | "discoveryGroupsEnabled"
  | "discoveryCreatorsEnabled"
  | "discoveryTopicsEnabled"
  | "discoverySponsoredEnabled";

/** Each flag's value, read through a STATIC `process.env` member expression. */
const FLAG_READERS: Record<DiscoveryFlagName, () => boolean> = {
  homeDiscoveryEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_HOME_DISCOVERY),
  discoveryReelsEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_HOME_DISCOVERY_REELS),
  discoveryPeopleEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_HOME_DISCOVERY_PEOPLE),
  discoveryStatusesEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_HOME_DISCOVERY_STATUSES),
  discoveryGroupsEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_HOME_DISCOVERY_GROUPS),
  discoveryCreatorsEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_HOME_DISCOVERY_CREATORS),
  discoveryTopicsEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_HOME_DISCOVERY_TOPICS),
  discoverySponsoredEnabled: () => isFlagValueOn(process.env.EXPO_PUBLIC_HOME_DISCOVERY_SPONSORED)
};

/**
 * Test-only overrides. Production code never calls the setter; jest suites use
 * it to exercise both sides of each gate without mutating process.env.
 */
const overrides = new Map<DiscoveryFlagName, boolean>();

export function __setDiscoveryFlagOverride(name: DiscoveryFlagName, value: boolean | undefined) {
  if (value === undefined) overrides.delete(name);
  else overrides.set(name, value);
}

export function __clearDiscoveryFlagOverrides() {
  overrides.clear();
}

function flagOn(name: DiscoveryFlagName): boolean {
  const override = overrides.get(name);
  if (override !== undefined) return override;
  return FLAG_READERS[name]();
}

/** Master switch. Off = Home is byte-for-byte the pre-discovery feed. */
export function homeDiscoveryEnabled(): boolean {
  return flagOn("homeDiscoveryEnabled");
}

/** Suggested reels row, and with it the exact-reel transfer into the player. */
export function discoveryReelsEnabled(): boolean {
  return homeDiscoveryEnabled() && flagOn("discoveryReelsEnabled");
}

/** People you may know. */
export function discoveryPeopleEnabled(): boolean {
  return homeDiscoveryEnabled() && flagOn("discoveryPeopleEnabled");
}

/** Statuses worth catching before they expire. */
export function discoveryStatusesEnabled(): boolean {
  return homeDiscoveryEnabled() && flagOn("discoveryStatusesEnabled");
}

/** Groups and communities. */
export function discoveryGroupsEnabled(): boolean {
  return homeDiscoveryEnabled() && flagOn("discoveryGroupsEnabled");
}

/** Creators to follow. */
export function discoveryCreatorsEnabled(): boolean {
  return homeDiscoveryEnabled() && flagOn("discoveryCreatorsEnabled");
}

/**
 * Topics. Kept off by default and separately from the rest because the mobile
 * app has no topic destination: topics exist only as the server-rendered
 * `/pulse/topic/<topic>` page, so a topic card in the feed would be a tap that
 * goes nowhere. Turning this on requires a topic screen first.
 */
export function discoveryTopicsEnabled(): boolean {
  return homeDiscoveryEnabled() && flagOn("discoveryTopicsEnabled");
}

/**
 * Sponsored discovery rows.
 *
 * Home already places sponsored cards through `injectAds`, which is a different
 * placement path with its own cadence and its own viewability accounting. This
 * flag exists so a sponsored *carousel* can never appear alongside that without
 * somebody deliberately turning it on and re-checking the ad frequency caps.
 */
export function discoverySponsoredEnabled(): boolean {
  return homeDiscoveryEnabled() && flagOn("discoverySponsoredEnabled");
}

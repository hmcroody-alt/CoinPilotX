/**
 * Home discovery suggestion flags.
 *
 * Home is the app's most-used surface, so every part of the discovery feed is
 * gated here and nowhere else.
 *
 * ## The five that finished rolling out default ON
 *
 * The master switch and the four modules that have a real source — reels,
 * people, statuses, groups — read through {@link isFlagValueOnUnlessDisabled},
 * so an unset variable means *on* and turning the feature off costs somebody a
 * deliberate `=0`.
 *
 * They did not start that way, and the reason they moved is worth keeping.
 * These five shipped default-OFF and were verified on device by exporting the
 * variables by hand at build time. Nothing in the repo sets them: no EAS profile
 * in `eas.json`, no `.env` (`.gitignore` excludes `.env` and `.env.*`, so there
 * is nowhere committed for them to live). So the feature was on for exactly one
 * build — the one whose operator typed the exports — and off for every build
 * made afterwards by anyone, including the very next one. The suggestion rows
 * were duly reported as having "come undone" when a later rebuild, made for an
 * unrelated fix, simply did not carry the exports.
 *
 * That is the same incident `spatial/flags.ts` documents for the Reels pager,
 * one file over and one week apart. A feature that is finished cannot depend on
 * a shell variable that has no committed home. Rollback is still a flag flip and
 * never a revert; the flip just now runs in the direction that should cost an
 * action.
 *
 * ## The three that stay OFF stay OFF
 *
 * Creators, topics and sponsored keep {@link isFlagValueOn} and its default-off
 * behaviour, because they are not finished rather than not enabled: creators has
 * no ranked endpoint distinct from People, topics has no mobile destination to
 * open, and sponsored would be a second ad surface alongside `injectAds` with no
 * shared frequency cap. `sources.ts` builds no adapter for any of them, so each
 * would render an empty row even if it were switched on. Defaulting them on
 * would be defaulting on three features that do not exist.
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
import { isFlagValueOn, isFlagValueOnUnlessDisabled } from "../core/envFlag";

type DiscoveryFlagName =
  | "homeDiscoveryEnabled"
  | "discoveryReelsEnabled"
  | "discoveryPeopleEnabled"
  | "discoveryStatusesEnabled"
  | "discoveryGroupsEnabled"
  | "discoveryCreatorsEnabled"
  | "discoveryTopicsEnabled"
  | "discoverySponsoredEnabled";

/**
 * Each flag's value, read through a STATIC `process.env` member expression.
 *
 * Shipped modules use `isFlagValueOnUnlessDisabled` (unset = on, `=0` to roll
 * back); unfinished ones use `isFlagValueOn` (unset = off). Which reader a line
 * uses *is* the ship state — there is no separate defaults table to fall out of
 * sync with this one.
 */
const FLAG_READERS: Record<DiscoveryFlagName, () => boolean> = {
  homeDiscoveryEnabled: () => isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_HOME_DISCOVERY),
  discoveryReelsEnabled: () => isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_HOME_DISCOVERY_REELS),
  discoveryPeopleEnabled: () => isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_HOME_DISCOVERY_PEOPLE),
  discoveryStatusesEnabled: () => isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_HOME_DISCOVERY_STATUSES),
  discoveryGroupsEnabled: () => isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_HOME_DISCOVERY_GROUPS),
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

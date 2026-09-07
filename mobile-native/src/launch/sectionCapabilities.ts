/**
 * Whether a capability inside a Business OS section can actually be used.
 *
 * WHY THIS EXISTS
 *
 * `readiness.ts` answers "has the audit locked this?" and `api/businessOs.ts`
 * answers "where does this go?". Neither question is the one a screen needs to
 * ask before drawing a row, which is "if the user taps this, does something
 * happen?" — and that is the conjunction of both.
 *
 * Before this module the conjunction was implicit, and it was made three times:
 * the screen split its lists on `isLaunchReady`, `LaunchModuleRow` recomputed
 * `readinessOf` to decide whether to draw a lock, and `openModule` carried an
 * `if (!module.route) return;` that silently swallowed the difference. Three
 * places agreeing today is not the same as three places that cannot disagree.
 *
 * THE GAP THAT MADE IT NECESSARY
 *
 * `readinessOf` returns READY for an id it has never heard of. That default is
 * correct and deliberate — the table is a deny-list produced by an audit, and
 * an allow-list would silently lock every feature nobody remembered to register
 * (see the header of `readiness.ts`). But READY means "the audit found nothing
 * wrong", not "there is somewhere to go". A module that is absent from the table
 * AND has no `route` therefore rendered as available, with a chevron and an
 * accessibility label saying it works, and did nothing at all when pressed.
 *
 * That combination does not occur in today's registry. It was prevented by a
 * comment and by a test that reads the registry as it stands, which is to say it
 * was prevented for exactly as long as nobody added a row. `NO_DESTINATION`
 * makes it structural: a capability with nowhere to go is not available, whatever
 * the table says, and the user is told so in the words the product already uses.
 *
 * WHAT THIS IS NOT
 *
 * It is not a second registry. Labels, blurbs, icons and routes stay in
 * `api/businessOs.ts`; states stay in `readiness.ts`. This file holds the join
 * and nothing else, so shipping a capability is still a deletion in the
 * readiness table plus a `route` in the registry, with no third file to update.
 */

import { businessOsSectionModules, type BusinessOsModule } from "../api/businessOs";
import { businessSubmoduleId, readinessOf, type LaunchModuleId, type ReadinessState } from "./readiness";

/**
 * The gate's verdict once a destination is taken into account.
 *
 * The three `ReadinessState` values keep their meanings. `NO_DESTINATION` is the
 * fourth case they cannot express: the audit has no objection, and there is
 * still nothing to open.
 *
 * It is kept apart from `COMING_SOON` for the same reason `BUILDING` is — the
 * user sees one message either way, but a later reader can tell "the audit shut
 * this" from "nobody wired it up" without re-running the audit.
 */
export type CapabilityAvailability = ReadinessState | "NO_DESTINATION";

export type ResolvedCapability = {
  module: BusinessOsModule;
  id: LaunchModuleId;
  availability: CapabilityAvailability;
  /** The single fact a screen acts on. True only for `READY` with a route. */
  available: boolean;
  /** Present only when `available`. Reading it is how a caller navigates. */
  route?: string;
  params?: Record<string, string | number | boolean>;
};

/**
 * The state to use for user-facing copy.
 *
 * `NO_DESTINATION` has no wording of its own on purpose. "Coming soon" is true
 * of it, the product already says it, and inventing a fourth badge would leak an
 * implementation detail — that a registry row is missing a field — into the
 * operator's language.
 */
export function capabilityCopyState(availability: CapabilityAvailability): ReadinessState {
  return availability === "BUILDING" ? "BUILDING" : availability === "READY" ? "READY" : "COMING_SOON";
}

/** One capability, resolved. See `CapabilityAvailability` for the four outcomes. */
export function resolveSectionCapability(sectionKey: string, module: BusinessOsModule): ResolvedCapability {
  const id = businessSubmoduleId(sectionKey, module.key);
  const state = readinessOf(id);

  if (state !== "READY") {
    return { module, id, availability: state, available: false };
  }
  if (!module.route) {
    return { module, id, availability: "NO_DESTINATION", available: false };
  }
  return { module, id, availability: "READY", available: true, route: module.route, params: module.params };
}

/**
 * Every capability a section lists, in registry order, each carrying its verdict.
 *
 * Registry order rather than state order: reading a section top to bottom should
 * describe the job, so a capability keeps its place in the story when it ships.
 * An unregistered section yields `[]`, which is what the screen's own fallback
 * is keyed on.
 */
export function resolveSectionCapabilities(sectionKey: string): ResolvedCapability[] {
  return businessOsSectionModules(sectionKey).map((module) => resolveSectionCapability(sectionKey, module));
}

/**
 * The two lists the landing renders.
 *
 * Split here rather than in the screen so that "available" has one definition.
 * A screen that filtered on `isLaunchReady` would put a routeless READY module
 * in the wrong list, which is the bug this module exists to make unrepresentable.
 */
export function sectionCapabilityLists(sectionKey: string): {
  available: ResolvedCapability[];
  upcoming: ResolvedCapability[];
} {
  const capabilities = resolveSectionCapabilities(sectionKey);
  return {
    available: capabilities.filter((capability) => capability.available),
    upcoming: capabilities.filter((capability) => !capability.available)
  };
}

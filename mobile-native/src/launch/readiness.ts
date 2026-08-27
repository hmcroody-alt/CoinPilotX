/**
 * Launch readiness gate — the ONE place that decides whether a Business or
 * Presence module opens its real feature or its Coming Soon message.
 *
 * WHY THIS EXISTS
 *
 * PulseSoc ships Business and Presence as complete-looking worlds. A few
 * modules inside them are not finished. The previous handling of that was to
 * *hide* them (`BUSINESS_OS_SECTIONS[].backed === false` filtered the card out
 * of the grid entirely), which has two costs: the user cannot see what is
 * coming, and every new gap needs a new ad-hoc conditional somewhere in a
 * screen. This module replaces both with one table.
 *
 * WHAT IT IS NOT
 *
 * It is not a feature flag system and it does not delete anything. Every screen,
 * route and backend endpoint named below still exists and still works if reached
 * by other means. This is a temporary launch gate: when a module's backend lands,
 * its row here is deleted and the module opens normally with no other edit
 * anywhere in the app.
 *
 * DEFAULTING
 *
 * An id that is not in the table is READY. That is deliberate. The mission
 * guard is "do not blindly lock an already-production-ready feature", so the
 * table is an explicit deny-list produced by an audit of every backing route,
 * not an allow-list that silently locks anything nobody remembered to register.
 */

/**
 * The three states a module can be in.
 *
 *   READY        — the feature works end to end. Open it.
 *   BUILDING     — the screen exists and is actively being finished, but its
 *                  backing data is absent or wrong. Do not open it.
 *   COMING_SOON  — there is no implementation yet at all. Do not open it.
 *
 * BUILDING and COMING_SOON differ only in what the audit found, not in what the
 * user sees: both show the same Coming Soon message. Keeping them apart is what
 * lets a future reader tell "half-built" from "not started" without re-running
 * the audit.
 */
export type ReadinessState = "READY" | "BUILDING" | "COMING_SOON";

/** `business:<sectionKey>` or `presence:<actionKey>`. */
export type LaunchModuleId = string;

/**
 * The gate table.
 *
 * Each row carries the evidence that put it here, because a launch gate with no
 * stated reason becomes permanent: nobody can tell whether it is still true.
 */
export const LAUNCH_READINESS: Readonly<Record<LaunchModuleId, ReadinessState>> = Object.freeze({
  /*
   * Business — Events.
   *
   * `EventsManagerScreen` renders three tabs off `loadEventsModel` →
   * `listScheduledLiveEvents` → `listLiveNow`, which calls
   * `GET /api/pulse/live-now`. That response has no `scheduled` or `events` key,
   * and `pulse_live_now_cards` excludes scheduled rows at the SQL level, so the
   * client filter can only ever produce []. Three tabs that are structurally
   * incapable of holding a row.
   */
  "business:events": "BUILDING",

  /*
   * Business — Customers. No screen, no route, no `/api/pulse/*` endpoint.
   * Was hidden by `backed: false`; now visible and locked.
   */
  "business:customers": "COMING_SOON",

  /*
   * Business — Team. No screen, no route, no `/api/pulse/*` endpoint. Page-level
   * roles exist under `/api/pages/*` but that is the Presence team, a different
   * subject. Was hidden by `backed: false`; now visible and locked.
   */
  "business:team": "COMING_SOON",

  /*
   * Presence — the per-presence "Business OS" action.
   *
   * This is the subtlest entry in the table and the reason the gate is keyed by
   * *module*, not by route. `PresenceHubScreen` navigates to `BusinessOs` with
   * `{ title: page.name }` and no page identifier. `BusinessOsScreen` then calls
   * `resolveRouteProfileContext(undefined, viewer)`, which sets
   * `isOwnProfile = true` — so the header carries the presence's name while the
   * body carries the signed-in user's own listings, orders and ad spend.
   *
   * The `BusinessOs` ROUTE is READY and stays READY: reached from the profile
   * tile it is correct and is the viewer's own business by definition. It is
   * only the presence-scoped entry that has no way to say which business it
   * means. Wrong data under a real name is worse than a locked door, so the
   * door is locked until the route can carry a page id.
   */
  "presence:businessOs": "BUILDING",

  /*
   * Presence — the three creation entries.
   *
   * "Create Artist Presence", "Create Business Presence" and "+ Create New" all
   * land on the same `PageCreate` screen and differ only in the `flavor` they
   * pass. They are three rows rather than one because they are three things a
   * user can see and tap: the badge and the accessibility label are read per
   * control, and a single shared id would leave the gate unable to say which
   * door it just closed.
   *
   * BUILDING rather than COMING_SOON: `PageCreateScreen` exists and renders, so
   * this is a workflow being finished rather than one never started. Both
   * resolve to the same message for the user; the distinction is what lets a
   * later reader tell half-built from not-started without re-running the audit.
   *
   * The landing page itself is deliberately absent from this table. Presence
   * Home lists real pages from `listMyPages()` and its View / Manage actions
   * work, so it stays READY — the gate is on creation, not on the surface.
   */
  "presence:createArtist": "BUILDING",
  "presence:createBusiness": "BUILDING",
  "presence:createNew": "BUILDING",

  /* ---------------------------------------------------------------- *
   * Second layer — modules INSIDE a Business OS section.
   *
   * The rows above gate a whole section tile. These gate the modules a
   * section lists once it is open, which is what lets a section be
   * enterable while the unfinished depth behind it stays shut. Same table,
   * same three states, same `readinessOf` — a second layer of gating, not a
   * second gating system.
   *
   * Id shape is `business:<section>.<module>`, so a module id can never
   * collide with the section id that owns it.
   * ---------------------------------------------------------------- */

  /*
   * Customers. The section has no backend at all, so both of its modules are
   * shut. `records` has nothing behind it; `segments` is the one being cut
   * first, which is the whole reason the two states are kept apart.
   */
  "business:customers.records": "COMING_SOON",
  "business:customers.segments": "BUILDING",

  /*
   * Team. Page-level roles exist under `/api/pages/*`, but that is the
   * Presence team — a different subject with a different owner model. Nothing
   * backs a *business* team yet, so both modules are shut.
   */
  "business:team.members": "COMING_SOON",
  "business:team.roles": "COMING_SOON",

  /*
   * Events. The hosted-events manager is the module that cannot hold a row
   * (see `business:events` above) — it is the module that is unfinished, not
   * the section. Live discovery is a real, shipping screen and is deliberately
   * absent from this table, so the Events section opens with one working
   * capability and one locked one.
   */
  "business:events.manager": "BUILDING"
});

/**
 * Id builders. Callers use these rather than writing the string, so a typo is a
 * compile error at the call site instead of a module that silently reads READY
 * because `"buisness:team"` is not in the table.
 */
export function businessModuleId(sectionKey: string): LaunchModuleId {
  return `business:${sectionKey}`;
}

export function presenceModuleId(actionKey: string): LaunchModuleId {
  return `presence:${actionKey}`;
}

/** A module *inside* a Business OS section — `business:<section>.<module>`. */
export function businessSubmoduleId(sectionKey: string, moduleKey: string): LaunchModuleId {
  return `business:${sectionKey}.${moduleKey}`;
}

/** The gate's verdict for a module. Unknown ids are READY — see the header. */
export function readinessOf(id: LaunchModuleId): ReadinessState {
  return LAUNCH_READINESS[id] ?? "READY";
}

/** True when the module should open its real feature. */
export function isLaunchReady(id: LaunchModuleId): boolean {
  return readinessOf(id) === "READY";
}

/** True when the module should show the Coming Soon message instead. */
export function isLaunchGated(id: LaunchModuleId): boolean {
  return !isLaunchReady(id);
}

/**
 * Routes that must refuse to render regardless of how they were reached.
 *
 * A card that shows Coming Soon is only half the gate: the same screen is also
 * reachable by deep link, by a `navigation.navigate` left in some other surface,
 * and by state restoration after a cold start. This map is what a route's own
 * router consults — `EventsRoute` is the one that exists today — so the refusal
 * lives at the screen rather than at each call site and there is no path around
 * it.
 *
 * Only routes whose WHOLE PURPOSE is a gated module belong here. `BusinessOs`
 * does not: it is READY from the profile tile, and only the presence-scoped
 * entry into it is gated — that one is enforced at its call site because the
 * route itself cannot tell the two callers apart (which is precisely the bug).
 */
export const GATED_ROUTES: Readonly<Record<string, LaunchModuleId>> = Object.freeze({
  BusinessOsEvents: "business:events",

  /*
   * Presence creation. `PageCreate` qualifies on the rule above: the whole
   * screen is the gated module, whichever of the three buttons opened it.
   *
   * It is registered because the three buttons are not the only way in. There
   * is a `navigate("PageCreate")` in `PagesHubScreen` (the Manage surface), and
   * `linking.ts` maps `pulse/pages/create` to it — so a deep link, or
   * navigation state restored after a cold start, arrives without passing any
   * of the gated controls. Keyed to `presence:createNew` because that is the
   * unflavoured entry; the flavoured rows exist for the buttons' own badges.
   */
  PageCreate: "presence:createNew"
});

/** The gate's verdict for a route name. Unregistered routes are READY. */
export function routeReadiness(routeName: string): ReadinessState {
  const id = GATED_ROUTES[routeName];
  return id ? readinessOf(id) : "READY";
}

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

/**
 * `business:<sectionKey>`, `business:<sectionKey>.<capabilityKey>`, or
 * `presence:<actionKey>`.
 *
 * The dotted form is the same gate at a finer grain. A section answers "can I
 * open this?"; a capability answers "is this one thing inside it real yet?".
 * They are the same table and the same verdict function on purpose — a second
 * mechanism for the second grain is how the two drift apart and a section ends
 * up claiming to be ready while listing nothing it can actually do.
 */
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

  /* ================================================================== *
   * Capabilities.
   *
   * Below the section grain: the individual things a section promises. A
   * section can be READY — you can open it, everything it shows is real — and
   * still be missing pieces a user would reasonably expect. Hiding those
   * pieces has the same cost the section-level gate was built to remove: the
   * user cannot see what is coming, and the absence gets filled in by a
   * plausible-looking zero instead.
   *
   * Every row here came out of a read of the screen and the endpoint behind
   * it, and says which one is missing. When the data lands, delete the row —
   * the capability moves from "Coming next" to "Available now" on its
   * section's landing with no other edit.
   *
   * The labels these ids are shown under live in `sectionCapabilities.ts`,
   * next to the section registry's own labels in `api/businessOs.ts`, for the
   * same reason those two are already apart: this file is the audit's verdict,
   * not the product's copy.
   * ================================================================== */

  /* --- Business Profile ---------------------------------------------
   * The profile itself saves and loads against `/api/pulse/business/profile`
   * and is fully real. What is missing is every *number* about the business
   * rather than every *fact*: the screen carries NO_DATA placeholders where a
   * rating, a view count or a response time would go, because no endpoint
   * produces them.
   */
  "business:profile.rating": "COMING_SOON",
  "business:profile.traffic": "COMING_SOON",
  "business:profile.serviceStats": "COMING_SOON",
  /* The hours row renders what the profile stored; there is no weekly editor
   * screen to change it from here. */
  "business:profile.hoursEditor": "BUILDING",
  /* `listScheduledLiveEvents` returns public discovery rows, not the ones this
   * business owns, so the linked-events panel can only be an empty state. */
  "business:profile.linkedEvents": "BUILDING",

  /* --- Store ---------------------------------------------------------
   * Listings, stock and the money already made are live off the seller-store
   * snapshot. The gaps are all declared in `STORE_MOCK_DATA_GAPS`.
   */
  "business:store.views": "COMING_SOON",
  "business:store.rating": "COMING_SOON",
  /* Both need `order.ship_by`, which the live order payload does not carry. */
  "business:store.dispatch": "COMING_SOON",
  "business:store.shipToday": "COMING_SOON",
  /* Per-listing pause works; a seller-level storefront status flag does not
   * exist, so there is nothing to switch. */
  "business:store.pauseStore": "COMING_SOON",
  /* Quantity is coerced to 0 upstream, so "not tracked" and "none left" arrive
   * here as the same value and cannot be told apart. */
  "business:store.stockTracking": "BUILDING",

  /* --- Marketplace ---------------------------------------------------
   * Selling, offers, cart and the buying feed are live. The gaps are declared
   * in `MARKETPLACE_MOCK_DATA_GAPS`.
   */
  "business:marketplace.boost": "COMING_SOON",
  "business:marketplace.listingStats": "COMING_SOON",
  "business:marketplace.savedSearches": "COMING_SOON",
  /* Wants coarse listing coordinates and a radius preference. When it ships it
   * exposes a distance and never a location. */
  "business:marketplace.distance": "COMING_SOON",
  "business:marketplace.ratings": "COMING_SOON",
  /* A safety feature. It is on this list rather than faked for that reason. */
  "business:marketplace.meetupSpots": "COMING_SOON",
  /* Accepting an offer writes no order row, so there is nothing to total. */
  "business:marketplace.soldRevenue": "BUILDING",

  /* --- Advertising ---------------------------------------------------
   * Accounts, campaigns, budgets, spend and the wallet are live against
   * `/api/pulse/ads/*`.
   */
  /* The composer exists behind `EXPO_PUBLIC_ADS_POST_MODE` and runs entirely
   * on mock data — there is no create route on the server. */
  "business:advertising.composer": "BUILDING",
  /* The analytics engine accepts neither a business id nor a date range, so
   * spend cannot be tied back to an order. */
  "business:advertising.attribution": "COMING_SOON",

  /* --- Orders --------------------------------------------------------
   * Both sides of the order list, their timelines and the payout link are
   * live. The gaps are declared in `ORDERS_MOCK_DATA_GAPS`.
   */
  /* The actions are drawn and disabled: they run against the order service,
   * which is not enabled in this build. */
  "business:orders.fulfilment": "BUILDING",
  /* `previewShipBy` fabricates a three-day SLA and the UI tags it "Preview".
   * A real deadline needs `order.ship_by`. */
  "business:orders.shipBy": "BUILDING",
  /* The live surface has only paid vs complete; scheduled and handed-off have
   * no state to read. */
  "business:orders.pickup": "COMING_SOON",
  /* The canonical hold exists, but it is on `/api/business-os`, which is dark
   * in every environment. */
  "business:orders.escrow": "BUILDING",
  "business:orders.perOrderPayout": "COMING_SOON",
  "business:orders.returnWindow": "COMING_SOON",

  /* --- Customers -----------------------------------------------------
   * The section has no screen and no endpoint at all — see the section row
   * above. These name what it will be rather than what is missing from it.
   */
  "business:customers.records": "COMING_SOON",
  "business:customers.segments": "COMING_SOON",
  "business:customers.history": "COMING_SOON",
  "business:customers.notes": "COMING_SOON",

  /* --- Messages ------------------------------------------------------
   * The inbox, threads, live updates, filters and search are live.
   */
  /* `deriveReplyStat` has no source and returns nothing. */
  "business:messages.replyStats": "BUILDING",
  "business:messages.savedReplies": "COMING_SOON",
  /* Stored on the device only, so it says nothing to anyone messaging you. */
  "business:messages.awayMode": "BUILDING",
  "business:messages.typing": "COMING_SOON",
  "business:messages.offerExpiry": "BUILDING",

  /* --- Insights ------------------------------------------------------
   * Revenue, orders, sources, top performers and export are live off
   * `GET /api/pulse/insights/seller/summary`. The gaps are declared in
   * `INSIGHTS_MOCK_DATA_GAPS` and are the same missing measurements the Store
   * and Profile rows above name.
   */
  "business:insights.storeViews": "COMING_SOON",
  "business:insights.dispatch": "COMING_SOON",
  "business:insights.replyRate": "COMING_SOON",
  "business:insights.offersAnswered": "COMING_SOON",
  "business:insights.adAttribution": "COMING_SOON",

  /* --- Payments ------------------------------------------------------
   * Balance, payout method, withdrawals, history, ledger and the ad wallet are
   * live and move real money. Nothing below touches that path.
   */
  "business:payments.instant": "COMING_SOON",
  "business:payments.escrow": "COMING_SOON",
  "business:payments.statements": "COMING_SOON",
  "business:payments.taxDocuments": "COMING_SOON",

  /* --- Events --------------------------------------------------------
   * The section row above explains why all three tabs are structurally empty.
   * These are the tabs, named so the landing can say which is which.
   */
  "business:events.upcoming": "BUILDING",
  "business:events.past": "BUILDING",
  "business:events.drafts": "BUILDING",
  "business:events.create": "COMING_SOON",
  "business:events.rsvp": "COMING_SOON",

  /* --- Team ----------------------------------------------------------
   * No screen and no endpoint — see the section row above. Page-level roles
   * under `/api/pages/*` are the Presence team, a different subject.
   */
  "business:team.invites": "COMING_SOON",
  "business:team.roles": "COMING_SOON",
  "business:team.activity": "COMING_SOON",

  /* --- Settings ------------------------------------------------------
   * Everything the settings hub offers is live; it is simply an account-level
   * hub. Nothing in `settings/registry.ts` is scoped to a business.
   */
  "business:settings.businessPreferences": "COMING_SOON"
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

/** One capability inside a business section. See `LaunchModuleId`. */
export function capabilityModuleId(sectionKey: string, capabilityKey: string): LaunchModuleId {
  return `business:${sectionKey}.${capabilityKey}`;
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
  BusinessOsEvents: "business:events"
});

/** The gate's verdict for a route name. Unregistered routes are READY. */
export function routeReadiness(routeName: string): ReadinessState {
  const id = GATED_ROUTES[routeName];
  return id ? readinessOf(id) : "READY";
}

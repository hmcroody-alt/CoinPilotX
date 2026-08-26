import type { BusinessOsSectionKey } from "../api/businessOs";
import type { ReadinessState } from "./launchReadiness";

/**
 * ## What is open inside Business OS, and what is not
 *
 * Business OS is a hub of thirteen sections. Every section opens — that is the
 * point of this file's existence, and the rule it protects. What varies is what
 * a member finds once they are inside: some sections are finished, some have a
 * finished first layer and unbuilt modules behind it, and two (Customers, Team)
 * have no working layer at all yet.
 *
 * Before this file, the shell handled that last case by *deleting* those two
 * sections from the grid — `businessOsHubSections()` filtered to sections with a
 * live backend contract, so Customers and Team were not merely locked, they were
 * invisible. That is a defensible policy and it was the old one. It has a cost:
 * a member cannot plan around capability they cannot see, and every quarter the
 * hub silently under-describes the product.
 *
 * The policy now is the opposite. Show the whole shape of Business OS, and be
 * exact about which parts of it are load-bearing today.
 *
 * ## Why one table
 *
 * The readiness of a module is a *product* fact, not a rendering detail. Left to
 * each screen, it becomes a scatter of `{isReady && …}` conditions that drift
 * apart: one screen greys a module, another hides it, a third forgets and ships
 * a tap into a half-built workflow. Every Business OS surface reads its answer
 * from here, so there is exactly one place to change when a module lands and
 * exactly one place to audit when someone asks what is real.
 *
 * ## The rule for adding an entry
 *
 * A module goes in this table when the product intends to build it. It does not
 * go in to make a section look fuller. A locked row is a promise rendered on a
 * member's screen, and an invented one is a lie with a lock icon on it — the
 * failure mode this whole layer exists to prevent, arriving through the config
 * instead of through a fake dashboard.
 *
 * `state` carries the same three values the Presence gate uses
 * (`core/launchReadiness.ts`) so the two launch layers speak one vocabulary and
 * a member sees one visual language across the app:
 *
 *   READY        — built, wired to a live contract, opens the real thing.
 *   BUILDING     — actively under construction.
 *   COMING_SOON  — planned, not started.
 *
 * A `READY` module must carry a `route` that `AppNavigator` registers. That
 * pairing is enforced by test rather than by convention, because a `READY` row
 * with no destination is the dead button this layer promises not to ship.
 */

export type BusinessOsModule = {
  key: string;
  label: string;
  /** What the module does, in the member's terms. Shown under the label. */
  blurb: string;
  state: ReadinessState;
  /** Required when `state` is READY; must be a route registered in AppNavigator. */
  route?: string;
  params?: Record<string, string | number | boolean>;
};

/**
 * Modules per section. A section absent from this table, or present with an
 * empty list, renders no roadmap at all — which is the correct output for a
 * section that is simply finished. Silence beats inventing a future for it.
 *
 * Sections deliberately left empty:
 *  - `dashboard` is the hub itself, not a destination with modules.
 *  - `settings` and `verification` are shared surfaces reached from ten-plus
 *    places outside Business OS. A Business OS roadmap panel rendered there
 *    would follow members into Trust & Safety and the profile sheet, which is
 *    not this mission's business.
 */
export const BUSINESS_OS_MODULES: Record<BusinessOsSectionKey, readonly BusinessOsModule[]> = {
  dashboard: [],

  profile: [
    {
      key: "brand_kit",
      label: "Brand Kit",
      blurb: "One set of logos, colours and copy reused everywhere buyers see you.",
      state: "COMING_SOON"
    }
  ],

  store: [
    {
      key: "inventory_intelligence",
      label: "Inventory Intelligence",
      blurb: "Stock forecasting and restock timing based on how your listings actually sell.",
      state: "COMING_SOON"
    }
  ],

  marketplace: [
    {
      key: "seller_analytics",
      label: "Advanced Seller Analytics",
      blurb: "Per-listing conversion, pricing position and what buyers looked at before deciding.",
      state: "BUILDING"
    }
  ],

  advertising: [
    {
      key: "audience_intelligence",
      label: "Advanced Audience Intelligence",
      blurb: "Who your campaigns actually reach, and which audiences are worth building next.",
      state: "COMING_SOON"
    }
  ],

  orders: [
    {
      key: "fulfilment_automation",
      label: "Fulfilment Automation",
      blurb: "Rules that move an order forward without you opening it.",
      state: "COMING_SOON"
    }
  ],

  /**
   * Customers has no live layer of its own, so its landing page is built
   * entirely from this list. The one READY row is not a courtesy entry: buyer
   * conversations are the only customer-facing tool that exists today, and
   * pointing at the real Messages screen is what keeps this landing page from
   * being a page of locks with nothing on it.
   */
  customers: [
    {
      key: "conversations",
      label: "Buyer Conversations",
      blurb: "Every message a buyer has sent you, in one thread list.",
      state: "READY",
      route: "BusinessOsMessages",
      params: { title: "Messages" }
    },
    {
      key: "customer_records",
      label: "Customer Records",
      blurb: "A profile per buyer: what they bought, what they asked, what they returned.",
      state: "COMING_SOON"
    },
    {
      key: "segments",
      label: "Segments",
      blurb: "Group buyers by behaviour and reach a group rather than a person.",
      state: "BUILDING"
    },
    {
      key: "crm",
      label: "Customer CRM Tools",
      blurb: "Follow-ups, notes and lifecycle tracking across a whole customer base.",
      state: "COMING_SOON"
    }
  ],

  messages: [],

  insights: [
    {
      key: "cohorts",
      label: "Cohort Reporting",
      blurb: "How groups of buyers behave over months, not how yesterday performed.",
      state: "COMING_SOON"
    }
  ],

  payments: [],

  events: [
    {
      key: "ticketing",
      label: "Ticketing",
      blurb: "Sell entry to an event you host, with capacity and check-in.",
      state: "COMING_SOON"
    }
  ],

  /** Like Customers: no live layer today, so the landing page is this list. */
  team: [
    {
      key: "members",
      label: "Team Members",
      blurb: "Invite people to help run the business and see who has access.",
      state: "BUILDING"
    },
    {
      key: "roles",
      label: "Roles & Permissions",
      blurb: "Decide who can publish, who can refund and who can only look.",
      state: "COMING_SOON"
    }
  ],

  verification: [],

  settings: []
};

/** Every module declared for a section, in declaration order. */
export function businessOsModules(section: BusinessOsSectionKey): readonly BusinessOsModule[] {
  return BUSINESS_OS_MODULES[section] ?? [];
}

export function isModuleReady(module: BusinessOsModule): boolean {
  return module.state === "READY";
}

/** Modules a member can open right now. */
export function readyBusinessOsModules(section: BusinessOsSectionKey) {
  return businessOsModules(section).filter(isModuleReady);
}

/** Modules that are visible, locked, and on the roadmap. */
export function lockedBusinessOsModules(section: BusinessOsSectionKey) {
  return businessOsModules(section).filter((module) => !isModuleReady(module));
}

/**
 * Whether a section needs its own generated landing page.
 *
 * True when the section has modules but no working screen behind it — Customers
 * and Team today. Everything else already has a real screen, and this layer only
 * appends a roadmap panel to it.
 */
export function hasBusinessOsModules(section: BusinessOsSectionKey): boolean {
  return businessOsModules(section).length > 0;
}

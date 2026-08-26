import type { BusinessOsSectionKey } from "../api/businessOs";

/**
 * Launch readiness — the ONE centralized answer to "is this module open?".
 *
 * Every Business OS and Presence module the hubs present is registered here
 * with exactly one status. `READY` means the module opens its real, shipped
 * feature. Anything else stays VISIBLE on its hub as a premium locked card and
 * a tap shows the Coming Soon message — never a dead button, never a hidden
 * tile, never developer language.
 *
 * This map is asserted against the real section registries in
 * `core/__tests__/launchReadiness.test.ts`: a key may only be `READY` when a
 * live, backed route actually exists, and every backed route must be `READY`.
 * That bijection is what prevents both failure modes — a locked card in front
 * of a working feature, and an "open" card in front of nothing.
 *
 * There is deliberately no other gate: screens consult these helpers and
 * nothing else, so flipping a module to `READY` here (once its route ships) is
 * the entire launch switch.
 */
export type LaunchReadiness = "READY" | "BUILDING" | "COMING_SOON";

export const BUSINESS_MODULE_READINESS: Readonly<Record<BusinessOsSectionKey, LaunchReadiness>> =
  Object.freeze({
    dashboard: "READY",
    profile: "READY",
    store: "READY",
    marketplace: "READY",
    advertising: "READY",
    orders: "READY",
    customers: "COMING_SOON",
    messages: "READY",
    insights: "READY",
    payments: "READY",
    events: "READY",
    team: "COMING_SOON",
    verification: "READY",
    settings: "READY"
  });

/**
 * Presence hub destinations. Every action on the Presence hub lands on a real,
 * shipped screen today, and the test suite pins that no presence module is
 * locked — if one ever becomes unfinished it must be added here as
 * `COMING_SOON`, not hidden.
 */
export type PresenceModuleKey =
  | "create_artist_presence"
  | "create_business_presence"
  | "view_page"
  | "manage_page"
  | "business_os"
  | "page_insights";

export const PRESENCE_MODULE_READINESS: Readonly<Record<PresenceModuleKey, LaunchReadiness>> =
  Object.freeze({
    create_artist_presence: "READY",
    create_business_presence: "READY",
    view_page: "READY",
    manage_page: "READY",
    business_os: "READY",
    page_insights: "READY"
  });

/**
 * Unknown keys are treated as not launched. Failing closed means a typo or a
 * section added without a readiness decision can never open an unfinished
 * surface — it shows Coming Soon until someone registers it as `READY`.
 */
export function businessModuleReadiness(key: string): LaunchReadiness {
  return BUSINESS_MODULE_READINESS[key as BusinessOsSectionKey] ?? "COMING_SOON";
}

export function isBusinessModuleReady(key: string): boolean {
  return businessModuleReadiness(key) === "READY";
}

export function presenceModuleReadiness(key: string): LaunchReadiness {
  return PRESENCE_MODULE_READINESS[key as PresenceModuleKey] ?? "COMING_SOON";
}

export function isPresenceModuleReady(key: string): boolean {
  return presenceModuleReadiness(key) === "READY";
}

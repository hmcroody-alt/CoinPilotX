/**
 * One seller verdict for the whole app, fetched from one endpoint.
 *
 * Every seller-only surface used to decide for itself whether the account could
 * sell, from whatever payload it happened to have loaded: Business OS opened
 * the Store tile unconditionally, the Marketplace Selling pane looked at a
 * listings response, and the Store dashboard asked nobody. So the same account
 * could be told three different things, and four production accounts holding a
 * *draft* merchant application could open a full Store dashboard.
 *
 * `GET /api/pulse/seller/access-state` is the server's single answer
 * (`services/seller_access_state.py`), and this module is the only thing that
 * reads it. Screens ask questions of the state — `canOpenStore(...)` — instead
 * of comparing status strings, because a screen that writes
 * `status === "approved"` is a screen that will miss `suspended` the day
 * suspension ships.
 *
 * Two axes, deliberately never merged:
 *
 * - `seller_application_status` governs *access to seller surfaces*.
 * - `card_payment_status` governs *eligibility to take card payments*.
 *
 * An approved seller with no Stripe Connect account gets the Store and the
 * Selling tools and cannot take cards. Collapsing those into one flag is how a
 * "temporarily unavailable" payment row turns into a locked-out seller, or
 * worse, how a card charge lands on the platform account with no seller to
 * transfer it to.
 */
import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi } from "./pulseApi";

const SELLER_ACCESS_CACHE_KEY = "pulsesoc.native.seller.access";

/** Mirrors the constants in `services/seller_access_state.py`. */
export type SellerApplicationAccess =
  | "NO_APPLICATION"
  | "DRAFT"
  | "SUBMITTED"
  | "UNDER_REVIEW"
  | "MORE_INFORMATION_REQUIRED"
  | "APPROVED"
  | "DECLINED"
  | "SUSPENDED";

export type CardPaymentStatus =
  | "SETUP_REQUIRED"
  | "SETUP_IN_PROGRESS"
  | "ACTION_REQUIRED"
  | "UNDER_REVIEW"
  | "READY"
  | "RESTRICTED"
  | "UNAVAILABLE";

export type SellerAccessState = {
  seller_application_status: SellerApplicationAccess;
  seller_approved: boolean;
  store_access: boolean;
  marketplace_selling_access: boolean;
  card_payment_status: CardPaymentStatus;
  stripe_connect_status: CardPaymentStatus;
  card_setup_actionable: boolean;
  can_manage_existing_orders: boolean;
  reason_code: string;
  application_id: number | null;
  lifecycle_status: string;
  /** True when the server could not read the gate and refused rather than guessed. */
  degraded: boolean;
};

/**
 * The state to assume before the server has answered.
 *
 * Denied, not approved. An optimistic default would flash the Store dashboard
 * to an unapproved account on every cold launch — the exact bug being fixed,
 * reintroduced as a loading state.
 */
export const DENIED_SELLER_ACCESS: SellerAccessState = {
  seller_application_status: "NO_APPLICATION",
  seller_approved: false,
  store_access: false,
  marketplace_selling_access: false,
  card_payment_status: "SETUP_REQUIRED",
  stripe_connect_status: "SETUP_REQUIRED",
  card_setup_actionable: true,
  can_manage_existing_orders: false,
  reason_code: "no_application",
  application_id: null,
  lifecycle_status: "",
  degraded: false
};

const APPLICATION_STATUSES: readonly SellerApplicationAccess[] = [
  "NO_APPLICATION",
  "DRAFT",
  "SUBMITTED",
  "UNDER_REVIEW",
  "MORE_INFORMATION_REQUIRED",
  "APPROVED",
  "DECLINED",
  "SUSPENDED"
];

const CARD_STATUSES: readonly CardPaymentStatus[] = [
  "SETUP_REQUIRED",
  "SETUP_IN_PROGRESS",
  "ACTION_REQUIRED",
  "UNDER_REVIEW",
  "READY",
  "RESTRICTED",
  "UNAVAILABLE"
];

function normalizeApplicationStatus(value: unknown): SellerApplicationAccess {
  const text = typeof value === "string" ? value.trim().toUpperCase() : "";
  return (APPLICATION_STATUSES as readonly string[]).includes(text)
    ? (text as SellerApplicationAccess)
    : "NO_APPLICATION";
}

function normalizeCardStatus(value: unknown): CardPaymentStatus {
  const text = typeof value === "string" ? value.trim().toUpperCase() : "";
  return (CARD_STATUSES as readonly string[]).includes(text)
    ? (text as CardPaymentStatus)
    : "SETUP_REQUIRED";
}

/**
 * Rebuild the state from a payload, refusing anything we do not recognise.
 *
 * An unknown status normalizes to `NO_APPLICATION` rather than being passed
 * through, so a server that grows a ninth status cannot unlock a surface on an
 * old client by accident. Access booleans are re-derived from the status rather
 * than trusted from the wire for the same reason: there is one rule, and it is
 * "approved means approved".
 */
export function parseSellerAccessState(payload: unknown): SellerAccessState {
  const row = (payload ?? {}) as Record<string, unknown>;
  const status = normalizeApplicationStatus(row.seller_application_status);
  const approved = status === "APPROVED";
  const card = normalizeCardStatus(row.card_payment_status ?? row.stripe_connect_status);
  return {
    seller_application_status: status,
    seller_approved: approved,
    store_access: approved,
    marketplace_selling_access: approved,
    card_payment_status: card,
    stripe_connect_status: card,
    card_setup_actionable: card === "SETUP_REQUIRED" || card === "ACTION_REQUIRED" || card === "SETUP_IN_PROGRESS",
    // Obligations outlive privileges: a suspended seller still owes their
    // buyers fulfilment, refunds and dispute responses.
    can_manage_existing_orders: approved || status === "SUSPENDED",
    reason_code: typeof row.reason_code === "string" ? row.reason_code : "",
    application_id: typeof row.application_id === "number" ? row.application_id : null,
    lifecycle_status: typeof row.lifecycle_status === "string" ? row.lifecycle_status : "",
    degraded: row.degraded === true
  };
}

/** Fetch the canonical state. Throws on transport failure — callers decide. */
export async function fetchSellerAccessState(): Promise<SellerAccessState> {
  const response = await pulseApi<{ ok?: boolean; seller_access?: unknown }>(
    "/api/pulse/seller/access-state",
    { method: "GET" }
  );
  const state = parseSellerAccessState(response?.seller_access);
  await writeJsonCache(SELLER_ACCESS_CACHE_KEY, state).catch(() => undefined);
  return state;
}

/**
 * The last known state, for painting before the network answers.
 *
 * Cached state is only ever used to render *sooner*, never to decide a
 * mutation: the server re-checks every seller write regardless of what this
 * says, so a stale "approved" here costs a refused request, not an escape.
 */
export async function loadCachedSellerAccessState(): Promise<SellerAccessState | null> {
  // The cache is normalized on the way out, not just on the way in: a payload
  // written by an older build is re-derived under today's rules, so a status
  // this build does not recognise reads as denied rather than as whatever
  // booleans happened to be stored beside it.
  return readJsonCache<SellerAccessState>(
    SELLER_ACCESS_CACHE_KEY,
    parseSellerAccessState
  ).catch(() => null);
}

export async function clearCachedSellerAccessState(): Promise<void> {
  await writeJsonCache(SELLER_ACCESS_CACHE_KEY, DENIED_SELLER_ACCESS).catch(() => undefined);
}

/** Can this account open the Store dashboard? */
export function canOpenStore(state: SellerAccessState | null | undefined): boolean {
  return state?.store_access === true;
}

/** Can this account use the Marketplace Selling tools? */
export function canSellOnMarketplace(state: SellerAccessState | null | undefined): boolean {
  return state?.marketplace_selling_access === true;
}

/** Can this account still work its existing orders? Suspension does not remove this. */
export function canManageExistingOrders(state: SellerAccessState | null | undefined): boolean {
  return state?.can_manage_existing_orders === true;
}

export type SellerAccessDestination =
  | "SELLER_APPLICATION"
  | "RESUME_APPLICATION"
  | "APPLICATION_STATUS"
  | "COMPLETE_REQUESTED_CHANGES"
  | "SELLER_TOOLS"
  | "RESTRICTED";

/**
 * Where a blocked seller should be sent, by status.
 *
 * The mapping is total over the status union, so adding a status to the server
 * without deciding where it routes is a TypeScript error rather than a screen
 * that silently offers "Apply" to a suspended account.
 */
export function sellerAccessDestination(state: SellerAccessState | null | undefined): SellerAccessDestination {
  switch (state?.seller_application_status ?? "NO_APPLICATION") {
    case "APPROVED":
      return "SELLER_TOOLS";
    case "DRAFT":
      return "RESUME_APPLICATION";
    case "SUBMITTED":
    case "UNDER_REVIEW":
      return "APPLICATION_STATUS";
    case "MORE_INFORMATION_REQUIRED":
      return "COMPLETE_REQUESTED_CHANGES";
    case "DECLINED":
      return "APPLICATION_STATUS";
    case "SUSPENDED":
      return "RESTRICTED";
    case "NO_APPLICATION":
    default:
      return "SELLER_APPLICATION";
  }
}

/**
 * Dropshipping — the merchant's supplier import journey, provider-neutral.
 *
 * Binds `/api/business-os/dropshipping/*`. No path, parameter or type below
 * names a provider except as a *value* on the `provider` field. CJ is the first
 * adapter, not the shape of the feature: connecting Printful must add a value,
 * not a screen.
 *
 * ## The client sends ids, never economics
 *
 * There is deliberately no function here that accepts a cost, an inventory
 * count, a supplier title or a variant identity and sends it to the server.
 * {@link importSelected} takes `itemIds` — rows in the merchant's own cart — and
 * the backend re-fetches every economic fact from the provider before it writes
 * anything. A `costCents` parameter added here "to save a round trip" would let
 * anyone who can craft a request set the merchant's cost basis, so the absence
 * is load-bearing and is pinned by a test.
 *
 * ## Unknown is not zero, and unknown is not out of stock
 *
 * The backend answers `null` for a cost it could not read and `UNKNOWN` for an
 * inventory signal that was absent. Both survive this module intact. The usual
 * way that guarantee dies is `Number(value) || 0` in a normalizer, which is
 * indistinguishable from tidying up and turns "we could not read the supplier's
 * price" into a free product with a 100% margin. Every money and count field
 * below goes through {@link centsOrNull}, which returns `null` for anything that
 * is not a finite number — including `null`, `""` and `undefined`.
 *
 * ## Supplier cost is merchant-private
 *
 * Cost, margin and provider identity appear on these types because every route
 * here is merchant-authenticated and merchant-scoped. None of it may be passed
 * to a buyer surface, a public route or a log line. The separation is enforced
 * by which module a screen imports, so a buyer screen must not import this one.
 */

import { pulseApi, PulseApiError } from "./pulseApi";

const BASE = "/api/business-os/dropshipping";
const SUPPLIERS_BASE = "/api/business-os/suppliers/cj";

/* ------------------------------------------------------------------ *
 * Coercion
 * ------------------------------------------------------------------ */

/**
 * A minor-unit amount, or `null` when the server did not have one.
 *
 * `Number` says 0 to a surprising number of things that are not zero: `null`,
 * `""`, `" "`, `false` and `[]` all coerce to 0, and every one of them reaching
 * a price field turns an absent supplier price into a free product.
 *
 * So this is an allowlist rather than a filter. Only a finite number, or a
 * string that is entirely a number, produces a number; everything else — every
 * shape the server might send when it has nothing to say — produces `null`.
 * Written this way because the blocklist form has to be right about every
 * falsy-but-not-null value JavaScript will ever have, and the allowlist form
 * only has to be right about the two that are real.
 */
export function centsOrNull(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? Math.round(value) : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? Math.round(parsed) : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

function textOrNull(value: unknown): string | null {
  const out = text(value).trim();
  return out ? out : null;
}

function list<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

/* ------------------------------------------------------------------ *
 * Scope
 * ------------------------------------------------------------------ */

/**
 * Every route is scoped to one store inside one business. Both ids are required
 * by the server and are the tenancy boundary — a merchant who owns business A
 * cannot read business B's cart by omitting the field, because the server
 * refuses rather than defaulting.
 */
export type DropshippingScope = {
  businessId: string;
  storeId: string;
};

/**
 * Why a merchant has no usable scope.
 *
 * These are the server's words, not a client interpretation, and they are three
 * different screens. `NO_STORE` is "you have never applied to sell";
 * `STORE_PENDING_REVIEW` is "we have your application" — telling that merchant
 * to go and set a store up is telling them to do something they already did.
 */
export const SCOPE_GAPS = ["NO_STORE", "STORE_PENDING_REVIEW", "NO_STOREFRONT"] as const;
export type ScopeGap = (typeof SCOPE_GAPS)[number];

/**
 * Which authority owns the merchant's store. Carried so a screen can explain
 * the right next step; it is not a permission and must not gate anything, since
 * the server decides access on every request regardless of what this says.
 */
export type ScopeSource = "MARKETPLACE_SELLER" | "BUSINESS_OS";

export type ScopeResolution =
  | { status: "ok"; scope: DropshippingScope; storeName: string; source: ScopeSource }
  | { status: "missing"; gap: ScopeGap };

function scopeGap(value: unknown): ScopeGap {
  const gap = text(value);
  return (SCOPE_GAPS as readonly string[]).includes(gap) ? (gap as ScopeGap) : "NO_STORE";
}

/**
 * Find the store these routes have to be scoped to.
 *
 * One call, to one endpoint, because the answer is one decision. This used to
 * ask Business OS for a business and then that business for a storefront, which
 * quietly made a Business OS workspace the definition of "has a store" — so a
 * merchant already trading on PulseSoc was told to go and set a business up
 * before they could connect a supplier. The server now answers from whichever
 * authority actually owns the merchant's store, and the client does not get to
 * hold a second opinion about it.
 */
export async function resolveDropshippingScope(): Promise<ScopeResolution> {
  const raw = await pulseApi<Record<string, unknown>>(`${BASE}/scope`);
  if (text(raw.status) !== "ok") return { status: "missing", gap: scopeGap(raw.gap) };

  const businessId = text(raw.business_id);
  const storeId = text(raw.store_id);
  if (!businessId || !storeId) return { status: "missing", gap: "NO_STORE" };

  return {
    status: "ok",
    scope: { businessId, storeId },
    storeName: text(raw.store_name),
    source: text(raw.source) === "BUSINESS_OS" ? "BUSINESS_OS" : "MARKETPLACE_SELLER"
  };
}

function scopeQuery(scope: DropshippingScope, extra: Record<string, string | number | undefined> = {}): string {
  const params = new URLSearchParams({ business_id: scope.businessId, store_id: scope.storeId });
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value));
  });
  return `?${params.toString()}`;
}

function scopeBody(scope: DropshippingScope, extra: Record<string, unknown> = {}): string {
  return JSON.stringify({ business_id: scope.businessId, store_id: scope.storeId, ...extra });
}

/* ------------------------------------------------------------------ *
 * Suppliers the merchant can choose from
 * ------------------------------------------------------------------ */

/**
 * A supplier the merchant can connect, and how that supplier names its own
 * credential.
 *
 * Once a supplier is chosen, every string the merchant reads is that supplier's
 * vocabulary, not ours. "Supplier access key" is our internal category; the
 * thing the merchant is holding is a *CJ API key*, and it is called that on
 * CJ's site, in CJ's documentation and in the dialog they copied it from.
 * Asking for a "supplier access key" sends someone to look for a control that
 * does not exist under that name.
 *
 * `helpSteps` name CJ's own controls (`Apps`, `API`, `Add API`, `Type`,
 * `API Key`) because those are the words on the buttons. They are taken from
 * CJ's published API documentation rather than composed here — an invented path
 * is how the previous copy came to send merchants to an "Account → API" menu
 * and a "create a new access key" control, neither of which CJ has.
 */
export type SupplierProviderInfo = {
  id: string;
  name: string;
  /** Shown on the supplier-specific step, in the supplier's own words. */
  blurb: string;
  /** What this supplier calls the credential. Used for labels and placeholder. */
  credentialName: string;
  helpSteps: readonly string[];
  /** What we do with the credential, stated before they paste it. */
  securityNote: string;
  /** The action, in the merchant's terms — not "discover shops". */
  connectCta: string;
};

/**
 * The suppliers this app can actually connect today.
 *
 * A list rather than a hardcoded CJ screen because the second adapter must add
 * an entry, not a screen. Everything downstream of the picker — the key step,
 * the shop list, the connection — is written against whichever entry the
 * merchant chose, so nothing here says CJ except the data.
 */
export const SUPPLIER_PROVIDERS: readonly SupplierProviderInfo[] = [
  {
    id: "CJ",
    name: "CJ Dropshipping",
    blurb:
      "Connect your CJ account to browse supplier products and import them into your PulseSoc Store.",
    credentialName: "CJ API key",
    helpSteps: [
      "Sign in to your CJ Dropshipping account.",
      "Under Apps, install the API app if you haven't already.",
      "Open the API page and press Add API.",
      "Enter a name, choose API Key as the Type, then confirm.",
      "Copy the API Key from the list and paste it here."
    ],
    // Says the same thing as "used only from the backend" without the word:
    // the guarantee a merchant needs is that the key is not kept on the phone
    // and is not used from it. See EXTERNAL_VOCABULARY in userFacingCopy.
    securityNote:
      "PulseSoc encrypts your CJ API key and never keeps it on this device. It is used only to connect your CJ account.",
    connectCta: "Connect to CJ"
  }
];

/**
 * Suppliers that are planned and cannot be connected yet.
 *
 * Named, and visibly not connectable. A merchant who is waiting for Printful
 * deserves to know it is coming; a merchant who taps a live-looking button and
 * lands nowhere learns the app is unreliable. Listing them without a control is
 * the only version of this that is true today.
 */
export const PLANNED_SUPPLIER_PROVIDERS: readonly string[] = ["Printful", "Printify"];

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/**
 * Availability as the supplier layer reports it. `UNKNOWN` is a real answer and
 * is not a synonym for `UNAVAILABLE` — a variant nobody could get a reading for
 * is not a variant that sold out, and rendering it as sold out is how a provider
 * outage becomes an empty storefront.
 */
export const SUPPLIER_AVAILABILITY = ["AVAILABLE", "UNAVAILABLE", "UNKNOWN"] as const;
export type SupplierAvailability = (typeof SUPPLIER_AVAILABILITY)[number];

/** Storage-level stock, the only three words the listing ledger accepts. */
export const SUPPLIER_STOCK_STATES = ["IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"] as const;
export type SupplierStockState = (typeof SUPPLIER_STOCK_STATES)[number];

/**
 * Margin as a named state rather than a number, so a caller cannot accidentally
 * decide that "no margin reading" is a low number. `UNKNOWN` appears whenever
 * either side of the subtraction is missing.
 */
export const MARGIN_STATES = ["HEALTHY", "LOW_MARGIN", "CRITICAL_MARGIN", "NEGATIVE_MARGIN", "UNKNOWN"] as const;
export type MarginState = (typeof MARGIN_STATES)[number];

/** Pricing rules the server will apply. Mirrors `suppliers.pricing.RULES`. */
export const PRICING_RULES = [
  "MANUAL_PRICE",
  "COST_PLUS_FIXED",
  "COST_PLUS_PERCENT",
  "MULTIPLIER",
  "TARGET_MARGIN"
] as const;
export type PricingRuleType = (typeof PRICING_RULES)[number];

export type PricingRule = { type: PricingRuleType; value?: number };

/**
 * Where the rule an import priced with came from. Mirrors `store_policy.SOURCE_*`.
 *
 * Worth rendering rather than hiding. An auto-priced import raises exactly one
 * question — "why is this $14.91" — and the three answers need three different
 * replies: you asked for this rule, your store is set to this, or nobody has set
 * anything and PulseSoc used its default. Only the third is an invitation to go
 * and configure something.
 */
export const PRICING_SOURCES = ["REQUEST", "STORE", "PLATFORM_DEFAULT"] as const;
export type PricingSource = (typeof PRICING_SOURCES)[number];

function normalizePricingSource(raw: unknown): PricingSource {
  const value = text(raw) as PricingSource;
  // Defaults to the platform, not to `STORE`. An unreadable source is one nobody
  // has verifiably configured, and claiming the merchant chose it is the worse
  // of the two mistakes: it hides the settings screen they need.
  return PRICING_SOURCES.includes(value) ? value : "PLATFORM_DEFAULT";
}

/**
 * Per-item outcomes of a bulk import. A bulk import reports each item honestly
 * rather than collapsing to one verdict: nine successes and one refusal is not
 * a failed import, and it is not a clean one either.
 */
export const IMPORT_OUTCOMES = [
  "IMPORTED",
  "ALREADY_EXISTS",
  "PROVIDER_UNAVAILABLE",
  "INVALID_PRODUCT",
  "NO_VARIANTS",
  "NO_MEDIA",
  "RESTRICTED",
  "NEEDS_REVIEW",
  // The two outcomes "Import to Store" added. `PUBLISHED` is the ordinary one
  // now — the product is priced, live and sellable with no further tap — and
  // `NEEDS_ATTENTION` is its counterpart: the listing exists in the store as a
  // draft, and `problems` says what stopped it going live.
  //
  // `NEEDS_ATTENTION` is distinct from `NEEDS_REVIEW` on purpose. Review is the
  // *platform's* pending moderation, which the merchant can do nothing about and
  // must not be asked to. Attention is the merchant's own: a missing price, an
  // unbound variant, something they can go and fix.
  "PUBLISHED",
  "NEEDS_ATTENTION"
] as const;
export type ImportOutcome = (typeof IMPORT_OUTCOMES)[number];

/**
 * Why a draft cannot be published yet. Every reason, not the first one.
 *
 * This is the second copy of an enumeration whose first copy is
 * `services/business_os/suppliers/drafts.py`. The two are in different
 * languages, so no compiler spans them, and they drifted: the backend grew
 * `VARIANT_PRICE_SPREAD`, `PRICE_ABOVE_CHECKOUT_LIMIT` and
 * `SUPPLIER_VARIANT_UNBOUND` and this list did not. A problem missing from here
 * is missing from `PROBLEM_COPY` too, and `ReviewImportedProductScreen` renders
 * an unknown code verbatim — so the merchant whose default import could not be
 * published read the words "SUPPLIER_VARIANT_UNBOUND" and nothing else.
 *
 * `tests/dropshipping/test_publish_problem_copy.py` pins this list against the
 * Python one, because a list only the backend can grow needs a check on the
 * side that cannot see it growing.
 */
export const PUBLISH_PROBLEMS = [
  "MISSING_TITLE",
  "MISSING_CATEGORY",
  "NO_VALID_MEDIA",
  "NO_VARIANTS_SELECTED",
  "MISSING_PRICE",
  "NEGATIVE_MARGIN",
  "UNKNOWN_INVENTORY",
  "SUPPLIER_DISCONNECTED",
  "PROVIDER_PRODUCT_UNAVAILABLE",
  "RESTRICTED_PRODUCT",
  "VARIANT_PRICE_SPREAD",
  "PRICE_ABOVE_CHECKOUT_LIMIT",
  "SUPPLIER_VARIANT_UNBOUND",
  // The two the publish read-back can emit. Not gate refusals — the gate passed
  // and the row then failed to say so — but they arrive in the same `problems`
  // field, on the same import result, and render through the same table.
  "PUBLISH_NOT_PERSISTED",
  "SUPPLIER_MAPPING_MISSING"
] as const;
export type PublishProblem = (typeof PUBLISH_PROBLEMS)[number];

/**
 * What a supplier read concluded needs a human, on a listing that is already live.
 *
 * Deliberately *not* `PUBLISH_PROBLEMS`, though the temptation to reuse it is
 * strong and the two lists even share the string "NEGATIVE_MARGIN" in spirit. A
 * publish problem describes a product that never went live and whose fix is
 * "finish it". These describe a product that is live right now, that a stranger
 * can buy this second, and whose fix is usually "stop selling it or change the
 * price". Rendering them through one table would tell a merchant their published
 * product "could not be published".
 *
 * Mirrors `revisions.ATTENTION_REASONS`. `tests/dropshipping/test_revision_attention_copy.py`
 * pins this list against the Python one, for the same reason the publish list is
 * pinned: only the backend can grow it, and nothing on this side would notice.
 */
export const REVISION_ATTENTION = [
  "SELLING_BELOW_COST",
  "MARGIN_LOST",
  "COST_UNAVAILABLE",
  "REPRICE_IMPOSSIBLE",
  "SUPPLIER_OUT_OF_STOCK",
  "STOCK_UNREADABLE"
] as const;
export type RevisionAttention = (typeof REVISION_ATTENTION)[number];

/**
 * Merchant-facing copy for each reason, with what to do about it.
 *
 * `severity` follows the same rule the sync screen already applies to sync
 * states: "fix" means there is an action this merchant can take today, "info"
 * means the situation is real, worth knowing, and not theirs to resolve. Grading
 * an unreadable stock count as "fix" would put an item in a list titled "Needs
 * you" that the merchant can only stare at, and a list like that stops being read.
 */
export const REVISION_ATTENTION_COPY: Record<
  RevisionAttention,
  { text: string; action: string; severity: "fix" | "info" }
> = {
  SELLING_BELOW_COST: {
    text: "Selling below what the supplier charges",
    action: "Every sale loses money. Raise the price or unpublish it.",
    severity: "fix"
  },
  MARGIN_LOST: {
    text: "Almost no margin left",
    action: "The supplier's cost rose. Raise the price to restore your margin.",
    severity: "fix"
  },
  COST_UNAVAILABLE: {
    text: "Supplier cost is unknown",
    action: "We cannot work out your margin, so we cannot tell you if this is profitable.",
    severity: "info"
  },
  REPRICE_IMPOSSIBLE: {
    text: "Your pricing rule could not be applied",
    action: "The old price is still live. Set a price yourself to take control of it.",
    severity: "fix"
  },
  SUPPLIER_OUT_OF_STOCK: {
    text: "The supplier has none left",
    action: "Orders cannot be fulfilled. Unpublish it until stock returns.",
    severity: "fix"
  },
  STOCK_UNREADABLE: {
    text: "Stock could not be read",
    action: "The last count still stands. We will try again on the next sync.",
    severity: "info"
  }
};

/** Drops anything this build has no copy for, rather than showing a raw code. */
export function revisionAttention(value: unknown): RevisionAttention[] {
  const seen = list<unknown>(value)
    .map((entry) => text(entry).toUpperCase())
    .filter((entry): entry is RevisionAttention =>
      (REVISION_ATTENTION as readonly string[]).includes(entry));
  // Declaration order, not arrival order, so two products with the same problems
  // list them the same way down a screen.
  return REVISION_ATTENTION.filter((reason) => seen.includes(reason));
}

/* ------------------------------------------------------------------ *
 * Supplier connections
 * ------------------------------------------------------------------ */

export type SupplierConnection = {
  id: string;
  provider: string;
  status: string;
  externalShopId: string | null;
  environment: string;
  /** Server's word. Always false while the funding kill-switch is off. */
  productionFulfillmentEnabled: boolean;
  lastVerifiedAt: string | null;
  lastSyncAt: string | null;
  /** Present only when the provider told us why access is blocked. */
  message: string | null;
};

function normalizeConnection(raw: Record<string, unknown>): SupplierConnection {
  return {
    id: text(raw.id),
    // Lowercased because `provider` is a *value* everywhere else in this module
    // — the route parameter, the adapter key and the cart row all use "cj".
    provider: text(raw.provider).toLowerCase() || "cj",
    status: text(raw.status) || "UNKNOWN",
    externalShopId: textOrNull(raw.external_shop_id),
    environment: text(raw.environment) || "SANDBOX",
    productionFulfillmentEnabled: raw.production_fulfillment_enabled === true,
    lastVerifiedAt: textOrNull(raw.last_verified_at),
    lastSyncAt: textOrNull(raw.last_sync_at),
    message: textOrNull(raw.message)
  };
}

/**
 * Connections a merchant has already made for this store.
 *
 * This is the one call that still goes to the provider-shaped supplier pack:
 * the *connection* layer beneath these screens is CJ-only today (its query
 * filters `provider='CJ'`). Everything downstream of it is neutral, so widening
 * it later changes this function and nothing else.
 */
export async function listSupplierConnections(scope: DropshippingScope): Promise<SupplierConnection[]> {
  const response = await pulseApi<{ connections?: unknown[] }>(`${SUPPLIERS_BASE}/connections${scopeQuery(scope)}`);
  return list<Record<string, unknown>>(response.connections).map(normalizeConnection);
}

export type SupplierShop = { externalShopId: string; name: string | null };

/**
 * Shops the supplier account can sell through, given a credential.
 *
 * Two steps rather than one — discover, then connect — because a merchant who
 * pastes a key and immediately gets "connected to shop 4471" has no idea whether
 * that is the right shop. The credential is sent once per step and is never
 * stored on the device: it goes into the request body and the server keeps it,
 * encrypted, against the connection it creates.
 */
export async function discoverSupplierShops(
  scope: DropshippingScope,
  apiKey: string
): Promise<SupplierShop[]> {
  const response = await pulseApi<{ data?: unknown }>(`${SUPPLIERS_BASE}/discover-shops`, {
    method: "POST",
    body: scopeBody(scope, { api_key: apiKey })
  });
  const data = response.data;
  const rows = Array.isArray(data) ? data : list<unknown>((data as Record<string, unknown>)?.shops);
  return rows
    .map((raw) => {
      const shop = (raw || {}) as Record<string, unknown>;
      return {
        externalShopId: text(shop.external_shop_id ?? shop.shop_id ?? shop.id),
        name: textOrNull(shop.name ?? shop.shop_name)
      };
    })
    .filter((shop) => Boolean(shop.externalShopId));
}

/**
 * Create the connection.
 *
 * Returns whatever the server says about the new connection; callers re-list
 * rather than trusting this shape, because the list is what every other screen
 * reads and one source for "what connections exist" is worth the extra call.
 */
export async function connectSupplier(
  scope: DropshippingScope,
  input: { apiKey: string; externalShopId?: string | null }
): Promise<void> {
  // The shop is omitted, not sent empty, when there is none. A supplier "shop"
  // is an external storefront authorized inside the merchant's own supplier
  // account; selling here means PulseSoc is the storefront, so a valid account
  // may have none. An empty string would read as "a shop, named nothing" and be
  // checked against a live list it cannot possibly appear in.
  const shop = input.externalShopId?.trim();
  await pulseApi(`${SUPPLIERS_BASE}/connect`, {
    method: "POST",
    body: scopeBody(scope, shop ? { api_key: input.apiKey, external_shop_id: shop } : { api_key: input.apiKey })
  });
}

/** Re-run the provider's credential check for one connection. */
export async function checkConnectionHealth(
  scope: DropshippingScope,
  connectionId: string
): Promise<void> {
  await pulseApi(`${SUPPLIERS_BASE}/connections/${encodeURIComponent(connectionId)}/health`, {
    method: "POST",
    body: scopeBody(scope)
  });
}

/**
 * One shop on an existing connection, with the server's verdict attached.
 *
 * `fulfillable` is not computed here and must not be. It is the fulfilment
 * layer's own `dispatch_shop` answer for that shop, returned per row, so the
 * list a merchant chooses from and the check the choice is spent against cannot
 * disagree. A client that re-derived it from `platform` and `status` would be a
 * second copy of a rule that already exists, and the copies drift.
 */
export type ConnectionShop = {
  externalShopId: string;
  name: string | null;
  platform: string | null;
  fulfillable: boolean;
  /** Why not, when not. Null whenever `fulfillable` is true. */
  unfulfillableReason: string | null;
};

export type ConnectionShopList = {
  shops: ConnectionShop[];
  /** The shop already bound, or null. A connection may legitimately have none. */
  boundShopId: string | null;
};

/**
 * The shops an *existing* connection can see, read through its stored key.
 *
 * `discoverSupplierShops` cannot answer this. It takes an API key, and the key
 * only exists while the connect form is on screen — afterwards it is in the
 * server's vault and the merchant has no copy to retype. Without this call
 * there was no way to see the list a second time, and therefore no way to
 * choose from it, which is precisely why `bindConnectionShop` below had a
 * route, a service and tests but nothing that could ever reach it.
 *
 * An empty list is a real answer, not a failure. A supplier account that owns
 * no external storefront is the normal shape for selling here, because PulseSoc
 * *is* the storefront.
 */
export async function listConnectionShops(
  scope: DropshippingScope,
  connectionId: string
): Promise<ConnectionShopList> {
  const response = await pulseApi<{ data?: Record<string, unknown> }>(
    `${SUPPLIERS_BASE}/connections/${encodeURIComponent(connectionId)}/shops`,
    { method: "POST", body: scopeBody(scope) }
  );
  const data = response.data || {};
  return {
    shops: list<Record<string, unknown>>(data.shops).map((raw) => ({
      externalShopId: text(raw.shop_id ?? raw.external_shop_id),
      name: textOrNull(raw.name),
      platform: textOrNull(raw.platform),
      // `=== true` rather than truthiness: a server that omitted the field
      // would otherwise read as "cannot fulfil", which is the safe direction,
      // but a string "false" would read as "can", which is not.
      fulfillable: raw.fulfillable === true,
      unfulfillableReason: textOrNull(raw.unfulfillable_reason)
    })).filter((shop) => Boolean(shop.externalShopId)),
    boundShopId: textOrNull(data.external_shop_id)
  };
}

/**
 * Choose the shop this connection fulfils through.
 *
 * None-to-one only; the server refuses to replace an existing binding, because
 * orders already created carry the shop they were created against. Callers
 * re-list afterwards rather than trusting the response, for the same reason
 * `connectSupplier` does: the list is what every other surface reads.
 */
export async function bindConnectionShop(
  scope: DropshippingScope,
  connectionId: string,
  externalShopId: string
): Promise<void> {
  await pulseApi(`${SUPPLIERS_BASE}/connections/${encodeURIComponent(connectionId)}/bind-shop`, {
    method: "POST",
    body: scopeBody(scope, { external_shop_id: externalShopId })
  });
}

/**
 * Statuses that mean the merchant has to do something before this connection
 * can serve a catalogue. Shared so the Suppliers list, the Find Products empty
 * state and the draft banner cannot disagree about what "connected" means.
 */
const ACTIONABLE_CONNECTION_STATUSES = [
  "AUTH_EXPIRED",
  "REAUTH_REQUIRED",
  "VERIFICATION_REQUIRED",
  "API_SUSPENDED",
  "REACTIVATION_REQUIRED",
  "DISCONNECTED",
  "REVOKED"
];

export function connectionNeedsAttention(connection: SupplierConnection): boolean {
  return ACTIONABLE_CONNECTION_STATUSES.includes(connection.status.toUpperCase());
}

export function connectionIsUsable(connection: SupplierConnection): boolean {
  return connection.status.toUpperCase() === "CONNECTED";
}

/**
 * Whether this connection can actually place a supplier order.
 *
 * Deliberately separate from `connectionIsUsable`, and deliberately narrower.
 * Importing, pricing and publishing need no supplier shop at all — that is why
 * connecting without one is allowed, and collapsing the two would re-break the
 * account shape that decision exists to support. Fulfilling does need one:
 * `create_intent` refuses an unbound connection outright.
 *
 * Which means "CONNECTED" was, on its own, answering a question it had not been
 * asked. A merchant read "Connected and working" off a connection that would
 * refuse every order it ever received, and found out at the first one.
 */
export function connectionCanFulfil(connection: SupplierConnection): boolean {
  return connectionIsUsable(connection) && Boolean(connection.externalShopId);
}

/* ------------------------------------------------------------------ *
 * Supplier status — the one health answer
 * ------------------------------------------------------------------ */

/**
 * The single most upstream thing the merchant has left to do.
 *
 * Ordered by what blocks what, and chosen by the server. A client picking its
 * own order would send a merchant with expired credentials to go and fix three
 * out-of-stock products, using stock readings taken before the credentials
 * expired — work they would do twice.
 */
export const SUPPLIER_NEXT_ACTIONS = [
  "RECONNECT_SUPPLIER",
  "CHOOSE_FULFILLMENT_SHOP",
  "RETRY_SYNC",
  "RESOLVE_PRODUCT_ISSUES",
  "IMPORT_FIRST_PRODUCT",
  "REVIEW_DRAFTS"
] as const;
export type SupplierNextAction = (typeof SUPPLIER_NEXT_ACTIONS)[number];

/**
 * Whether a fulfilment shop has been chosen, as a state rather than as "is the
 * id empty". The id is stored NOT NULL, so "never chosen" and "chosen, then the
 * provider stopped offering it" are both the empty string on the wire and a
 * client testing truthiness cannot tell them apart.
 */
export const FULFILLMENT_SHOP_STATES = ["BOUND", "NOT_SELECTED"] as const;
export type FulfillmentShopState = (typeof FULFILLMENT_SHOP_STATES)[number];

export type SupplierProductCounts = {
  imported: number;
  published: number;
  awaitingReview: number;
  draft: number;
  blocked: number;
  archived: number;
  other: number;
};

export type SupplierIssueCounts = {
  /** Products flagged for anything at all. One product with two problems is one. */
  products: number;
  cost: number;
  stock: number;
};

export type SupplierOrderCounts = {
  /** Paid sales with no supplier purchase behind them yet. */
  awaitingSupplierOrder: number;
  /** Of those, the ones the merchant can place right now. */
  readyToPlace: number;
  /** Of those, the ones held up by something they must fix first. */
  blocked: number;
  placed: number;
};

export type SupplierStatus = {
  connectionId: string;
  provider: string;
  connectionState: string;
  message: string | null;
  environment: string;
  realOrderSubmissionEnabled: boolean;
  fulfillmentShopState: FulfillmentShopState;
  externalShopId: string | null;
  credentialPresent: boolean;
  lastVerifiedAt: string | null;
  lastSyncAt: string | null;
  /**
   * When the *catalogue* last moved, which is a different clock from
   * `lastSyncAt`: one connection-level call can succeed while every product row
   * stays untouched.
   */
  lastProductSyncAt: string | null;
  products: SupplierProductCounts;
  /** Worst sync state across this connection's products, or null when it has none. */
  syncState: string | null;
  issues: SupplierIssueCounts;
  /**
   * Null when the server could not read the fulfilment tables — not zero. "No
   * orders are waiting" is a claim about the merchant's sales, and a read that
   * did not happen cannot support it.
   */
  orders: SupplierOrderCounts | null;
  nextAction: SupplierNextAction | null;
  needsAttention: boolean;
};

export type StoreSupplierStatus = {
  environment: string;
  realOrderSubmissionEnabled: boolean;
  suppliers: SupplierStatus[];
  needsAttention: boolean;
};

function count(value: unknown): number {
  const parsed = centsOrNull(value);
  return parsed !== null && parsed >= 0 ? parsed : 0;
}

/**
 * Null unless the server sent an object. Every other normalizer here defaults a
 * missing number to zero, which is right for a count of products the merchant
 * owns and wrong for a count of orders they owe: zero is the reassuring answer,
 * so an absent block must not be spelled as one.
 */
function orderCounts(value: unknown): SupplierOrderCounts | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  return {
    awaitingSupplierOrder: count(raw.awaiting_supplier_order),
    readyToPlace: count(raw.ready_to_place),
    blocked: count(raw.blocked),
    placed: count(raw.placed)
  };
}

function nextAction(value: unknown): SupplierNextAction | null {
  const name = text(value).toUpperCase();
  return (SUPPLIER_NEXT_ACTIONS as readonly string[]).includes(name)
    ? (name as SupplierNextAction)
    : null;
}

/**
 * Every default below leans the same way: toward "not proven healthy".
 *
 * A missing field is not evidence of health, and this payload's whole purpose is
 * to stop screens claiming a supplier works when nothing checked. So
 * `needsAttention` is `!== false` rather than `=== true` — an absent field means
 * a server that cannot answer, and nagging a working merchant is recoverable
 * where a green badge over a dead connection is not. `environment` falls back to
 * SANDBOX and `realOrderSubmissionEnabled` to false for the same reason: the
 * failure mode of guessing wrong must never be "we told them real orders ship".
 */
function normalizeSupplierStatus(raw: Record<string, unknown>): SupplierStatus {
  const products = (raw.products || {}) as Record<string, unknown>;
  const issues = (raw.issues || {}) as Record<string, unknown>;
  return {
    connectionId: text(raw.connection_id),
    provider: text(raw.provider).toLowerCase() || "unknown",
    connectionState: text(raw.connection_state).toUpperCase() || "UNKNOWN",
    message: textOrNull(raw.message),
    environment: text(raw.environment).toUpperCase() || "SANDBOX",
    realOrderSubmissionEnabled: raw.real_order_submission_enabled === true,
    fulfillmentShopState: raw.fulfillment_shop_state === "BOUND" ? "BOUND" : "NOT_SELECTED",
    externalShopId: textOrNull(raw.external_shop_id),
    credentialPresent: raw.credential_present === true,
    lastVerifiedAt: textOrNull(raw.last_verified_at),
    lastSyncAt: textOrNull(raw.last_sync_at),
    lastProductSyncAt: textOrNull(raw.last_product_sync_at),
    products: {
      imported: count(products.imported),
      published: count(products.published),
      awaitingReview: count(products.awaiting_review),
      draft: count(products.draft),
      blocked: count(products.blocked),
      archived: count(products.archived),
      other: count(products.other)
    },
    // Null, not "SYNCED": a connection with no products has no sync state, and
    // saying it is synced is the fabrication this endpoint exists to end.
    syncState: textOrNull(raw.sync_state)?.toUpperCase() ?? null,
    issues: {
      products: count(issues.products),
      cost: count(issues.cost),
      stock: count(issues.stock)
    },
    orders: orderCounts(raw.orders),
    nextAction: nextAction(raw.next_action),
    needsAttention: raw.needs_attention !== false
  };
}

/**
 * Everything every dropshipping surface needs to describe supplier health.
 *
 * One call, because the alternative is what this replaced: screens holding the
 * connections list and the products list, neither of which contains "is this
 * supplier working", inferring it — three screens, three rules, three answers
 * about the same connection at the same moment.
 *
 * `environment` and `realOrderSubmissionEnabled` are repeated at the top level
 * on purpose. They are platform-wide, and a client reading them off whichever
 * connection sorted first would report them per-supplier, so a second supplier
 * would appear to have different permissions than the first.
 */
export async function getSupplierStatus(scope: DropshippingScope): Promise<StoreSupplierStatus> {
  const response = await pulseApi<{
    environment?: unknown;
    real_order_submission_enabled?: unknown;
    suppliers?: unknown[];
    needs_attention?: unknown;
  }>(`${BASE}/supplier-status${scopeQuery(scope)}`);
  const suppliers = list<Record<string, unknown>>(response.suppliers).map(normalizeSupplierStatus);
  return {
    environment: text(response.environment).toUpperCase() || "SANDBOX",
    realOrderSubmissionEnabled: response.real_order_submission_enabled === true,
    suppliers,
    // The server's own rollup when it sent one. Re-scanning the list here would
    // be a second implementation of "is anything wrong", which is the defect.
    needsAttention:
      response.needs_attention !== undefined
        ? response.needs_attention !== false
        : suppliers.some((supplier) => supplier.needsAttention)
  };
}

export type SupplierResync = {
  queuedProducts: number;
  queuedJobs: number;
  truncated: boolean;
  maxProducts: number;
};

/**
 * Ask for this supplier's data to be re-read now.
 *
 * Resolves when the work is *queued*, not when it is done — the background
 * worker drains it. So the caller must not tell the merchant "synced"; the
 * honest sentence is that a refresh has started, and the fresh figures arrive
 * on a later {@link getSupplierStatus}.
 *
 * `truncated` is passed through rather than hidden. A merchant with a catalogue
 * larger than one request may enqueue, told simply "syncing", would go looking
 * for a failure that is really a cap.
 */
export async function requestSupplierResync(
  scope: DropshippingScope,
  connectionId: string
): Promise<SupplierResync> {
  const response = await pulseApi<{
    queued_products?: unknown;
    queued_jobs?: unknown;
    truncated?: unknown;
    max_products?: unknown;
  }>(`${BASE}/connections/${encodeURIComponent(connectionId)}/sync`, {
    method: "POST",
    body: scopeBody(scope)
  });
  return {
    queuedProducts: count(response.queued_products),
    queuedJobs: count(response.queued_jobs),
    truncated: response.truncated === true,
    maxProducts: count(response.max_products)
  };
}

/* ------------------------------------------------------------------ *
 * Catalogue browse
 * ------------------------------------------------------------------ */

/**
 * One search result. Thin on purpose: the provider's search endpoint returns a
 * summary without variants, and the backend does not fire fifty extra calls to
 * fill a grid the merchant will scroll past. Variants arrive on detail.
 *
 * Nothing here is trusted later. These numbers are a preview; the import
 * re-fetches all of them, which is why a stale price on a card cannot become a
 * wrong price in the store.
 */
export type SupplierProductCard = {
  provider: string;
  externalProductId: string;
  title: string;
  category: string | null;
  coverImageUrl: string | null;
  costLowCents: number | null;
  costHighCents: number | null;
  currency: string | null;
  origin: string | null;
  variantCount: number | null;
};

function normalizeCard(raw: Record<string, unknown>): SupplierProductCard {
  return {
    provider: text(raw.provider).toLowerCase(),
    externalProductId: text(raw.external_product_id),
    title: text(raw.title),
    category: textOrNull(raw.category),
    coverImageUrl: textOrNull(raw.cover_image_url),
    costLowCents: centsOrNull(raw.cost_low_cents),
    costHighCents: centsOrNull(raw.cost_high_cents),
    currency: textOrNull(raw.currency),
    origin: textOrNull(raw.origin),
    variantCount: centsOrNull(raw.variant_count)
  };
}

export type SupplierSearchResult = {
  products: SupplierProductCard[];
  page: number;
  size: number;
  /**
   * The provider's own count, or `null`. Never derived from page length — a
   * count invented from `products.length` reads as authoritative ("1–20 of 20")
   * while being wrong for every page but the last.
   */
  total: number | null;
  hasMore: boolean;
  cached: boolean;
};

export type SupplierSearchFilters = {
  keyword?: string;
  categoryId?: string;
  countryCode?: string;
};

export async function searchSupplierProducts(
  scope: DropshippingScope,
  connectionId: string,
  options: { filters?: SupplierSearchFilters; page?: number; size?: number; provider?: string } = {}
): Promise<SupplierSearchResult> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/search`,
    {
      // POST rather than GET: search filters are merchant business intelligence
      // and have no business sitting in an access log or a history entry.
      method: "POST",
      body: scopeBody(scope, {
        filters: options.filters || {},
        page: options.page ?? 1,
        size: options.size ?? 20,
        provider: options.provider || "cj"
      })
    }
  );
  return {
    products: list<Record<string, unknown>>(response.products).map(normalizeCard),
    page: centsOrNull(response.page) ?? 1,
    size: centsOrNull(response.size) ?? 20,
    total: centsOrNull(response.total),
    hasMore: response.has_more === true,
    cached: response.cached === true
  };
}

export type SupplierVariant = {
  providerVariantId: string;
  variantKey: string;
  options: Record<string, string>;
  sku: string | null;
  costCents: number | null;
  currency: string | null;
  stockState: SupplierStockState | string;
  stockQuantity: number | null;
  availability: SupplierAvailability | string;
  imageUrl: string | null;
};

function normalizeVariant(raw: Record<string, unknown>): SupplierVariant {
  const options = raw.options;
  return {
    providerVariantId: text(raw.provider_variant_id ?? raw.external_variant_id),
    variantKey: text(raw.variant_key),
    options: options && typeof options === "object" && !Array.isArray(options)
      ? Object.fromEntries(Object.entries(options as Record<string, unknown>).map(([k, v]) => [k, text(v)]))
      : {},
    sku: textOrNull(raw.sku),
    costCents: centsOrNull(raw.cost_cents),
    currency: textOrNull(raw.currency),
    // Passed through, not defaulted. A word this app does not recognise renders
    // as unknown; it must never fall back to OUT_OF_STOCK.
    stockState: text(raw.stock_state) || "UNKNOWN",
    stockQuantity: centsOrNull(raw.stock_quantity),
    availability: text(raw.availability) || "UNKNOWN",
    imageUrl: textOrNull(raw.image_url)
  };
}

export type SupplierProductDetail = {
  provider: string;
  externalProductId: string;
  title: string;
  description: string | null;
  category: string | null;
  coverImageUrl: string | null;
  media: string[];
  currency: string | null;
  origin: string | null;
  variants: SupplierVariant[];
  costLowCents: number | null;
  costHighCents: number | null;
  /**
   * False when the live inventory read failed. The catalogue's own stock signal
   * then stands — including `UNKNOWN` — rather than degrading to out of stock,
   * which would present a provider outage as a sold-out product.
   */
  inventoryFresh: boolean;
  cached: boolean;
};

export async function getSupplierProduct(
  scope: DropshippingScope,
  connectionId: string,
  externalProductId: string,
  options: { provider?: string } = {}
): Promise<SupplierProductDetail> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/products/${encodeURIComponent(externalProductId)}` +
      scopeQuery(scope, { provider: options.provider || "cj" })
  );
  const product = (response.product || {}) as Record<string, unknown>;
  return {
    provider: text(product.provider).toLowerCase(),
    externalProductId: text(product.external_product_id),
    title: text(product.title),
    description: textOrNull(product.description),
    category: textOrNull(product.category),
    coverImageUrl: textOrNull(product.cover_image_url),
    media: list<unknown>(product.media).map(text).filter(Boolean),
    currency: textOrNull(product.currency),
    origin: textOrNull(product.origin),
    variants: list<Record<string, unknown>>(product.variants).map(normalizeVariant),
    costLowCents: centsOrNull(response.cost_low_cents),
    costHighCents: centsOrNull(response.cost_high_cents),
    inventoryFresh: response.inventory_fresh !== false,
    cached: response.cached === true
  };
}

/* ------------------------------------------------------------------ *
 * Import cart
 * ------------------------------------------------------------------ */

/**
 * A cart row's cached summary. Bounded on purpose — the full normalized product
 * carries every variant with dimensions and warehouse strings, and a hundred of
 * those in one response is a payload no phone needs to draw a list of cards.
 */
export type ImportCartPreview = {
  title: string;
  coverImageUrl: string | null;
  category: string | null;
  origin: string | null;
  currency: string | null;
  variantCount: number | null;
  costLowCents: number | null;
  costHighCents: number | null;
  availability: SupplierAvailability | string;
};

export type ImportCartItem = {
  itemId: string;
  connectionId: string;
  provider: string;
  externalProductId: string;
  selectedVariantIds: string[];
  /** `null` when nothing was cached — a card with no preview, not a blank card. */
  preview: ImportCartPreview | null;
  /**
   * True when the cached preview is old enough that its numbers should not be
   * shown as current. The import re-fetches regardless, so staleness is a
   * display concern and never a correctness one.
   */
  stale: boolean;
  createdAt: string | null;
  updatedAt: string | null;
};

function normalizePreview(raw: unknown): ImportCartPreview | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const cached = raw as Record<string, unknown>;
  return {
    title: text(cached.title),
    coverImageUrl: textOrNull(cached.cover_image_url),
    category: textOrNull(cached.category),
    origin: textOrNull(cached.origin),
    currency: textOrNull(cached.currency),
    variantCount: centsOrNull(cached.variant_count),
    costLowCents: centsOrNull(cached.cost_low_cents),
    costHighCents: centsOrNull(cached.cost_high_cents),
    availability: text(cached.availability) || "UNKNOWN"
  };
}

function normalizeCartItem(raw: Record<string, unknown>): ImportCartItem {
  return {
    itemId: text(raw.item_id),
    connectionId: text(raw.connection_id),
    provider: text(raw.provider).toLowerCase(),
    externalProductId: text(raw.external_product_id),
    selectedVariantIds: list<unknown>(raw.selected_variant_ids).map(text).filter(Boolean),
    preview: normalizePreview(raw.cached),
    stale: raw.cached_stale === true,
    createdAt: textOrNull(raw.created_at),
    updatedAt: textOrNull(raw.updated_at)
  };
}

export type ImportCart = {
  items: ImportCartItem[];
  count: number;
  staleCount: number;
  maxItems: number;
};

export async function getImportCart(scope: DropshippingScope, connectionId: string): Promise<ImportCart> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/cart${scopeQuery(scope)}`
  );
  const items = list<Record<string, unknown>>(response.items).map(normalizeCartItem);
  return {
    items,
    count: centsOrNull(response.count) ?? items.length,
    staleCount: centsOrNull(response.stale_count) ?? 0,
    maxItems: centsOrNull(response.max_items) ?? 0
  };
}

/**
 * Put a supplier product in the cart.
 *
 * `preview` is a display convenience and is the only place a client-supplied
 * product body is accepted anywhere in this module. The server stores it as a
 * *cache* on the cart row and re-fetches everything at import time, so a forged
 * preview can make a card look wrong and cannot make a listing wrong. Passing it
 * saves the merchant a round trip when they add straight from a detail screen.
 */
export async function addImportCartItem(
  scope: DropshippingScope,
  connectionId: string,
  input: {
    externalProductId: string;
    selectedVariantIds?: string[];
    preview?: Record<string, unknown>;
    provider?: string;
  }
): Promise<ImportCartItem> {
  const response = await pulseApi<{ item?: Record<string, unknown> }>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/cart/items`,
    {
      method: "POST",
      body: scopeBody(scope, {
        external_product_id: input.externalProductId,
        selected_variant_ids: input.selectedVariantIds,
        product: input.preview,
        provider: input.provider || "cj"
      })
    }
  );
  return normalizeCartItem(response.item || {});
}

/** Change which variants of a cart row will be imported. */
export async function updateImportCartItem(
  scope: DropshippingScope,
  connectionId: string,
  itemId: string,
  selectedVariantIds: string[]
): Promise<ImportCartItem> {
  const response = await pulseApi<{ item?: Record<string, unknown> }>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/cart/items/${encodeURIComponent(itemId)}`,
    { method: "PATCH", body: scopeBody(scope, { selected_variant_ids: selectedVariantIds }) }
  );
  return normalizeCartItem(response.item || {});
}

export async function removeImportCartItem(
  scope: DropshippingScope,
  connectionId: string,
  itemId: string
): Promise<void> {
  await pulseApi(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/cart/items/${encodeURIComponent(itemId)}` +
      scopeQuery(scope),
    { method: "DELETE" }
  );
}

/* ------------------------------------------------------------------ *
 * Store import policy
 * ------------------------------------------------------------------ */

/**
 * How a store imports: what it prices at, whether imports finish themselves,
 * and whether finished imports are offered beyond the store.
 *
 * Scoped to the storefront, not the supplier connection. A merchant with two
 * suppliers prices both the same way unless they say otherwise, so this is read
 * and written without a connection id.
 */
export type StoreImportPolicy = {
  pricingRule: PricingRule;
  pricingSource: PricingSource;
  /** Imports publish themselves when they are safe to publish. */
  autoPublish: boolean;
  /**
   * Imported products are offered marketplace-wide, not just in this store.
   *
   * Off by default and deliberately separate from `autoPublish`: one tap should
   * finish a listing in the merchant's own store without broadcasting every
   * sourced product across PulseSoc. Turning it on is a distribution decision,
   * and it is theirs to make.
   */
  marketplaceAutolist: boolean;
  /**
   * What this store's supplier charges to ship one unit, in cents — or `null`.
   *
   * `null` is not zero, and that distinction is the whole point of the field.
   * Zero means the merchant declared that freight is already inside the item
   * price. `null` means nobody has said, and the margins shown everywhere else
   * are then measured against the item cost alone — which on a cheap, heavy
   * product is a margin that does not exist.
   *
   * There is no platform default here, unlike `pricingRule`. A default margin is
   * a choice PulseSoc may make; a default freight cost would be PulseSoc
   * asserting what a supplier charges, which it cannot know. Nor can it be
   * estimated at import time: a real CJ quote needs a destination, and at import
   * time there is no buyer.
   */
  shippingAllowanceCents: number | null;
  /**
   * `STORE` when the merchant declared it, `PLATFORM_DEFAULT` when nobody has.
   *
   * Reads oddly beside a `null` allowance — the platform has no number it could
   * have defaulted to — but it is the same vocabulary as `pricingSource`, and the
   * alternative is a second one for the same concept.
   */
  shippingAllowanceSource: PricingSource;
  /**
   * Whether this store has ever saved a policy.
   *
   * `false` does not mean "no policy" — the values above are the platform's and
   * are what an import would really use. It means nobody has chosen, which is the
   * difference between "your store is set to 45%" and "PulseSoc is using 45%
   * because you haven't said".
   */
  configured: boolean;
};

/**
 * Send as `shippingAllowanceCents` to un-declare an allowance.
 *
 * `undefined` already means "leave this field alone" for every field on this
 * PATCH, and the cleared value *is* absent. So clearing needs a third value, and
 * it has to be one that survives JSON. The server compares against the identical
 * constant (`store_policy.CLEAR_ALLOWANCE`).
 */
export const CLEAR_SHIPPING_ALLOWANCE = "UNKNOWN" as const;

function normalizeShippingAllowance(raw: unknown): number | null {
  // Deliberately strict, and deliberately not `Number(raw) || null`: `0` is a
  // real declaration — "freight is in the item price" — and must survive, while a
  // string or a fraction of a cent must not be rendered as a figure the merchant
  // never set.
  if (typeof raw !== "number" || !Number.isInteger(raw) || raw < 0) return null;
  return raw;
}

function normalizeStorePolicy(raw: unknown): StoreImportPolicy {
  const value = (raw || {}) as Record<string, unknown>;
  const allowance = normalizeShippingAllowance(value.shipping_allowance_cents);
  return {
    pricingRule: normalizePricingRule(value.pricing_rule),
    pricingSource: normalizePricingSource(value.pricing_source),
    autoPublish: value.auto_publish === undefined ? true : value.auto_publish === true,
    marketplaceAutolist: value.marketplace_autolist === true,
    shippingAllowanceCents: allowance,
    // Floored by the number rather than taken on its own: a source of `STORE`
    // beside a `null` allowance would light up "you set this" on a field showing
    // nothing, and a figure this client had just rejected is the likeliest way to
    // arrive there.
    shippingAllowanceSource:
      allowance === null
        ? "PLATFORM_DEFAULT"
        : normalizePricingSource(value.shipping_allowance_source),
    configured: value.configured === true
  };
}

/**
 * Parse a merchant's typed dollar amount into whole cents.
 *
 * Returns `null` for anything unusable, and the caller must read that as "do not
 * send" rather than as zero: a blank field is not a declaration of free shipping.
 * So `""` is `null` and so is `"abc"`, while `"0"` is `0`.
 *
 * Rounded, not truncated. `Number.parseFloat("9.29") * 100` is `928.9999…`, and
 * truncating would quietly shave a cent off a good half of what merchants type.
 */
export function shippingAllowanceFromInput(input: string): number | null {
  const trimmed = (input || "").trim().replace(/^\$/, "");
  if (!trimmed || trimmed === "." || !/^\d*\.?\d*$/.test(trimmed)) return null;
  const dollars = Number.parseFloat(trimmed);
  if (!Number.isFinite(dollars) || dollars < 0) return null;
  const cents = Math.round(dollars * 100);
  // The server's own ceiling (`pricing.MAX_PRICE_CENTS`). Refusing here means the
  // merchant is told by the field rather than by a 400 the screen has to explain.
  if (cents > 1_000_000_000) return null;
  return cents;
}

export async function getStoreImportPolicy(scope: DropshippingScope): Promise<StoreImportPolicy> {
  const response = await pulseApi<Record<string, unknown>>(`${BASE}/store-policy${scopeQuery(scope)}`);
  return normalizeStorePolicy(response.policy);
}

/**
 * Change one or more policy fields. Anything omitted is left alone.
 *
 * PATCH semantics all the way down, and the reason is a real bug rather than a
 * preference: four independent controls share one row, and a writer that sent a
 * whole policy object would overwrite whichever field the merchant changed on the
 * other screen with the stale copy this one is holding.
 *
 * `shippingAllowanceCents` takes whole cents, or `CLEAR_SHIPPING_ALLOWANCE` to
 * un-declare. It must not be sent as `0` to mean "we don't know" — the server
 * reads `0` as a merchant stating that freight is already in the item price, and
 * prices every import of theirs accordingly.
 */
export async function updateStoreImportPolicy(
  scope: DropshippingScope,
  changes: {
    pricingRule?: PricingRule;
    autoPublish?: boolean;
    marketplaceAutolist?: boolean;
    shippingAllowanceCents?: number | typeof CLEAR_SHIPPING_ALLOWANCE;
  }
): Promise<StoreImportPolicy> {
  const response = await pulseApi<Record<string, unknown>>(`${BASE}/store-policy`, {
    method: "PATCH",
    body: scopeBody(scope, {
      pricing_rule: changes.pricingRule,
      auto_publish: changes.autoPublish,
      marketplace_autolist: changes.marketplaceAutolist,
      shipping_allowance_cents: changes.shippingAllowanceCents
    })
  });
  return normalizeStorePolicy(response.policy);
}

/* ------------------------------------------------------------------ *
 * Import
 * ------------------------------------------------------------------ */

export type ImportItemResult = {
  itemId: string;
  externalProductId: string;
  provider: string;
  outcome: ImportOutcome | string;
  /** Set whenever a listing was created: `IMPORTED`, `PUBLISHED`, `NEEDS_ATTENTION`, `ALREADY_EXISTS`. */
  listingId: number | null;
  /**
   * A machine code narrowing why an item was refused, when the server had one.
   * Never provider prose — a supplier's error string is not something to render
   * to a merchant, and not something to put in a log either.
   */
  detail: string | null;
  /** How many variants were written. Only meaningful where a listing exists. */
  variantCount: number | null;
  /**
   * Whether this product is live in the merchant's store.
   *
   * Read from the server's own field rather than inferred from `outcome`. The two
   * agree, and they have to keep agreeing — but a screen that derives liveness
   * from a string it may not recognise will call an unknown outcome published,
   * and telling a merchant a product is live when it is a draft is the one
   * mistake here that costs them sales silently.
   */
  published: boolean;
  /**
   * Why this product is not live, when it is not. Empty otherwise.
   *
   * The same vocabulary as a publish refusal (`PUBLISH_PROBLEMS`), because it is
   * the same gate: the importer publishes through the identical code path the
   * merchant's own Publish button uses, so the reasons cannot diverge.
   */
  problems: PublishProblem[];
  /** The price a buyer sees, formatted by the server. Only set on a published item. */
  priceLabel: string | null;
  /** Sellable stock at publication. */
  quantity: number | null;
};

function normalizeImportResult(raw: Record<string, unknown>): ImportItemResult {
  return {
    itemId: text(raw.item_id),
    externalProductId: text(raw.external_product_id),
    provider: text(raw.provider).toLowerCase(),
    // Not defaulted to a success-shaped word. An outcome this app cannot read
    // is reported as invalid, which is the safe side of that mistake.
    outcome: text(raw.outcome) || "INVALID_PRODUCT",
    listingId: centsOrNull(raw.listing_id),
    detail: textOrNull(raw.detail),
    variantCount: centsOrNull(raw.variant_count),
    published: raw.published === true,
    problems: list<unknown>(raw.problems).map((problem) => text(problem) as PublishProblem),
    priceLabel: textOrNull(raw.price_label),
    quantity: centsOrNull(raw.quantity)
  };
}

export type ImportRunResult = {
  results: ImportItemResult[];
  requested: number;
  /** Listings created, whether they went live or not. */
  imported: number;
  /** Only the outcomes that actually occurred, so a zero never needs rendering. */
  counts: Partial<Record<ImportOutcome | string, number>>;
  /**
   * True only when *every* listing this run created is live.
   *
   * Not "at least one published". A run of twenty that publishes one and leaves
   * nineteen needing attention is not a published run, and a banner that said so
   * would be the §30 failure the brief names: one success speaking for nineteen
   * drafts the merchant has not been told about.
   */
  published: boolean;
  /** How many went live, for the summary line. */
  publishedCount: number;
  /** How many landed as drafts with something for the merchant to fix. */
  needsAttention: number;
  /** The rule the run actually priced with. */
  pricingRule: PricingRule;
  /**
   * Where that rule came from: this request, the store's saved policy, or the
   * platform default. Rendered, because "why is this priced at $14.91" is the
   * first question an auto-priced import raises.
   */
  pricingSource: PricingSource;
  /** Whether the store's policy has imports finish themselves. */
  autoPublish: boolean;
  /** Whether products imported in this run are offered marketplace-wide (§19/§20). */
  marketplaceAutolist: boolean;
};

/**
 * Import the selected cart rows.
 *
 * Takes ids and an optional pricing rule — nothing else. The rule is arithmetic
 * the server applies to a cost *it* fetched, so it cannot be used to assert one.
 *
 * `pricingRule` is an override and omitting it is meaningful: the server then
 * resolves the store's own policy, and failing that the platform default. Sending
 * a rule the merchant did not choose — a screen's initial state, say — silently
 * outranks the policy they configured, which is §8's priority order inverted by
 * a `useState` default.
 *
 * Idempotent per product: re-running returns `ALREADY_EXISTS` for anything the
 * merchant already has, rather than a second listing. That is why a retry after
 * a dropped connection is safe.
 */
export async function importSelected(
  scope: DropshippingScope,
  connectionId: string,
  input: { itemIds: string[]; pricingRule?: PricingRule | null }
): Promise<ImportRunResult> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/import`,
    {
      method: "POST",
      // `undefined` is dropped by JSON serialisation, which is what "let the
      // store decide" has to look like on the wire. `null` would be a value.
      body: scopeBody(scope, {
        item_ids: input.itemIds,
        pricing_rule: input.pricingRule ?? undefined
      })
    }
  );
  const results = list<Record<string, unknown>>(response.results).map(normalizeImportResult);
  const counts = response.counts;
  return {
    results,
    requested: centsOrNull(response.requested) ?? results.length,
    imported: centsOrNull(response.imported) ?? 0,
    counts: counts && typeof counts === "object" && !Array.isArray(counts)
      ? (counts as Partial<Record<string, number>>)
      : {},
    published: response.published === true,
    publishedCount: centsOrNull(response.published_count) ?? 0,
    needsAttention: centsOrNull(response.needs_attention) ?? 0,
    pricingRule: normalizePricingRule(response.pricing_rule),
    pricingSource: normalizePricingSource(response.pricing_source),
    // Defaulted to the server's own default rather than to `false`. A response
    // from an older build that does not send these fields describes a store that
    // does auto-publish and does not distribute, because that is what the server
    // that omits them does.
    autoPublish: response.auto_publish === undefined ? true : response.auto_publish === true,
    marketplaceAutolist: response.marketplace_autolist === true
  };
}

/** Outcomes the merchant does not need to act on. */
const BENIGN_OUTCOMES = ["IMPORTED", "PUBLISHED", "ALREADY_EXISTS"];

/**
 * Whether a bulk import needs the merchant's attention.
 *
 * Deliberately not "did anything fail" — a run where every item was already
 * imported is a no-op the merchant should be told about plainly, and a run with
 * one refusal among nine successes is neither a success nor a failure.
 */
export function importNeedsReview(result: ImportRunResult): boolean {
  return result.results.some((item) => !BENIGN_OUTCOMES.includes(String(item.outcome)));
}

/* ------------------------------------------------------------------ *
 * Drafts
 * ------------------------------------------------------------------ */

export type DraftVariant = {
  variantId: number | null;
  options: Record<string, string>;
  sku: string | null;
  providerVariantId: string | null;
  stockState: SupplierStockState | string;
  stockQuantity: number | null;
  availability: SupplierAvailability | string;
  currency: string | null;
  /** Supplier cost. Merchant-private. `null` when the supplier gave no price. */
  costCents: number | null;
  /** What the merchant will charge, or `null` while unpriced. */
  retailCents: number | null;
  /** What the active pricing rule would charge. `null` when cost is unknown. */
  proposedRetailCents: number | null;
  marginCents: number | null;
  marginPercent: number | null;
  marginState: MarginState | string;
};

function normalizeDraftVariant(raw: Record<string, unknown>): DraftVariant {
  const options = raw.options;
  const percent = raw.margin_percent;
  return {
    variantId: centsOrNull(raw.variant_id),
    options: options && typeof options === "object" && !Array.isArray(options)
      ? Object.fromEntries(Object.entries(options as Record<string, unknown>).map(([k, v]) => [k, text(v)]))
      : {},
    sku: textOrNull(raw.sku),
    providerVariantId: textOrNull(raw.provider_variant_id),
    stockState: text(raw.stock_state) || "UNKNOWN",
    stockQuantity: centsOrNull(raw.stock_quantity),
    availability: text(raw.availability) || "UNKNOWN",
    currency: textOrNull(raw.currency),
    costCents: centsOrNull(raw.cost_cents),
    retailCents: centsOrNull(raw.retail_cents),
    proposedRetailCents: centsOrNull(raw.proposed_retail_cents),
    marginCents: centsOrNull(raw.margin_cents),
    // Not routed through centsOrNull: a margin percentage is fractional and
    // rounding it here would report 24.6% as 25% and move it across a band.
    marginPercent: typeof percent === "number" && Number.isFinite(percent) ? percent : null,
    marginState: text(raw.margin_state) || "UNKNOWN"
  };
}

export type DraftSupplier = {
  provider: string;
  fulfillmentMode: string | null;
  syncState: string | null;
  lastSyncedAt: string | null;
  supplierCostCents: number | null;
  supplierCostCurrency: string | null;
  externalSku: string | null;
  /**
   * The supplier product this listing was imported from — the `pid` half of a
   * binding. `null` should not happen for a dropship draft; treat it as "cannot
   * bind from here" rather than substituting anything.
   */
  providerProductId: string | null;
  /**
   * The one supplier variant an order for this listing is placed for, or `null`
   * while nothing is bound.
   *
   * A dropship listing does not sell "its variants". The buyer's checkout has no
   * variant selector, so it sells exactly this one and the rest of the variant
   * list is catalogue. `null` is why `SUPPLIER_VARIANT_UNBOUND` refuses
   * publication, and it is the ordinary outcome of importing a product with more
   * than one in-stock variant, which is what the supplier screen pre-selects.
   */
  providerVariantId: string | null;
  /**
   * Fields the merchant has edited. A provider sync must not overwrite these —
   * this list is the mechanism, not a record of one.
   */
  merchantOwnedFields: string[];
};

export type DraftValidation = {
  publishable: boolean;
  /** Every reason, not the first. Fixing one at a time is the experience this avoids. */
  problems: (PublishProblem | string)[];
};

function normalizeValidation(raw: unknown): DraftValidation {
  const value = (raw || {}) as Record<string, unknown>;
  return {
    publishable: value.publishable === true,
    problems: list<unknown>(value.problems).map(text).filter(Boolean)
  };
}

export type ImportedDraft = {
  listingId: number;
  status: string;
  approvalStatus: string;
  published: boolean;
  title: string;
  description: string | null;
  category: string | null;
  currency: string | null;
  media: string[];
  coverImageUrl: string | null;
  variants: DraftVariant[];
  supplier: DraftSupplier;
  pricingRule: PricingRule;
  validation: DraftValidation;
};

function normalizePricingRule(raw: unknown): PricingRule {
  const value = (raw || {}) as Record<string, unknown>;
  const type = text(value.type) as PricingRuleType;
  const numeric = value.value;
  const rule: PricingRule = { type: PRICING_RULES.includes(type) ? type : "MANUAL_PRICE" };
  if (typeof numeric === "number" && Number.isFinite(numeric)) rule.value = numeric;
  return rule;
}

function normalizeDraft(raw: Record<string, unknown>): ImportedDraft {
  const supplier = (raw.supplier || {}) as Record<string, unknown>;
  return {
    listingId: centsOrNull(raw.listing_id) ?? 0,
    status: text(raw.status),
    approvalStatus: text(raw.approval_status),
    published: raw.published === true,
    title: text(raw.title),
    description: textOrNull(raw.description),
    category: textOrNull(raw.category),
    currency: textOrNull(raw.currency),
    media: list<unknown>(raw.media).map(text).filter(Boolean),
    coverImageUrl: textOrNull(raw.cover_image_url),
    variants: list<Record<string, unknown>>(raw.variants).map(normalizeDraftVariant),
    supplier: {
      provider: text(supplier.provider).toLowerCase(),
      fulfillmentMode: textOrNull(supplier.fulfillment_mode),
      syncState: textOrNull(supplier.sync_state),
      lastSyncedAt: textOrNull(supplier.last_synced_at),
      supplierCostCents: centsOrNull(supplier.supplier_cost_cents),
      supplierCostCurrency: textOrNull(supplier.supplier_cost_currency),
      externalSku: textOrNull(supplier.external_sku),
      providerProductId: textOrNull(supplier.provider_product_id),
      providerVariantId: textOrNull(supplier.provider_variant_id),
      merchantOwnedFields: list<unknown>(supplier.merchant_owned_fields).map(text).filter(Boolean)
    },
    pricingRule: normalizePricingRule(raw.pricing_rule),
    validation: normalizeValidation(raw.validation)
  };
}

export type ImportedProductRow = {
  listingId: number;
  title: string;
  status: string;
  approvalStatus: string;
  currency: string | null;
  coverImageUrl: string | null;
  updatedAt: string | null;
  provider: string;
  syncState: string | null;
  /**
   * What the last supplier read concluded needs a human. Beside `syncState`,
   * never folded into it: a listing that is now selling below cost synced
   * perfectly, so `syncState` reads SYNCED and is right to. A screen keyed on
   * sync state alone shows that product as healthy, which is not silence but a
   * false all-clear.
   */
  attention: RevisionAttention[];
  supplierCostCents: number | null;
  providerProductId: string | null;
};

function normalizeImportedRow(raw: Record<string, unknown>): ImportedProductRow {
  return {
    listingId: centsOrNull(raw.id) ?? 0,
    title: text(raw.title),
    status: text(raw.status),
    approvalStatus: text(raw.approval_status),
    currency: textOrNull(raw.currency),
    coverImageUrl: textOrNull(raw.cover_image_url),
    updatedAt: textOrNull(raw.updated_at),
    provider: text(raw.provider).toLowerCase(),
    syncState: textOrNull(raw.sync_state),
    attention: revisionAttention(raw.attention),
    supplierCostCents: centsOrNull(raw.supplier_cost_cents),
    providerProductId: textOrNull(raw.provider_product_id)
  };
}

/**
 * Products imported through this connection, newest first.
 *
 * The server joins from the supplier mapping rather than scanning listings, so
 * a merchant's hand-written products can never appear here — which is what
 * keeps "Dropshipping products" from quietly becoming "all products".
 */
export async function listImportedProducts(
  scope: DropshippingScope,
  connectionId: string,
  options: { status?: string; limit?: number } = {}
): Promise<{ items: ImportedProductRow[]; count: number }> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/imported` +
      scopeQuery(scope, { status: options.status, limit: options.limit })
  );
  const items = list<Record<string, unknown>>(response.items).map(normalizeImportedRow);
  return { items, count: centsOrNull(response.count) ?? items.length };
}

export async function getImportedProduct(
  scope: DropshippingScope,
  connectionId: string,
  listingId: number | string
): Promise<ImportedDraft> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/imported/${encodeURIComponent(String(listingId))}` +
      scopeQuery(scope)
  );
  return normalizeDraft(response);
}

/**
 * Fields a merchant may edit on an imported product.
 *
 * This is the storefront half of the ownership split and mirrors the server's
 * `drafts.EDITABLE` allowlist exactly: cost, inventory, provider identity and
 * availability are the supplier's and have no entry here, so no screen can
 * offer to edit them and no request from this app can carry one.
 *
 * `prices` maps variant id to retail minor units. `null` unprices a variant,
 * which is a real thing a merchant does and is not the same as pricing it at 0.
 */
export type DraftEdits = {
  title?: string;
  description?: string;
  category?: string;
  currency?: string;
  media?: string[];
  prices?: Record<string, number | null>;
};

/**
 * Only the keys the merchant actually changed are sent. The server records each
 * one as merchant-owned and stops provider sync from reverting it, so sending
 * an unchanged field would hand ownership away for an edit that never happened.
 */
function editsToFields(edits: DraftEdits): Record<string, unknown> {
  const fields: Record<string, unknown> = {};
  if (edits.title !== undefined) fields.title = edits.title;
  if (edits.description !== undefined) fields.description = edits.description;
  if (edits.category !== undefined) fields.category = edits.category;
  if (edits.currency !== undefined) fields.currency = edits.currency;
  if (edits.media !== undefined) fields.media = edits.media;
  if (edits.prices !== undefined) fields.price_cents = edits.prices;
  return fields;
}

export async function updateImportedProduct(
  scope: DropshippingScope,
  connectionId: string,
  listingId: number | string,
  edits: DraftEdits
): Promise<ImportedDraft> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/imported/${encodeURIComponent(String(listingId))}`,
    { method: "PATCH", body: scopeBody(scope, { fields: editsToFields(edits) }) }
  );
  return normalizeDraft(response);
}

/**
 * Name the one supplier variant this listing sells.
 *
 * The answer to `SUPPLIER_VARIANT_UNBOUND`. Without this call that problem code
 * was a refusal nothing in the app could satisfy: `bind-product` existed on the
 * server and had no caller on any screen, so a merchant whose import selected
 * more than one in-stock variant — the supplier screen's own default — held a
 * draft that could never be published.
 *
 * Binding is close to one-way. `marketplace_variants.link_source` accepts NULL →
 * a variant and refuses variant A → variant B with `binding_conflict`, because a
 * published listing that silently changed what it ships would keep selling a
 * page describing the old product. So the caller must present this as a choice
 * being made, not a setting being adjusted.
 *
 * Returns nothing, for the same reason `bindConnectionShop` does: the draft is
 * what every surface reads, and re-reading it is how the caller learns that
 * `SUPPLIER_VARIANT_UNBOUND` has cleared. Trusting this response instead would
 * be trusting a second copy of the verdict.
 */
export async function bindDraftVariant(
  scope: DropshippingScope,
  connectionId: string,
  input: { listingId: number | string; providerProductId: string; providerVariantId: string }
): Promise<void> {
  await pulseApi(`${SUPPLIERS_BASE}/connections/${encodeURIComponent(connectionId)}/bind-product`, {
    method: "POST",
    body: scopeBody(scope, {
      canonical_product_id: String(input.listingId),
      pid: input.providerProductId,
      vid: input.providerVariantId
    })
  });
}

/** Dry-run the publish gate. Changes nothing. */
export async function validateImportedProduct(
  scope: DropshippingScope,
  connectionId: string,
  listingId: number | string
): Promise<DraftValidation> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/imported/${encodeURIComponent(String(listingId))}/validate`,
    { method: "POST", body: scopeBody(scope) }
  );
  return normalizeValidation(response);
}

export type PublishResult = {
  listingId: number;
  status: string;
  /**
   * Published is not the same as publicly discoverable. Moderation still has to
   * approve, and saying otherwise would have the merchant hunting for a product
   * in a marketplace that is correctly hiding it.
   */
  awaitingModeration: boolean;
  sellableVariants: number;
};

export async function publishImportedProduct(
  scope: DropshippingScope,
  connectionId: string,
  listingId: number | string
): Promise<PublishResult> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/imported/${encodeURIComponent(String(listingId))}/publish`,
    { method: "POST", body: scopeBody(scope) }
  );
  return {
    listingId: centsOrNull(response.listing_id) ?? 0,
    status: text(response.status),
    awaitingModeration: response.awaiting_moderation === true,
    sellableVariants: centsOrNull(response.sellable_variants) ?? 0
  };
}

/* ------------------------------------------------------------------ *
 * Pricing preview
 * ------------------------------------------------------------------ */

export type PricingQuote = {
  costCents: number | null;
  retailCents: number | null;
  proposedRetailCents: number | null;
  marginCents: number | null;
  marginPercent: number | null;
  marginState: MarginState | string;
};

/**
 * What a pricing rule would do to a set of costs, without applying it.
 *
 * The arithmetic happens server-side so that one implementation decides what a
 * margin is. It takes no connection and touches no provider — a merchant
 * dragging a markup slider must not generate supplier traffic.
 */
export async function previewPricing(
  costCents: (number | null)[],
  rule: PricingRule
): Promise<{ rule: PricingRule; quotes: PricingQuote[] }> {
  const response = await pulseApi<Record<string, unknown>>(`${BASE}/pricing/preview`, {
    method: "POST",
    body: JSON.stringify({ cost_cents: costCents, pricing_rule: rule })
  });
  return {
    rule: normalizePricingRule(response.rule),
    quotes: list<Record<string, unknown>>(response.quotes).map((quote) => {
      const percent = quote.margin_percent;
      return {
        costCents: centsOrNull(quote.cost_cents),
        retailCents: centsOrNull(quote.retail_cents),
        proposedRetailCents: centsOrNull(quote.proposed_retail_cents),
        marginCents: centsOrNull(quote.margin_cents),
        marginPercent: typeof percent === "number" && Number.isFinite(percent) ? percent : null,
        marginState: text(quote.margin_state) || "UNKNOWN"
      };
    })
  };
}

/* ------------------------------------------------------------------ *
 * Supplier obligations
 * ------------------------------------------------------------------ */

/**
 * Where a paid sale has got to on the supplier's side of the transaction.
 *
 * `AWAITING_SUPPLIER_ORDER` is this app's own name for "there is no supplier
 * order yet", and it is deliberately not one of the outbox's states: the outbox
 * describes the delivery of a supplier order, and in this case there is not one
 * to deliver. Every other member is an outbox state, spelled exactly as
 * `fulfillment.dispatch` and `webhooks` write it.
 *
 * This is the second copy of an enumeration whose first copy is Python, which is
 * the same shape as the `PUBLISH_PROBLEMS` drift above: no compiler spans the
 * two, the backend grows one and this list does not, and the merchant reads a
 * raw identifier. So `tests/dropshipping/test_supplier_obligation_copy.py` pins
 * this list against the state literals the Python writers actually emit, rather
 * than against a Python list that could drift from them in turn.
 */
export const SUPPLIER_ORDER_STATES = [
  "AWAITING_SUPPLIER_ORDER",
  "READY",
  "SENDING",
  "UNKNOWN",
  "RECONCILE",
  "LINKED",
  "BLOCKED"
] as const;
export type SupplierOrderState = (typeof SUPPLIER_ORDER_STATES)[number];

/**
 * What each state means to a merchant, in their words rather than the outbox's.
 *
 * A total `Record` rather than a `Set` or a partial map, so that adding a state
 * above without writing copy for it is a compile error here — the one place a
 * compiler *can* span, because both halves are TypeScript.
 *
 * `UNKNOWN` is the load-bearing one. It does not mean "we don't know the state";
 * it means the supplier order may or may not exist because a write could not be
 * confirmed. Telling a merchant "not placed" there would invite them to place a
 * second one, and duplicate supplier orders are real money.
 */
export const SUPPLIER_ORDER_STATE_COPY: Record<SupplierOrderState, string> = {
  AWAITING_SUPPLIER_ORDER: "No supplier order yet",
  READY: "Queued to send to your supplier",
  SENDING: "Sending to your supplier",
  UNKNOWN: "Unconfirmed — do not re-order",
  RECONCILE: "Checking with your supplier",
  LINKED: "Placed with your supplier",
  // Not "your supplier refused this order", which is what this said. `BLOCKED`
  // is reached overwhelmingly by *this* deployment refusing to send — an
  // expired quote, a changed cost, a connection that moved — and on every one
  // of those paths `dispatch` raises before `_sending`, so the supplier was
  // never contacted and has no opinion to report. The reason line rendered
  // underneath now names which refusal it was; this line's only job is to say
  // that nothing was sent and that it is waiting on the merchant.
  BLOCKED: "Not sent — needs your attention"
};

/**
 * Merchant-readable words for a state, including one this build has never heard
 * of.
 *
 * The fallback exists for the same reason the one on publish problems does: a
 * server ahead of this build can name a state that is not in the union above,
 * and rendering the raw identifier is what put `SUPPLIER_VARIANT_UNBOUND` on a
 * merchant's screen. The unknown case says what is true — that this app cannot
 * interpret it — instead of guessing a side.
 */
export function supplierOrderStateCopy(state: string): string {
  return (
    SUPPLIER_ORDER_STATE_COPY[state as SupplierOrderState] ||
    "Your supplier order is in a state this app does not recognise yet"
  );
}

/**
 * Why a paid sale cannot yet be turned into a supplier purchase.
 *
 * Third copy of a Python enumeration, same shape and same risk as
 * `SUPPLIER_ORDER_STATES` above, and pinned the same way — against the literals
 * `fulfillment.BLOCKERS` actually holds, in
 * `tests/dropshipping/test_supplier_obligation_copy.py`.
 *
 * `SHOP_BINDING_REQUIRED` is the one that is true of the connection rather than
 * of any one sale, so it appears on every obligation at once. That is not a
 * duplication bug: each obligation is separately unfulfillable, and hiding it
 * from all but the first would leave a merchant fixing the sales one at a time.
 */
export const SUPPLIER_OBLIGATION_BLOCKERS = [
  "SUPPLIER_ORDER_ALREADY_PLACED",
  "SHOP_BINDING_REQUIRED",
  "NOT_SHIPPING_LANE",
  "DESTINATION_MISSING",
  "DESTINATION_INCOMPLETE",
  "SUPPLIER_SKU_MISSING",
  "SUPPLIER_COST_UNKNOWN"
] as const;
export type SupplierObligationBlocker = (typeof SUPPLIER_OBLIGATION_BLOCKERS)[number];

/**
 * What each blocker means, and — where there is one — what the merchant can do.
 *
 * A total `Record` for the reason the state copy above is one: adding a blocker
 * to the list without writing words for it has to fail the compiler here.
 *
 * Two of these have no merchant action at all. `NOT_SHIPPING_LANE` means the
 * buyer chose collection or a digital delivery, so there is nothing to buy from
 * a supplier and the sale is already complete — it is an explanation, not a
 * problem. `DESTINATION_MISSING` means the address the buyer paid against is not
 * on the record, which a merchant cannot supply on their behalf; asking them to
 * type one would be inventing a delivery address for somebody else's parcel.
 */
export const SUPPLIER_OBLIGATION_BLOCKER_COPY: Record<SupplierObligationBlocker, string> = {
  SUPPLIER_ORDER_ALREADY_PLACED: "You have already ordered this from your supplier",
  SHOP_BINDING_REQUIRED: "Choose which of your supplier shops to order through in Connection settings",
  NOT_SHIPPING_LANE: "This sale is not being shipped, so there is nothing to order",
  DESTINATION_MISSING: "This order has no delivery address on record",
  DESTINATION_INCOMPLETE: "The delivery address is missing something your supplier requires",
  SUPPLIER_SKU_MISSING: "This listing is not linked to a supplier product code",
  SUPPLIER_COST_UNKNOWN: "Your supplier has not quoted a cost for this variant"
};

/**
 * Merchant-readable words for a blocker, including one this build has never
 * heard of. Same fallback as `supplierOrderStateCopy`, same reason.
 */
export function supplierObligationBlockerCopy(blocker: string): string {
  return (
    SUPPLIER_OBLIGATION_BLOCKER_COPY[blocker as SupplierObligationBlocker] ||
    "Something about this order stops it being sent to your supplier"
  );
}

/**
 * Every reason the outbox records for a supplier order that has not gone out.
 *
 * Fourth copy of a Python enumeration — `fulfillment.OUTBOX_REASONS` — and
 * pinned against it the same way as the two above.
 *
 * This one existed only as a rendering accident until now. The backend column is
 * `last_error`; `DropshippingOrdersScreen` printed its value verbatim under a
 * comment saying it was "the supplier's own refusal text ... the words their
 * supplier used". It was never either of those. No provider string can reach
 * that column by construction (`services/business_os/suppliers/errors.py` exists
 * to guarantee it), and the value is an identifier written in Python — so what a
 * merchant actually read on a blocked order was `preflight_blocked`.
 *
 * Worse, it was one word for about a dozen causes, because `dispatch` flattened
 * them all before storing. Half of those a merchant can fix. So the fix is on
 * both sides: the backend keeps the cause, and this map turns it into words.
 */
export const SUPPLIER_ORDER_REASONS = [
  "dispatch_lease_expired",
  "absence_not_proven",
  "awaiting_create_readback",
  "readback_required",
  "preflight_deferred",
  "preflight_blocked",
  "connection_unavailable",
  "supplier_quote_expired",
  "supplier_cost_changed",
  "supplier_connection_changed",
  "supplier_shop_unbound",
  "supplier_item_changed",
  "supplier_cost_unknown",
  "supplier_stock_unconfirmed",
  "order_no_longer_eligible",
  "supplier_ordering_disabled",
  "supplier_funding_required",
  "supplier_order_needs_support"
] as const;
export type SupplierOrderReason = (typeof SUPPLIER_ORDER_REASONS)[number];

/**
 * What each reason means, and what — if anything — the merchant does next.
 *
 * A total `Record`, for the third time and the same reason: a reason added to
 * the list without words has to be a compile error here.
 *
 * The first five say "this worker has not finished", not "something is wrong",
 * and they are phrased so a merchant does not go looking for a problem that is
 * not theirs. In particular none of them may imply the order failed:
 * `awaiting_create_readback` and `readback_required` are written *after* a send
 * whose outcome is unconfirmed, so telling a merchant it did not go would invite
 * the one mistake that costs real money — ordering the same goods twice.
 *
 * They also do not repeat "do not re-order". All four of the read-back reasons
 * are only ever stored alongside state `UNKNOWN`, whose own copy carries that
 * instruction, and the screen renders both lines. This line's job is the part
 * the state cannot express — that a send was attempted and is being confirmed.
 */
export const SUPPLIER_ORDER_REASON_COPY: Record<SupplierOrderReason, string> = {
  dispatch_lease_expired: "A send was interrupted — checking whether it went through",
  absence_not_proven: "Checking with your supplier whether this order exists",
  awaiting_create_readback: "Sent to your supplier — waiting for them to confirm it",
  readback_required: "Waiting for your supplier to confirm this order",
  preflight_deferred: "Your supplier is busy — this will be retried automatically",
  connection_unavailable: "Your supplier connection could not be loaded — this will be retried",
  preflight_blocked: "This could not be sent to your supplier. Contact support",
  supplier_quote_expired: "The shipping quote expired before this was sent. Get a new quote and approve it",
  supplier_cost_changed: "Your supplier cost changed before this was sent. Review and approve the new cost",
  supplier_connection_changed: "Your supplier connection changed after this was queued. Reconnect, then try again",
  supplier_shop_unbound: "Choose which of your supplier shops to order through in Connection settings",
  supplier_item_changed: "This product no longer matches what your supplier lists. Import it again",
  supplier_cost_unknown: "Your supplier did not state a usable cost for this item",
  supplier_stock_unconfirmed: "Your supplier has not confirmed stock for this order",
  order_no_longer_eligible: "This sale was cancelled, refunded or disputed, so nothing was ordered",
  supplier_ordering_disabled: "Supplier ordering is not switched on for this account yet",
  supplier_funding_required: "Waiting for this order to be funded before it goes to your supplier. Nothing has been sent",
  supplier_order_needs_support: "This order needs support before it can be sent to your supplier"
};

/**
 * Merchant-readable words for an outbox reason, including one this build has
 * never heard of. Same fallback as the two above, and it matters more here:
 * falling through used to mean printing the identifier itself.
 */
export function supplierOrderReasonCopy(reason: string): string {
  return (
    SUPPLIER_ORDER_REASON_COPY[reason as SupplierOrderReason] ||
    "Your supplier order is waiting on something this app cannot name yet"
  );
}

/**
 * Whether anything on the server is turning queued supplier orders into real
 * ones — fifth copy of a Python enumeration, `fulfillment.DRAIN_STATES`.
 *
 * Why a screen needs to know this at all: `READY` renders as "Queued to send to
 * your supplier", and in this deployment nothing sends them. The only caller of
 * the dispatch path is a worker that is not in the `Procfile`, so a paid order
 * sits at that reassuring sentence permanently.
 *
 * The wording was not the bug. The claim was unfalsifiable — the worker printed
 * its counts to stdout and recorded nothing, so no payload could distinguish a
 * queue that is moving from a queue with nothing attached to it. The server now
 * records each tick and states what it found, and this map turns that into the
 * one sentence that stops a merchant waiting on something that will never come.
 */
export const SUPPLIER_DRAIN_STATES = [
  "DRAINING",
  "DRAIN_STALLED",
  "TICKING_BUT_NOT_COMPLETING",
  "NO_DRAIN_HAS_EVER_RUN"
] as const;
export type SupplierDrainState = (typeof SUPPLIER_DRAIN_STATES)[number];

/**
 * What to tell a merchant about the drain, or `null` when there is nothing to
 * say.
 *
 * `DRAINING` maps to `null` on purpose. A healthy queue needs no banner, and the
 * per-row copy already says "Queued to send to your supplier" — which is true
 * exactly then, and is the reason this is a separate notice rather than a change
 * to that sentence. Folding it in would make one row's `state` mean two
 * different things depending on a fact about the server.
 *
 * None of these blame the merchant, because none of them are the merchant's
 * fault, and none promise a time — the server states what it last observed and
 * this copy says no more than that.
 */
export const SUPPLIER_DRAIN_NOTICE: Record<SupplierDrainState, string | null> = {
  DRAINING: null,
  NO_DRAIN_HAS_EVER_RUN:
    "Supplier ordering is not running on this account yet, so queued orders are not being sent. Contact support before promising a dispatch date",
  TICKING_BUT_NOT_COMPLETING:
    "Supplier ordering is failing on this account, so queued orders are not being sent. Contact support",
  DRAIN_STALLED:
    "Queued orders have not been sent for some time. Contact support before promising a dispatch date"
};

export function supplierDrainNotice(state: string | null | undefined): string | null {
  if (!state) {
    // An older server sends no `drain` at all. Saying nothing is right here and
    // is not the same mistake as before: the old screen made a positive promise
    // with no evidence, whereas a build talking to a server that cannot answer
    // has genuinely not been told anything.
    return null;
  }
  return SUPPLIER_DRAIN_NOTICE[state as SupplierDrainState] ?? null;
}

/**
 * One paid sale and the supplier purchase it owes.
 *
 * Two orders, deliberately: `orderId` is the customer's order, `intentId` is
 * the merchant's order with the supplier, and `intentId` being `null` is the
 * normal state of a sale nobody has fulfilled yet rather than an error.
 *
 * `supplierCostCents` is on this type because every route in this module is
 * merchant-authenticated (see the file header). It must never reach a buyer
 * surface.
 *
 * There is no delivery address on this type, and there must not be. The server
 * reads one to decide `blockers`, and deliberately does not send it: a merchant
 * needs to know whether the parcel can be shipped, not where to, and the
 * address belongs to the buyer.
 */
export type SupplierObligation = {
  orderId: number;
  listingId: number;
  title: string;
  quantity: number;
  amountCents: number | null;
  currency: string | null;
  orderStatus: string;
  paidAt: string | null;
  orderedAt: string | null;
  provider: string;
  providerProductId: string | null;
  providerVariantId: string | null;
  /**
   * The supplier's code for the *bound variant*, not for the product.
   *
   * It used to be `externalSku`, read from `marketplace_product_sources`, which
   * is the product-level column and is usually empty. The two live one table
   * apart and the one that was sent was never the one the supplier order is
   * matched on, so the honest case looked like a missing SKU and the populated
   * case looked like a binding bug.
   */
  supplierSku: string | null;
  supplierCostCents: number | null;
  supplierCostCurrency: string | null;
  intentId: string | null;
  /**
   * Everything standing between this sale and a supplier purchase, empty when
   * nothing is.
   *
   * Passed through as `string[]` rather than narrowed to the union for the same
   * reason `state` is: a server ahead of this build can name a blocker this one
   * has never heard of, and `supplierObligationBlockerCopy` says so rather than
   * rendering the identifier.
   */
  blockers: string[];
  /**
   * The server's own answer, not `blockers.length === 0` recomputed here.
   *
   * It means every precondition an obligation can carry is satisfied — not that
   * the order will certainly go through. Freight still has to be quoted, and
   * that step can refuse on its own grounds.
   */
  canPlaceSupplierOrder: boolean;
  state: SupplierOrderState | string;
  supplierOrderPlaced: boolean;
  providerOrderId: string | null;
  /**
   * The supplier's own word for where the order stands, verbatim.
   *
   * Named `supplierOrderStatus` rather than `providerStatus` because on this
   * platform `provider_status` is the payment provider's subscription status —
   * a membership field the entitlement drift guard keeps off unlisted files.
   * The backend aliases the outbox column on the way out for the same reason.
   */
  supplierOrderStatus: string | null;
  lastError: string | null;
  updatedAt: string | null;
};

function normalizeObligation(raw: Record<string, unknown>): SupplierObligation {
  const intentId = textOrNull(raw.intent_id);
  return {
    orderId: centsOrNull(raw.order_id) ?? 0,
    listingId: centsOrNull(raw.listing_id) ?? 0,
    title: text(raw.title),
    quantity: centsOrNull(raw.quantity) ?? 0,
    amountCents: centsOrNull(raw.amount_cents),
    currency: textOrNull(raw.currency),
    orderStatus: text(raw.order_status),
    paidAt: textOrNull(raw.paid_at),
    orderedAt: textOrNull(raw.ordered_at),
    provider: text(raw.provider).toLowerCase(),
    providerProductId: textOrNull(raw.provider_product_id),
    providerVariantId: textOrNull(raw.provider_variant_id),
    supplierSku: textOrNull(raw.supplier_sku),
    supplierCostCents: centsOrNull(raw.supplier_cost_cents),
    supplierCostCurrency: textOrNull(raw.supplier_cost_currency),
    intentId,
    blockers: list<unknown>(raw.blockers)
      .map((entry) => text(entry))
      .filter((entry) => entry.length > 0),
    canPlaceSupplierOrder: raw.can_place_supplier_order === true,
    // Passed through, not narrowed to the union: a state this build has not
    // heard of must survive to `supplierOrderStateCopy`, which says so.
    state: text(raw.state) || "AWAITING_SUPPLIER_ORDER",
    // Read from the server's own field rather than re-derived here as
    // `intentId !== null`. The server already decided; deriving it a second
    // time is one more copy that can disagree, and this is the field a merchant
    // would act on.
    supplierOrderPlaced: raw.supplier_order_placed === true,
    providerOrderId: textOrNull(raw.provider_order_id),
    supplierOrderStatus: textOrNull(raw.supplier_order_status),
    lastError: textOrNull(raw.last_error),
    updatedAt: textOrNull(raw.intent_updated_at)
  };
}

/**
 * Paid sales through this connection that still owe a purchase from the
 * supplier, newest first.
 *
 * This is the endpoint the supplier-orders screen had no source for. Before it,
 * a buyer could pay for a published, bound dropship listing and the merchant's
 * only record was a customer order indistinguishable from a hand-stocked sale —
 * the fulfilment layer could create one supplier order and read one back by id,
 * but nothing could tell a merchant which of their sales needed one.
 *
 * The server derives the list on read by joining paid orders to the supplier
 * mapping of the listing they were placed on, so a merchant's hand-stocked
 * products cannot appear here and nothing has to be kept in step.
 */
export async function listSupplierObligations(
  scope: DropshippingScope,
  connectionId: string,
  options: { limit?: number } = {}
): Promise<{
  obligations: SupplierObligation[];
  isSandbox: boolean;
  drainState: string | null;
}> {
  const response = await pulseApi<Record<string, unknown>>(
    `${SUPPLIERS_BASE}/connections/${encodeURIComponent(connectionId)}/obligations` +
      scopeQuery(scope, { limit: options.limit })
  );
  const drain = (response.drain ?? null) as Record<string, unknown> | null;
  return {
    obligations: list<Record<string, unknown>>(response.obligations).map(normalizeObligation),
    // The server states this; it is not assumed from a build flag. A screen
    // that promises "nothing is sent to your supplier" on its own authority
    // would keep promising it after the platform switched fulfilment on.
    isSandbox: centsOrNull(response.isSandbox) === 1,
    // Same rule, and the reason the field is here rather than derived: whether
    // a drain exists is a fact only the server can observe. `null` when an
    // older server does not report one — not narrowed to the union, so a state
    // this build has never heard of reaches `supplierDrainNotice` intact.
    drainState: drain ? textOrNull(drain.state) : null
  };
}

/* ------------------------------------------------------------------ *
 * Screen states
 * ------------------------------------------------------------------ */

/**
 * The distinct conditions a dropshipping screen can be in.
 *
 * They are one closed set rather than a pile of booleans because several of
 * them look alike and must not be allowed to co-render: an `ERROR` that also
 * draws the `EMPTY` copy tells a merchant their supplier has no products when
 * in fact the request failed, and that is a merchant who goes looking for a new
 * supplier. A screen renders exactly one of these.
 */
export const DROPSHIPPING_STATES = [
  "LOADING",
  "EMPTY",
  "READY",
  "STALE",
  "SUPPLIER_DISABLED",
  "PROVIDER_NETWORK_DISABLED",
  "CREDENTIAL_STORAGE_UNAVAILABLE",
  "STORE_NOT_APPROVED",
  "STORE_NOT_FOUND",
  "STORE_ACCESS_REVOKED",
  "STALE_STORE_CONTEXT",
  "STORE_MAPPING_MISSING",
  "SUPPLIER_CONNECTION_FORBIDDEN",
  "CSRF_INVALID",
  "SESSION_EXPIRED",
  "INVALID_CREDENTIAL",
  "SUPPLIER_DISCONNECTED",
  // The shop-binding conditions. They are separate states rather than one
  // "binding problem" because the merchant's next move differs in every one:
  // choose a shop, choose a *different* shop, wait for whoever already bound
  // this connection, go and create an API app in the supplier console, or go and
  // rename a storefront there. A single state would have to pick one of those
  // sentences and be wrong the rest of the time.
  "SHOP_BINDING_REQUIRED",
  "SHOP_NOT_AUTHORIZED",
  "SHOP_BINDING_CONFLICT",
  "SHOP_CANNOT_FULFIL",
  "SHOP_NAME_AMBIGUOUS",
  "PROVIDER_UNAVAILABLE",
  "UNAUTHORIZED",
  "ERROR"
] as const;
export type DropshippingState = (typeof DROPSHIPPING_STATES)[number];

/** Server codes that mean the connection itself is the problem. */
const DISCONNECTED_CODES = [
  "supplier_disconnected",
  "connection_not_found",
  "credential_missing",
  // A stored credential the server cannot open: sealed under a key rotation has
  // since retired, or written under a different store than the one asking. It
  // sits beside `credential_missing` because the merchant's move is identical —
  // reconnect the supplier account — and because the alternative is worse than
  // it looks. It arrives as a 409, which matches none of the status classes
  // below, so without this entry it reaches the merchant as "Something went
  // wrong": no cause, no button, and no hint that reconnecting fixes it. That
  // is the failure this whole function exists to prevent, and it would have
  // been introduced by the server-side change that made 409 possible.
  "credential_unusable",
  "auth_expired",
  "reauth_required",
  "not_connected"
];

/** Server codes that mean the provider is the problem, not the merchant. */
const PROVIDER_CODES = [
  "supplier_unavailable",
  "provider_unavailable",
  "provider_error",
  "rate_limited",
  "quota_exhausted",
  "product_unavailable",
  "request_timeout",
  "request_unreachable"
];

/**
 * Classify a thrown error into the state the screen should show.
 *
 * The point is to keep "your supplier account needs attention" apart from
 * "CJ is having a bad afternoon". They need different words and different
 * buttons, and a single "Something went wrong" sends the merchant to re-enter
 * credentials that were never wrong.
 */
export function stateForError(error: unknown): DropshippingState {
  if (!(error instanceof PulseApiError)) return "ERROR";
  const code = String(error.code || "").toLowerCase();

  // Checked before the status classes below, because every one of these arrives
  // with a status that would otherwise be read as something else entirely:
  // `disabled` is a 404, and a store awaiting approval is a 403. A merchant told
  // "not found" when the truth is "this server has the supplier feature turned
  // off" goes looking for a bug in their own account.
  if (code === "disabled") return "SUPPLIER_DISABLED";
  if (code === "provider_network_disabled" || code === "provider_approval_required") {
    return "PROVIDER_NETWORK_DISABLED";
  }
  // Third condition of the deployment, and the one that hid the longest. The
  // server refuses to store a credential it cannot encrypt, and it checks that
  // *before* it calls the supplier -- `vault.require_available()` runs ahead of
  // `adapter.authenticate()` precisely so a broken deployment cannot spend one
  // of the egress IP's three account slots. So when this code arrives, the
  // supplier was never contacted at all.
  //
  // It used to fall through to the `status === 503` catch-all below and reach
  // the merchant as "Your supplier isn't responding ... try again shortly" --
  // wrong about who failed, and wrong that waiting helps, since nothing about a
  // missing key on our own server changes with time. It is the same mistake the
  // two codes above exist to prevent, on a third deployment condition that was
  // missed, so it is matched here beside them rather than added to
  // PROVIDER_CODES.
  if (code === "credential_vault_unavailable") return "CREDENTIAL_STORAGE_UNAVAILABLE";
  // Its two siblings are deliberately elsewhere, because "the vault is down" is
  // the only one of the three the merchant can wait out. `credential_unusable`
  // is in DISCONNECTED_CODES (reconnect), and `credential_request_invalid` is a
  // 500 that falls through to "ERROR" on purpose — it means the bug is ours, and
  // there is no action to offer someone for a mistake they did not make.
  if (code === "store_not_approved") return "STORE_NOT_APPROVED";
  if (code === "invalid_api_key") return "INVALID_CREDENTIAL";

  // Four different things used to reach the merchant as "you're not signed in
  // to this store any more": a rejected write token, an expired session, a
  // store the server could not find, and a store whose access was withdrawn.
  // Only the second of those is about being signed in, and the merchant can
  // only act on the one they are actually in. They are matched ahead of the
  // status classes below because every one of them is a 401 or a 403.
  if (code === "csrf") return "CSRF_INVALID";
  if (code === "login_required" || code === "unauthorized") return "SESSION_EXPIRED";
  if (code === "store_access_revoked" || code === "account_hold") return "STORE_ACCESS_REVOKED";
  if (code === "stale_store_context") return "STALE_STORE_CONTEXT";
  if (code === "merchant_identity_unresolved") return "STORE_MAPPING_MISSING";
  if (code === "store_not_found") return "STORE_NOT_FOUND";
  if (code === "forbidden") return "SUPPLIER_CONNECTION_FORBIDDEN";

  // Every shop-binding code, matched here for the same reason `credential_unusable`
  // is matched above: their statuses lead the merchant somewhere else entirely.
  // `shop_not_authorized` is a 403 and so was rendered as "you're not signed in
  // to this store any more" — a merchant with a perfectly healthy session sent
  // to sign in again over a stale shop list. The other three are 400s and 409s,
  // which match none of the status classes at the bottom of this function and so
  // arrived as a bare "Something went wrong": no cause, and no hint that the fix
  // is one tap away on the Suppliers screen.
  //
  // `shop_binding_required` is the one a merchant meets by accident. It is what
  // an order against an unbound connection answers, so it is the first time most
  // accounts will hear that a shop was ever needed. `shop_required` joins it
  // because the move is identical — go and choose one — and it is what the
  // connect form gets for a shop field that is not a usable string.
  //
  // The rest are reachable from a shop list that has gone stale under the
  // merchant: the picker marks unfulfillable shops before they are chosen, so
  // the only way to choose one is for the account to have changed since the list
  // was drawn. Stale is the normal state of a list left open, so none of them is
  // hypothetical.
  if (code === "shop_binding_required" || code === "shop_required") return "SHOP_BINDING_REQUIRED";
  if (code === "shop_not_authorized") return "SHOP_NOT_AUTHORIZED";
  if (code === "connection_binding_conflict") return "SHOP_BINDING_CONFLICT";
  if (code === "api_shop_binding_required") return "SHOP_CANNOT_FULFIL";
  if (code === "ambiguous_shop_name") return "SHOP_NAME_AMBIGUOUS";

  // Both lists are matched ahead of the bare status classes for the same reason
  // the block above is: a named code is the server being specific, and a status
  // is the server being generic, so letting the status win discards the only
  // information the merchant can act on. This ordering is load-bearing rather
  // than tidy -- `auth_expired` and `reauth_required` both arrive as 401, so
  // behind a `status === 401` catch-all every entry in DISCONNECTED_CODES that
  // matters here is unreachable. That is not hypothetical: a CJ key CJ itself
  // rejects ("APIkey is wrong") comes back as `reauth_required` 401 and reached
  // the merchant as "You're not signed in to this store any more", which sends
  // someone whose PulseSoc session is perfectly healthy off to sign in again
  // while the supplier connection stays broken.
  if (DISCONNECTED_CODES.includes(code)) return "SUPPLIER_DISCONNECTED";
  if (PROVIDER_CODES.includes(code)) return "PROVIDER_UNAVAILABLE";
  if (error.status === 401 || error.status === 403) return "UNAUTHORIZED";
  if (error.status === 429 || error.status === 503 || error.status === 504) return "PROVIDER_UNAVAILABLE";
  return "ERROR";
}

/**
 * How long the caller should wait before retrying, in seconds, when the server
 * said. `null` when it did not — a made-up backoff is worse than none, because
 * the screen then promises a retry time it has no basis for.
 */
export function retryAfterSeconds(error: unknown): number | null {
  if (!(error instanceof PulseApiError)) return null;
  return centsOrNull(error.details?.retry_after);
}

/* ------------------------------------------------------------------ *
 * Unsourced surfaces
 * ------------------------------------------------------------------ */

export type DropshippingDataGap = { surface: string; needs: string };

/**
 * Parts of the merchant journey this app has no data source for.
 *
 * Listed rather than mocked. A supplier-orders screen populated with invented
 * rows would be read as "these orders were placed with your supplier", which is
 * the one claim in this whole feature a merchant would act on financially.
 *
 * `needs` is merchant-readable, because `DropshippingOrdersScreen` renders it.
 * It used to be an implementation note, and one of them named a table
 * (`business_os_supplier_fulfillment_intents`) that does not exist anywhere in
 * the repo — so the note meant to tell the next implementer where to look sent
 * them to a name nothing has. A gap note is a claim about the system like any
 * other, and this one had never been checked against it.
 *
 * Two entries left this list when `listSupplierObligations` arrived. They said
 * the fulfilment layer "can create and read a single supplier order by id" and
 * only lacked an enumeration; in fact nothing reachable created one either, so
 * both the stated gap and the capability it assumed were wrong. The entry that
 * remains is the one that is still true.
 */
export const DROPSHIPPING_DATA_GAPS: readonly DropshippingDataGap[] = [
  {
    // `list_obligations` enumerates paid orders that owe a supplier purchase.
    // It deliberately does not enumerate an order refunded or cancelled *after*
    // a supplier order was placed: the merchant needs to cancel with the
    // supplier, and that is a different action from placing one. There is no
    // cancellation path, so listing those rows here would imply one exists.
    surface: "Cancelling a supplier order",
    needs:
      "When a customer refunds an order you've already bought from your supplier, you'll be able to cancel it with them from here."
  }
] as const;

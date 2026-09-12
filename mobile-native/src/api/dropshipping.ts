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
  "NEEDS_REVIEW"
] as const;
export type ImportOutcome = (typeof IMPORT_OUTCOMES)[number];

/** Why a draft cannot be published yet. Every reason, not the first one. */
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
  "RESTRICTED_PRODUCT"
] as const;
export type PublishProblem = (typeof PUBLISH_PROBLEMS)[number];

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
 * Import
 * ------------------------------------------------------------------ */

export type ImportItemResult = {
  itemId: string;
  externalProductId: string;
  provider: string;
  outcome: ImportOutcome | string;
  /** Set only on `IMPORTED` and `ALREADY_EXISTS`. */
  listingId: number | null;
  /**
   * A machine code narrowing why an item was refused, when the server had one.
   * Never provider prose — a supplier's error string is not something to render
   * to a merchant, and not something to put in a log either.
   */
  detail: string | null;
  /** How many variants were written. Only meaningful on `IMPORTED`. */
  variantCount: number | null;
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
    variantCount: centsOrNull(raw.variant_count)
  };
}

export type ImportRunResult = {
  results: ImportItemResult[];
  requested: number;
  imported: number;
  /** Only the outcomes that actually occurred, so a zero never needs rendering. */
  counts: Partial<Record<ImportOutcome | string, number>>;
  /**
   * Always false. Import creates drafts; publishing is a separate, deliberate
   * merchant act. Kept on the type because the server states it explicitly and
   * a screen that reads it cannot drift into assuming otherwise.
   */
  published: boolean;
  pricingRule: PricingRule;
};

/**
 * Import the selected cart rows.
 *
 * Takes ids and an optional pricing rule — nothing else. The rule is arithmetic
 * the server applies to a cost *it* fetched, so it cannot be used to assert one.
 *
 * Idempotent per product: re-running returns `ALREADY_IMPORTED` for anything the
 * merchant already has, rather than a second listing. That is why a retry after
 * a dropped connection is safe.
 */
export async function importSelected(
  scope: DropshippingScope,
  connectionId: string,
  input: { itemIds: string[]; pricingRule?: PricingRule }
): Promise<ImportRunResult> {
  const response = await pulseApi<Record<string, unknown>>(
    `${BASE}/connections/${encodeURIComponent(connectionId)}/import`,
    {
      method: "POST",
      body: scopeBody(scope, { item_ids: input.itemIds, pricing_rule: input.pricingRule })
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
    pricingRule: normalizePricingRule(response.pricing_rule)
  };
}

/** Outcomes the merchant does not need to act on. */
const BENIGN_OUTCOMES = ["IMPORTED", "ALREADY_EXISTS"];

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
 * Exported so a test can assert the count: if someone later fakes one of these,
 * the list changes and the test says so.
 */
export const DROPSHIPPING_DATA_GAPS: readonly DropshippingDataGap[] = [
  {
    // The fulfillment layer can create and read a single intent by id, but
    // nothing enumerates a merchant's supplier orders.
    surface: "Supplier orders list",
    needs: "a merchant-scoped list endpoint over business_os_supplier_fulfillment_intents"
  },
  {
    // Tracking numbers land on the intent, which the same gap hides.
    surface: "Shipment tracking",
    needs: "tracking numbers surfaced on the same supplier-orders list"
  }
] as const;

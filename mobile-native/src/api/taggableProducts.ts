import { API_PREFIX, CommerceProduct, mapProduct, RawProduct } from "./commerceDiscovery";
import { pulseApi } from "./pulseApi";

/**
 * The creator's side of commerce discovery: which of my products can I tag, and
 * which of them will actually be shown?
 *
 * **Why this is not in `commerceDiscovery.ts`.** That module's first documented
 * invariant is "every read swallows its errors and returns empty", and it is
 * correct there — a recommendation failure must never break a viewer's feed, so
 * an empty list is the right thing to return when something breaks. This
 * endpoint needs the exact opposite, and putting a function with the inverse
 * contract inside a module whose header promises that contract is an invitation
 * for someone to "fix" the inconsistency in the wrong direction.
 *
 * The reason the contract inverts is what `[]` *means* on each surface. On a
 * viewer surface it means "no products here", which is a safe thing to say when
 * a query failed. On this surface it means "you have no products to tag" — a
 * claim about the creator's own store. Telling a seller with forty listings that
 * they have none is a confident lie, and the client renders it as an empty
 * state with no way back. So:
 *
 * - The server answers **500** with `code="TAGGABLE_PRODUCTS_UNAVAILABLE"` when
 *   it cannot read the catalogue, rather than `200 []`.
 * - This function lets that **throw**. Callers must have an error branch, and
 *   that is deliberate friction.
 * - A genuinely empty store is still `200 []`, so "empty" and "broken" are
 *   distinguishable by the caller. They must never render the same.
 *
 * **The two questions.** `tagging.attach` server-side asks whether the creator
 * owns the listing. `eligibility.gate` asks, separately and at *serve* time,
 * whether the listing can be shown at all. Nothing joins those two moments, so a
 * tag can be accepted — `ok: true`, clean post, row written — and the product
 * never appear to anybody, with no log line and nothing the creator could have
 * noticed. This endpoint exists to make that knowable before posting, which is
 * why every listing is returned with `serves` and `blockedReason` rather than
 * the unservable ones being filtered out. A product missing from your own picker
 * teaches you nothing; a product marked "no cover image" tells you what to fix.
 *
 * There is deliberately no `canTag` field. Every listing here is owned by the
 * caller, so it would be unconditionally true, and a field that is always true
 * teaches a client to stop reading it.
 */

/**
 * Why a listing will not be shown. Wire values from
 * `services/commerce_discovery/eligibility.py`'s `INELIGIBLE_CODES`, used as the
 * second half of an i18n key (`commerce:discovery.tagging.blocked.<code>`), so a
 * paraphrase here type-checks fine and renders a missing-key placeholder under a
 * product. Keep them byte-identical to the server.
 *
 * `eligibility` declares eight codes; only these six are reachable from a
 * composer. `suppressed` and `over_exposed` are per-viewer judgements — one
 * viewer hid this seller, or this viewer has seen it too often — and there is no
 * viewer in a request a creator makes about their own store.
 */
export type TaggableBlockedReason =
  | "not_purchasable"
  | "no_cover_image"
  | "no_resolvable_price"
  | "moderation_flagged"
  | "listing_risk"
  | "seller_risk";

/**
 * What the picker may render. The six wire codes plus the fallback for a code
 * this build has never heard of, which is a real case: the server can add an
 * `INELIGIBLE_CODES` entry and installed clients keep running.
 */
export type TaggableBlockedLabel = TaggableBlockedReason | "unknown_reason";

export type TaggableProduct = {
  listingId: number;
  product: CommerceProduct;
  /** False means tagging it is allowed and pointless: it will never be shown. */
  serves: boolean;
  /** Empty exactly when `serves` is true. */
  blockedReason: TaggableBlockedLabel | "";
};

export type TaggableProductsResult = {
  products: TaggableProduct[];
  /** `tagging.MAX_TAGGED_PER_CONTENT` — how many may be stored per post. */
  maxPerContent: number;
  /** `bot.PULSE_PRODUCT_TAG_REQUEST_LIMIT` — how many one request may carry. */
  requestLimit: number;
};

type RawTaggableProduct = {
  listing_id?: number;
  product?: RawProduct;
  serves?: boolean;
  blocked_reason?: string;
};

type TaggableProductsResponse = {
  ok?: boolean;
  products?: RawTaggableProduct[];
  max_per_content?: number;
  request_limit?: number;
};

/**
 * Both limits are read from the response, never defaulted to a literal here.
 *
 * These fallbacks exist only for a response that omits the key entirely, which
 * the current server never does. They are deliberately the *server's* present
 * values so a truncated response degrades to the real behaviour rather than to
 * something permissive — but a client-side copy of a server-side limit is a
 * divergence waiting for the limit to change, which is why the picker reads the
 * response and these are not exported.
 */
const FALLBACK_MAX_PER_CONTENT = 5;
const FALLBACK_REQUEST_LIMIT = 20;

const BLOCKED_REASONS: readonly TaggableBlockedReason[] = [
  "not_purchasable",
  "no_cover_image",
  "no_resolvable_price",
  "moderation_flagged",
  "listing_risk",
  "seller_risk"
];

/**
 * An unrecognised code becomes `unknown_reason`, not the raw string.
 *
 * The raw string would be rendered through an i18n lookup that cannot match,
 * putting a bare server identifier like `no_resolvable_price` under a product
 * card in every locale. A code this client does not know about still has to say
 * *something* true — "we can't show this one" — so the fallback direction is
 * toward a sentence a person can read, never toward the wire value.
 */
function mapBlockedReason(raw: string | undefined): TaggableBlockedLabel | "" {
  const code = String(raw || "").trim();
  if (!code) return "";
  return BLOCKED_REASONS.includes(code as TaggableBlockedReason)
    ? (code as TaggableBlockedReason)
    : "unknown_reason";
}

/**
 * `serves` is derived from the reason, not trusted alongside it.
 *
 * The server sends both, and they cannot disagree there (`serves` is literally
 * `not blocked`). But a client that reads them independently has two sources of
 * truth for one fact, and the failure mode is asymmetric: believing `serves` over
 * a present reason shows a creator a product as taggable-and-fine while telling
 * them why it is broken. Deriving from the reason means a malformed response
 * degrades to "blocked, here's why" rather than to a contradiction.
 */
function mapTaggableProduct(raw: RawTaggableProduct): TaggableProduct {
  const reason = mapBlockedReason(raw.blocked_reason);
  return {
    listingId: Number(raw.listing_id ?? raw.product?.listing_id ?? raw.product?.id) || 0,
    product: mapProduct(raw.product),
    serves: reason === "",
    blockedReason: reason
  };
}

/**
 * Fetch the signed-in creator's own taggable listings.
 *
 * Throws on failure — see the module header. Do not add a `catch` that returns
 * an empty result; that converts "we could not read your store" into "you have
 * no products", which is the one thing this endpoint was built to stop.
 */
export async function fetchTaggableProducts(limit?: number): Promise<TaggableProductsResult> {
  const query = typeof limit === "number" && limit > 0 ? `?limit=${Math.floor(limit)}` : "";
  const data = await pulseApi<TaggableProductsResponse>(`${API_PREFIX}/taggable-products${query}`);
  const rows = Array.isArray(data.products) ? data.products : [];
  return {
    products: rows.map(mapTaggableProduct).filter((row) => row.listingId > 0),
    maxPerContent: Number(data.max_per_content) || FALLBACK_MAX_PER_CONTENT,
    requestLimit: Number(data.request_limit) || FALLBACK_REQUEST_LIMIT
  };
}

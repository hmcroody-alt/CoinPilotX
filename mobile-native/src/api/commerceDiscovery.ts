import { pulseApi } from "./pulseApi";

/**
 * Client for organic + house commerce discovery.
 *
 * Mirrors `services/commerce_discovery_routes.py`. Three things about this
 * module are load-bearing and should survive any refactor:
 *
 * 1. **Every read swallows its errors and returns empty.** The server already
 *    guarantees a 200 with an empty list for every failure mode it can see; this
 *    layer extends that to the ones it cannot (offline, DNS, a 500 from a proxy).
 *    Callers therefore have no error branch, which is what makes "a
 *    recommendation failure must never break the feed" structural rather than a
 *    promise made in a code review.
 *
 * 2. **Writes report a boolean and never throw.** A failed impression beacon is
 *    an analytics gap, not a user-visible event. The one exception is feedback:
 *    a "hide this" that silently failed would leave the user believing they had
 *    told us something, so it returns its outcome and the caller decides.
 *
 * 3. **`promotionClass` is carried, never defaulted to a label.** A placement
 *    whose class the client does not recognise renders with the organic
 *    discovery label, never "Sponsored" — mislabelling unpaid reach as an ad is
 *    the one error in this system with a legal shape.
 */

export type CommerceSurface = "feed" | "reels" | "messenger" | "marketplace";

/** Matches `services/commerce_discovery/promotion.py`. Paid never arrives here. */
export type CommercePromotionClass = "organic" | "house";

/**
 * Reason codes from `ranking.choose_reason`. Rendered via i18n, never shown raw.
 *
 * These are wire values and must stay byte-identical to the constants in
 * `services/commerce_discovery/ranking.py` — they are the second half of an
 * i18n key (`commerce:discovery.subtitle.<reason>`), so a paraphrase here does
 * not fail the type-check, it just renders a missing-key placeholder under a
 * product card. Two of them read like abbreviations of themselves
 * (`related_to_this_post`, not `context`); resist tidying them.
 */
export type CommerceReason =
  | "matches_your_interests"
  | "because_you_viewed"
  | "related_to_this_post"
  | "from_sellers_you_follow"
  | "trending"
  | "new_arrival"
  | "popular"
  | "new_to_marketplace";

export type CommerceProduct = {
  listingId: number;
  title: string;
  priceLabel: string;
  coverImageUrl: string;
  sellerUserId: number;
  sellerStoreName: string;
  category: string;
  rating: number;
  ratingCount: number;
};

export type CommercePlacement = {
  placementId: string;
  impressionToken: string;
  surface: CommerceSurface | string;
  slot: number;
  expiresAt: string;
  promotionClass: CommercePromotionClass;
  /** i18n key for the placement's label. Never a literal string from the wire. */
  labelKey: string;
  reason: CommerceReason | string;
  rankingVersion: string;
  product: CommerceProduct;
  priceMinor: number;
  priceCurrency: string;
};

export type CommerceModule = {
  key: string;
  reason: string;
  titleKey: string;
  placements: CommercePlacement[];
};

export type CommerceContext = {
  category?: string;
  subcategory?: string;
  topic?: string;
  tags?: string[];
};

export type CommerceServeResult = {
  placements: CommercePlacement[];
  /** Server-owned viewability contract, so the rule lives in one place. */
  visiblePercentThreshold: number;
  visibleDwellMs: number;
};

const EMPTY_RESULT: CommerceServeResult = {
  placements: [],
  visiblePercentThreshold: 60,
  visibleDwellMs: 1000
};

const API_PREFIX = "/api/pulse/commerce/discovery";

type RawProduct = {
  id?: number;
  listing_id?: number;
  title?: string;
  price_label?: string;
  cover_image_url?: string;
  image_url?: string;
  seller_user_id?: number;
  seller_store_name?: string;
  store_name?: string;
  category?: string;
  rating?: number;
  rating_count?: number;
  review_count?: number;
};

type RawPlacement = {
  placement_id?: string;
  impression_token?: string;
  surface?: string;
  slot?: number;
  expires_at?: string;
  promotion_class?: string;
  label_key?: string;
  reason?: string;
  ranking_version?: string;
  product?: RawProduct;
  price_minor?: number;
  price_currency?: string;
};

function mapProduct(raw: RawProduct | undefined): CommerceProduct {
  const product = raw || {};
  return {
    listingId: Number(product.listing_id ?? product.id) || 0,
    title: product.title || "",
    priceLabel: product.price_label || "",
    coverImageUrl: product.cover_image_url || product.image_url || "",
    sellerUserId: Number(product.seller_user_id) || 0,
    sellerStoreName: product.seller_store_name || product.store_name || "",
    category: product.category || "",
    rating: Number(product.rating) || 0,
    ratingCount: Number(product.rating_count ?? product.review_count) || 0
  };
}

/**
 * An unrecognised class falls back to "organic", not to the raw string.
 *
 * The fallback direction is the whole point. Labelling an organic placement as
 * an ad is a false disclosure; labelling something unknown as organic is, at
 * worst, an under-disclosure of a class that by construction never reaches this
 * client — the serve endpoint asserts unpaid before it writes a placement row.
 */
function mapPromotionClass(raw: string | undefined): CommercePromotionClass {
  return raw === "house" ? "house" : "organic";
}

function mapPlacement(raw: RawPlacement): CommercePlacement {
  return {
    placementId: String(raw.placement_id || ""),
    impressionToken: String(raw.impression_token || ""),
    surface: raw.surface || "",
    slot: Number(raw.slot) || 0,
    expiresAt: raw.expires_at || "",
    promotionClass: mapPromotionClass(raw.promotion_class),
    // The fallback has to name a key that exists. There is no `marketplace`
    // i18n namespace — marketplace strings live under `commerce.marketplace` —
    // so a plausible-looking `marketplace:…` default renders the raw key string
    // as the card's headline, and only on the path where the server omitted the
    // label, which is the path no one looks at.
    labelKey: raw.label_key || "commerce:discovery.label.recommended",
    reason: raw.reason || "popular",
    rankingVersion: raw.ranking_version || "",
    product: mapProduct(raw.product),
    priceMinor: Number(raw.price_minor) || 0,
    priceCurrency: raw.price_currency || "USD"
  };
}

/**
 * A placement past its TTL cannot be reported against — the server rejects the
 * token — so renderers must drop it rather than show a card whose impression,
 * click and hide would all silently fail.
 */
export function isCommercePlacementExpired(placement: Pick<CommercePlacement, "expiresAt">): boolean {
  if (!placement.expiresAt) return false;
  const expiry = Date.parse(placement.expiresAt);
  if (Number.isNaN(expiry)) return false;
  return expiry <= Date.now();
}

function usable(placement: CommercePlacement): boolean {
  return (
    Boolean(placement.placementId) &&
    Boolean(placement.impressionToken) &&
    placement.product.listingId > 0 &&
    Boolean(placement.product.title) &&
    !isCommercePlacementExpired(placement)
  );
}

/**
 * Placements for one surface.
 *
 * POST rather than GET because `context` describes what the viewer is looking
 * at this second, and a query string is written to access logs, proxy caches and
 * analytics referrers. The method follows the payload.
 */
export async function fetchCommercePlacements(
  surface: CommerceSurface,
  options: { context?: CommerceContext; sessionId?: string; limit?: number } = {}
): Promise<CommerceServeResult> {
  try {
    const response = await pulseApi<{
      ok?: boolean;
      placements?: RawPlacement[];
      visible_percent_threshold?: number;
      visible_dwell_ms?: number;
    }>(`${API_PREFIX}/${surface}`, {
      method: "POST",
      body: JSON.stringify({
        context: options.context || {},
        session_id: options.sessionId || "",
        ...(options.limit === undefined ? {} : { limit: options.limit })
      })
    });
    if (!response?.ok || !Array.isArray(response.placements)) return EMPTY_RESULT;
    return {
      placements: response.placements.map(mapPlacement).filter(usable),
      visiblePercentThreshold:
        Number(response.visible_percent_threshold) || EMPTY_RESULT.visiblePercentThreshold,
      visibleDwellMs: Number(response.visible_dwell_ms) || EMPTY_RESULT.visibleDwellMs
    };
  } catch {
    return EMPTY_RESULT;
  }
}

/** The Marketplace shelves. Modules that lost every item to filtering are dropped. */
export async function fetchCommerceModules(sessionId = ""): Promise<CommerceModule[]> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
  try {
    const response = await pulseApi<{
      ok?: boolean;
      modules?: { key?: string; reason?: string; title_key?: string; placements?: RawPlacement[] }[];
    }>(`${API_PREFIX}/marketplace/modules${query}`, { method: "GET" });
    if (!response?.ok || !Array.isArray(response.modules)) return [];
    return response.modules
      .map((module) => ({
        key: String(module.key || ""),
        reason: String(module.reason || ""),
        titleKey: String(module.title_key || ""),
        placements: (module.placements || []).map(mapPlacement).filter(usable)
      }))
      .filter((module) => Boolean(module.key) && module.placements.length > 0);
  } catch {
    return [];
  }
}

type PlacementIdentity = Pick<CommercePlacement, "placementId" | "impressionToken">;

function identityBody(placement: PlacementIdentity, extra: Record<string, unknown> = {}) {
  return JSON.stringify({
    placement_id: placement.placementId,
    impression_token: placement.impressionToken,
    ...extra
  });
}

/**
 * Rendered, or crossed the viewability threshold.
 *
 * Two calls per card by design: `visible: false` when it mounts, `visible: true`
 * once it has held the threshold. The server stores them under different dedup
 * keys, so "served" and "actually seen" stay separable — collapsing them would
 * make every card that scrolled past in a blur count as an impression.
 */
export async function recordCommerceImpression(
  placement: PlacementIdentity,
  options: { visible?: boolean; viewDurationMs?: number } = {}
): Promise<boolean> {
  if (!placement.placementId) return false;
  try {
    const result = await pulseApi<{ ok?: boolean }>(`${API_PREFIX}/events/impression`, {
      method: "POST",
      body: identityBody(placement, {
        visible: Boolean(options.visible),
        view_duration_ms: Math.max(0, Math.round(options.viewDurationMs || 0))
      })
    });
    return Boolean(result?.ok);
  } catch {
    return false;
  }
}

export type CommerceEngagementAction =
  | "click"
  | "product_view"
  | "save"
  | "add_to_cart"
  | "checkout_started"
  | "purchase";

export async function recordCommerceEngagement(
  placement: PlacementIdentity,
  action: CommerceEngagementAction,
  extra: { valueMinor?: number; currency?: string; orderRef?: string } = {}
): Promise<boolean> {
  if (!placement.placementId) return false;
  try {
    const result = await pulseApi<{ ok?: boolean }>(`${API_PREFIX}/events/engagement`, {
      method: "POST",
      body: identityBody(placement, {
        action,
        value_minor: Math.max(0, Math.round(extra.valueMinor || 0)),
        currency: extra.currency || "",
        order_ref: extra.orderRef || ""
      })
    });
    return Boolean(result?.ok);
  } catch {
    return false;
  }
}

export type CommerceFeedbackAction =
  | "hide"
  | "not_interested"
  | "see_fewer"
  | "hide_seller"
  | "snooze";

/**
 * A negative signal, and the suppression it implies, in one round trip.
 *
 * Returns whether the server honoured it. Unlike the other writes this one is
 * worth surfacing: the card should collapse either way (an undismissable card is
 * a worse outcome than a lost preference), but a caller that knows the write
 * failed can re-send rather than leave the user having told us nothing.
 */
export async function recordCommerceFeedback(
  placement: PlacementIdentity,
  action: CommerceFeedbackAction,
  options: { category?: string } = {}
): Promise<boolean> {
  if (!placement.placementId) return false;
  try {
    const result = await pulseApi<{ ok?: boolean; suppressed?: boolean }>(
      `${API_PREFIX}/events/feedback`,
      {
        method: "POST",
        body: identityBody(placement, { action, category: options.category || "" })
      }
    );
    return Boolean(result?.ok && result.suppressed);
  } catch {
    return false;
  }
}

export type CommerceExplanation = {
  reason: string;
  /** Factor *names* — the server never sends scores, so there is none to leak. */
  factors: string[];
  rankingVersion: string;
};

/** "Why am I seeing this?". POST because the token is a capability, not a filter. */
export async function explainCommercePlacement(
  placement: PlacementIdentity
): Promise<CommerceExplanation | null> {
  if (!placement.placementId) return null;
  try {
    const result = await pulseApi<{
      ok?: boolean;
      reason?: string;
      factors?: string[];
      ranking_version?: string;
    }>(`${API_PREFIX}/explain/${encodeURIComponent(placement.placementId)}`, {
      method: "POST",
      body: JSON.stringify({ impression_token: placement.impressionToken })
    });
    if (!result?.ok) return null;
    return {
      reason: String(result.reason || ""),
      factors: Array.isArray(result.factors) ? result.factors.map(String) : [],
      rankingVersion: String(result.ranking_version || "")
    };
  } catch {
    return null;
  }
}

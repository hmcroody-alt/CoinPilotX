/**
 * The related-products row under a canonical Marketplace product.
 *
 * ## Why this surface is not like the other five
 *
 * Every other discovery surface interrupts something: a feed being scrolled, a
 * reel being watched, a conversation list being scanned. This one does not. The
 * user is looking at a product, and "here are six more like it" is the
 * navigation the screen owes them — a product page with no way onward is a dead
 * end, not a restrained one.
 *
 * That difference is why the numbers here point the opposite way to everywhere
 * else: `product_detail` is the only surface whose per-session cap is in the
 * hundreds and whose relevance floor sits *below* the feed's. Both are set
 * server-side; this hook only has to not fight them.
 *
 * ## Why the row is a shelf and not a card
 *
 * It renders through `MarketplaceDiscoveryShelves`, the component the Marketplace
 * home already uses, by wrapping the serve response in a single
 * `CommerceModule`. Nothing about a rail of products on a product page differs
 * from a rail of products on the shop home, so a second implementation would
 * only be a second place for the price formatting, the impression beacons, the
 * ••• menu and the "Why am I seeing this?" sheet to drift. The brief's
 * instruction not to duplicate product formatting across ten screens is easier
 * to keep by reusing the shelf than by keeping four card files in agreement.
 *
 * ## Why only the listing id goes to the server
 *
 * The other context-carrying surfaces describe their context in the request
 * body: a post's caption tokens, a reel's topic. This hook sends an id and
 * nothing else, and the server reads the product's category itself.
 *
 * That is deliberate. This is the one surface whose reason code makes a claim
 * about a *comparison* — `similar_to_this_product` — and a comparison is only
 * true if the server owns both sides of it. A client that described the product
 * it was displaying inaccurately, for any reason including a stale render, would
 * get back a row of genuinely-similar-to-something-else products sitting under
 * the word "similar".
 *
 * The id is also what excludes the product from its own recommendations. Both
 * uses only ever *narrow* the result, which is what makes accepting an id from
 * the client safe: there is no value that widens what comes back.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CommerceFeedbackAction,
  CommerceModule,
  CommercePlacement,
  fetchCommercePlacements,
  recordCommerceFeedback
} from "../api/commerceDiscovery";
import { startCommercePause } from "./consent";
import { commerceSessionId } from "./session";

export type UseProductDetailCommerceOptions = {
  /**
   * The product on screen, or 0 while it loads.
   *
   * Zero also covers a product that failed to load or was withdrawn. In both
   * cases there is no request: without the anchor id the server could neither
   * exclude this product from the row nor read the category the row's heading
   * claims similarity to.
   */
  listingId: number;
  /**
   * False whenever the row must not exist at all — signed out, or still loading.
   *
   * Explicitly **not** the master switch. "Show Marketplace suggestions"
   * governs being recommended to while doing something else; a product page is
   * inside Marketplace, which the settings screen promises keeps working
   * either way. `preferences.SOCIAL_SURFACES` is the server half of the same
   * line, which is why this hook reads `useSocialDiscoveryAllowed` no more than
   * `useMarketplaceCommerce` does.
   */
  enabled?: boolean;
  /** Bumped by the screen on pull-to-refresh. */
  refreshToken?: number;
};

export type ProductDetailCommerceState = {
  /**
   * The row, as zero or one module. An array because that is what the shelf
   * component takes, and an empty one renders as nothing rather than as an empty
   * container with padding.
   */
  modules: CommerceModule[];
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
  sessionId: string;
};

/** Matches `config.product_detail_row_size()`. Used until the server answers. */
const LOCAL_CADENCE = { leadIn: 0, interval: 1, maxPerPage: 6 };

/**
 * Below this the row is dropped rather than rendered short. Matches
 * `MIN_MODULE_ITEMS` in `services/commerce_discovery_routes.py`.
 *
 * Two products under "Similar products" reads as the catalogue being empty,
 * which on this screen is a statement about the shop rather than about the
 * ranking.
 */
const MIN_ROW_ITEMS = 3;

/**
 * Reason code to shelf-heading key, mirroring `MARKETPLACE_MODULES` on the
 * server.
 *
 * Two of these are not identity — `matches_your_interests` is headed
 * "Recommended for you" and `new_arrival` is headed "New arrivals" — which is
 * why this map exists rather than interpolating the reason straight into the
 * i18n key. Doing that would render a missing-key placeholder as a shelf
 * heading for those two.
 *
 * `related_to_this_post` is absent on purpose: the server substitutes
 * `similar_to_this_product` for this surface, so a placement carrying the post
 * wording here would be a server bug, and falling through to `popular` is the
 * right way to not repeat it in the UI.
 */
/**
 * The heading for a row whose cards were justified several different ways.
 *
 * Deliberately the weakest statement in the vocabulary: it claims no similarity,
 * no trend and nothing about the viewer's history, because on a mixed row each of
 * those would be false of some card in it.
 */
const NEUTRAL_HEADING_KEY = "you_might_also_like";

const HEADING_KEYS: Record<string, string> = {
  similar_to_this_product: "similar_to_this_product",
  because_you_viewed: "because_you_viewed",
  matches_your_interests: "recommended_for_you",
  from_sellers_you_follow: "from_sellers_you_follow",
  trending: "trending",
  new_arrival: "new_arrivals",
  new_to_marketplace: "new_to_marketplace",
  popular: "popular"
};

export function useProductDetailCommerce({
  listingId,
  enabled: callerEnabled = true,
  refreshToken = 0
}: UseProductDetailCommerceOptions): ProductDetailCommerceState {
  const anchorId = Number(listingId || 0);
  const enabled = callerEnabled && anchorId > 0;

  const [placements, setPlacements] = useState<CommercePlacement[]>([]);
  const [dismissedPlacementIds, setDismissedPlacementIds] = useState<ReadonlySet<string>>(new Set());
  const [dismissedSellerIds, setDismissedSellerIds] = useState<ReadonlySet<number>>(new Set());

  const sessionId = commerceSessionId();
  const snoozedRef = useRef(false);

  useEffect(() => {
    if (!enabled) {
      setPlacements([]);
      return undefined;
    }
    let cancelled = false;
    fetchCommercePlacements("product_detail", {
      sessionId,
      limit: LOCAL_CADENCE.maxPerPage,
      cadence: LOCAL_CADENCE,
      listingId: anchorId
    })
      .then((result) => {
        if (cancelled || snoozedRef.current) return;
        setPlacements(result.placements);
      })
      // Belt and braces: `fetchCommercePlacements` already resolves empty rather
      // than throwing. A recommendation failure must not reach the screen where
      // the user is trying to buy something.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [anchorId, enabled, refreshToken, sessionId]);

  // Navigating between products reuses this hook, so the session-local dismissals
  // are no longer about anything on screen. The server still holds them, so this
  // cannot un-hide anything.
  useEffect(() => {
    setDismissedPlacementIds(new Set());
    setDismissedSellerIds(new Set());
  }, [anchorId, refreshToken]);

  const onFeedback = useCallback((placement: CommercePlacement, action: CommerceFeedbackAction) => {
    if (action === "snooze") {
      snoozedRef.current = true;
      setPlacements([]);
      startCommercePause();
    } else if (action === "hide_seller") {
      const sellerId = placement.product.sellerUserId;
      if (sellerId) {
        setDismissedSellerIds((current) => {
          if (current.has(sellerId)) return current;
          const next = new Set(current);
          next.add(sellerId);
          return next;
        });
      }
      setDismissedPlacementIds((current) => withPlacement(current, placement.placementId));
    } else {
      setDismissedPlacementIds((current) => withPlacement(current, placement.placementId));
    }

    recordCommerceFeedback(placement, action).catch(() => undefined);
  }, []);

  const modules = useMemo<CommerceModule[]>(() => {
    const visible = placements.filter((candidate) => {
      if (!candidate?.placementId) return false;
      if (!candidate.product?.listingId || !candidate.product?.title) return false;
      // The server already excludes the anchor. Re-checked here because this is
      // the one wrong answer on this surface that a user would definitely
      // notice, and the check costs a comparison.
      if (candidate.product.listingId === anchorId) return false;
      if (dismissedPlacementIds.has(candidate.placementId)) return false;
      const sellerId = candidate.product.sellerUserId;
      if (sellerId && dismissedSellerIds.has(sellerId)) return false;
      return true;
    });
    if (visible.length < MIN_ROW_ITEMS) return [];

    // The heading is the row's only user-visible claim — the shelf renders no
    // per-card subtitle — so it may only say something true of *every* card in
    // it. "Similar products" over a row where two items cleared the relevance
    // floor on freshness is the fabricated-claim failure the brief names.
    //
    // The Marketplace home keeps this honest by splitting the pool into one
    // shelf per reason. That does not port: it has eight shelves and a pool of
    // forty-eight, while this surface has one row and a server-side cap of six,
    // so requiring a homogeneous group would leave the row empty on any mixed
    // response — which is most of them.
    //
    // So the *heading* narrows instead of the row. Every card is kept, and the
    // specific claim is made only when it holds throughout; otherwise the row
    // falls back to a heading that asserts nothing. Per-card justification is
    // still one tap away in "Why am I seeing this?", which is where a claim
    // about an individual card belongs.
    const reasons = new Set(visible.map((candidate) => candidate.reason || "popular"));
    const sole = reasons.size === 1 ? [...reasons][0] : "";
    const headingKey = sole ? HEADING_KEYS[sole] || NEUTRAL_HEADING_KEY : NEUTRAL_HEADING_KEY;

    return [
      {
        key: `product_detail_${headingKey}`,
        reason: sole,
        titleKey: `commerce:discovery.module.${headingKey}`,
        placements: visible
      }
    ];
  }, [anchorId, dismissedPlacementIds, dismissedSellerIds, placements]);

  return useMemo(() => ({ modules, onFeedback, sessionId }), [modules, onFeedback, sessionId]);
}

function withPlacement(current: ReadonlySet<string>, placementId: string): ReadonlySet<string> {
  if (!placementId || current.has(placementId)) return current;
  const next = new Set(current);
  next.add(placementId);
  return next;
}

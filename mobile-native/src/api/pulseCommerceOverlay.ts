import type { MarketplacePurchaseBlock } from "./marketplaceBuyerPresentation";
import { marketplacePurchaseBlock } from "./marketplaceBuyerPresentation";
import type { MarketplaceListing } from "./marketplace";

/**
 * The live commerce overlay a PulseDrop publication carries beside its media.
 *
 * PulseDrop renders no price, no stock and no call to action into its pixels --
 * not into the Signal's image and not into the Reel's video. A Reel published in
 * March is still in the feed in September, by which time the price has changed
 * twice and the product may be gone. So the video is history and this object is
 * the present, read fresh from `marketplace_listings` on every serialization by
 * `services/pulsedrop/hydration.py`.
 *
 * The consequence for this file: **never cache it, never persist it, and never
 * reconstruct it from a stale copy.** It is delivered with the post precisely so
 * it cannot be older than the post's own read. The reels feed cache in
 * `reels.ts` writes whole `PulseReel` objects to AsyncStorage, which is why
 * `commerceOverlayIsStale` exists below and why the offline path shows the
 * product without a price rather than showing yesterday's price.
 */
export type PulseCommerceOverlay = {
  /** Always true when present. A discriminator, so `post.commerce` reads as one. */
  pulsedrop: true;
  surface: "signal" | "reel" | string;
  publication_id: number;
  published_at?: string;
  attribution: PulseCommerceAttribution;
  product: PulseCommerceProduct;
  seller: PulseCommerceSeller;
  label: PulseCommerceLabel;
  cta: PulseCommerceCta;
  availability: PulseCommerceAvailability;
  /** One sentence for screen readers, assembled server-side. See below. */
  accessibility_text: string;
};

/**
 * Who published, who sells, and what is being sold -- as three named roles.
 *
 * PulseDrop is the **publisher**. The seller is the **merchant**. The listing is
 * the **commerce object**. An analytics consumer that flattens these into one
 * "owner" id makes PulseDrop the seller of every product it has ever posted,
 * which is wrong in the ledger, wrong in the dispute queue and wrong on a
 * payout. The roles are spelled out on the wire so they cannot be confused by a
 * consumer that never read this comment.
 */
export type PulseCommerceAttribution = {
  /** `pd1.<publication>.<surface>.<listing>` -- the forward-compatible join key. */
  token: string;
  publisher_role: "publisher";
  merchant_role: "merchant";
  listing_id: number;
  seller_user_id: number;
  surface: string;
};

export type PulseCommerceProduct = {
  listing_id: number;
  title: string;
  /** Formatted by the server in the listing's own currency. Never re-format it. */
  price_label: string;
  currency: string;
  image_url: string;
  /** The four fields `marketplacePurchaseBlock` reads, so it can be run here. */
  buyer_visible: boolean;
  inventory_state: string;
  quantity: number;
  product_type: string;
  /** The server's own verdict, in the same vocabulary. See `commerceBlock`. */
  denial_code: MarketplacePurchaseBlock | "REMOVED" | "";
  /**
   * No `route`/`screen` here. The product's destination lives on {@link
   * PulseCommerceCta}, which withdraws it for every state that would 404. A
   * second copy on this block would be a route that outlives the withdrawal.
   */
};

export type PulseCommerceSeller = {
  seller_user_id: number;
  store_name: string;
  username: string;
  route: string;
  screen: string;
};

export type PulseCommerceLabel = {
  /** `TRENDING`, `NEW_DROP`, `POPULAR`, `TOP_PICK`, `DISCOVERY`. */
  key: string;
  i18n_key: string;
  fallback: string;
  /** Why the server made this claim, for the admin log. Not for display. */
  evidence?: string;
};

export type PulseCommerceCta = {
  code: "VIEW_PRODUCT" | "NONE" | string;
  i18n_key: string;
  fallback: string;
  enabled: boolean;
  /** Empty for every unroutable state. A CTA with no route must not be tappable. */
  route: string;
  screen: string;
  url: string;
};

export type PulseCommerceAvailability = {
  code: PulseCommerceAvailabilityCode;
  i18n_key: string;
  fallback: string;
  purchasable: boolean;
};

/**
 * `""` means available, matching `MarketplacePurchaseBlock`'s no-block sentinel.
 *
 * This is deliberately the marketplace's vocabulary and not a second one. The
 * same listing can appear on a product screen and in a PulseDrop Reel in the
 * same session; two independently-derived answers would eventually disagree on
 * one screen about one product, and the user would be right to believe either.
 * `REMOVED` is the single addition -- a listing row that no longer exists, which
 * a marketplace screen never has to describe because it 404s first.
 */
export type PulseCommerceAvailabilityCode = MarketplacePurchaseBlock | "REMOVED";

/** Present and shaped like an overlay. Written as a guard so `any` stops here. */
export function isPulseCommerceOverlay(value: unknown): value is PulseCommerceOverlay {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<PulseCommerceOverlay>;
  return item.pulsedrop === true && typeof item.publication_id === "number" && Boolean(item.product);
}

/**
 * The availability code, preferring the server's and falling back to the
 * client's own derivation of the same four fields.
 *
 * Both are here on purpose. The server is authoritative -- it read the row and
 * it knows about `REMOVED`, which the client's helper has no input for. But
 * running `marketplacePurchaseBlock` over the shipped fields is what keeps the
 * two implementations honest: they are given the same inputs, so a divergence
 * is a bug in one of them rather than an invisible difference of opinion. The
 * parity is asserted in `__tests__/pulseCommerceOverlay.test.ts`.
 */
export function commerceBlock(overlay: PulseCommerceOverlay): PulseCommerceAvailabilityCode {
  const declared = overlay.availability?.code;
  if (declared === "REMOVED") return "REMOVED";
  if (declared === "UNAVAILABLE" || declared === "OUT_OF_STOCK" || declared === "NOT_PRICED") return declared;
  if (declared === "") return "";
  // No usable verdict: derive it rather than assume the best. A shape this
  // function does not recognise is an older or newer server, and defaulting an
  // unknown to "available" is how a sold-out product gets an enabled button.
  return marketplacePurchaseBlock(commerceListingShim(overlay));
}

/** Just enough of a `MarketplaceListing` for the shared helper to read. */
function commerceListingShim(overlay: PulseCommerceOverlay): MarketplaceListing {
  const product = overlay.product || ({} as PulseCommerceProduct);
  return {
    id: product.listing_id,
    title: product.title,
    price_label: product.price_label,
    currency: product.currency,
    quantity: product.quantity,
    product_type: product.product_type,
    listing_type: product.product_type,
    inventory_state: product.inventory_state,
    buyer_visible: product.buyer_visible,
  } as MarketplaceListing;
}

/**
 * May this overlay's call to action be tapped?
 *
 * Gated on the route as well as the flag. `/pulse/marketplace/<id>` answers 404
 * for any listing that is not public -- deliberately, so that guessing an id
 * cannot confirm a row exists -- so a button that navigates to a withdrawn
 * product is a button that navigates to an error screen. The server already
 * declines to emit a route for those states; this refuses to trust that it
 * always will.
 */
export function commerceCtaEnabled(overlay: PulseCommerceOverlay): boolean {
  const cta = overlay.cta;
  if (!cta || !cta.enabled) return false;
  if (!cta.route) return false;
  return commerceBlock(overlay) === "";
}

/**
 * Whether the price may be shown for this state.
 *
 * The line is drawn at *who took the product down*, matching `_DISCLOSED` in
 * `hydration.py`. Sold out and not-priced are the seller still offering the
 * listing, so the price stays and the state is a pill beside it. Withdrawn,
 * unapproved, suspended and deleted were taken off sale, and continuing to
 * advertise a price for them under a verified badge would make this the one
 * PulseSoc surface that does not honour a withdrawal.
 *
 * The server already omits the price for those states. This mirrors the rule
 * rather than testing for an empty string, so a client that somehow receives a
 * price it should not show still does not show it.
 */
export function commercePriceVisible(overlay: PulseCommerceOverlay): boolean {
  const block = commerceBlock(overlay);
  return block === "" || block === "OUT_OF_STOCK" || block === "NOT_PRICED";
}

/**
 * A cached overlay is not an overlay.
 *
 * The reels feed and reel detail are both written to AsyncStorage so the app
 * opens with something on screen. That cache is correct for pixels, captions and
 * author names -- none of which change -- and wrong for every field in here. A
 * restored offline reel must show the product and no price at all, because
 * "$49.00" from three days ago is a specific false claim, while no price is a
 * visibly incomplete card the user can refresh.
 */
export function commerceOverlayIsStale(source: "network" | "cache"): boolean {
  return source === "cache";
}

/**
 * Strip the perishable half of an overlay before it is written to a cache.
 *
 * Keeps the parts that are facts about the publication -- that it is a PulseDrop
 * post, which product it is about, who the merchant is -- and drops price, stock,
 * availability and the call to action. The result renders as a product card
 * awaiting refresh rather than as a card that lies with confidence.
 */
export function commerceOverlayForCache(
  overlay: PulseCommerceOverlay | undefined
): PulseCommerceOverlay | undefined {
  if (!overlay) return undefined;
  return {
    ...overlay,
    product: {
      ...overlay.product,
      price_label: "",
      inventory_state: "",
      quantity: 0,
      denial_code: "",
    },
    cta: { ...overlay.cta, enabled: false, route: "", url: "" },
    availability: { code: "", i18n_key: "", fallback: "", purchasable: false },
  };
}

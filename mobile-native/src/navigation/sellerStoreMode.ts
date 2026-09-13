import { RootStackParamList } from "./types";

export type SellerStoreMode = NonNullable<NonNullable<RootStackParamList["SellerStore"]>["mode"]>;

/** The panels `SellerStoreScreen` renders, in the order they appear. */
export type SellerStorePanel =
  | "hero"
  | "application"
  | "listings"
  | "inventory"
  | "media"
  | "orders"
  | "trust";

const ALL_PANELS: SellerStorePanel[] = ["hero", "application", "listings", "inventory", "media", "orders", "trust"];

/**
 * `SellerStore` accepted a `mode` param that was read once and never used, so
 * every caller landed on the same undifferentiated wall of panels. Business OS
 * links to Store, Orders, Payments and Business Profile — four distinct jobs —
 * so the mode has to actually mean something for those links to be honest.
 *
 * `overview` keeps the original everything-at-once view, which is what existing
 * callers (and any deep link without a mode) get, so no current entry point
 * changes behaviour.
 */
const PANELS_BY_MODE: Record<SellerStoreMode, SellerStorePanel[]> = {
  overview: ALL_PANELS,
  dashboard: ["hero", "listings", "inventory", "media"],
  apply: ["hero", "application", "trust"],
  profile: ["hero", "application", "trust"],
  // `inventory` is the panel holding the listing editor, so a mode named
  // Listings that omitted it could create a product but never change one.
  create: ["hero", "listings", "inventory", "media"],
  /**
   * One product, nothing else.
   *
   * The editor lives inside the `inventory` panel, so every mode that showed it
   * also showed whatever panels preceded it. `create` was what Store's Edit
   * used, and it renders `hero` (Storefront readiness, four metrics, a
   * Marketplace button) and `listings` (Listing management, Capture Product
   * Media, Create Listing, five rows) first. The editor did open on the right
   * product — it was simply below all of that, under a heading that said
   * "Listings". The merchant reasonably read it as the wrong screen.
   *
   * So this is a panel-set fix, not a new screen: same editor, nothing above it.
   * `media` is excluded too — the editor has its own Add media button, and the
   * panel is a store-wide capture surface, not part of this product.
   */
  product: ["inventory"],
  payouts: ["hero", "orders", "trust"],
  orders: ["hero", "orders"]
};

const TITLES: Record<SellerStoreMode, { title: string; subtitle: string }> = {
  overview: {
    title: "Seller / Store",
    subtitle: "Run your store with PulseSoc approval, media, payouts and checkout."
  },
  dashboard: {
    title: "Store",
    subtitle: "Your listings, inventory and product media."
  },
  apply: {
    title: "Merchant application",
    subtitle: "Apply to sell on the PulseSoc marketplace."
  },
  profile: {
    title: "Business profile",
    subtitle: "How buyers see your business, and what unlocks selling."
  },
  create: {
    title: "Listings",
    subtitle: "Create listings and manage product media."
  },
  /**
   * A fallback only. The caller knows the product's name and passes it, so
   * `sellerStoreHeading` takes that title and uses this pair just for the
   * subtitle — and for the case where a deep link arrives with an id but no
   * title, where "Edit product" is at least honest about where you are.
   */
  product: {
    title: "Edit product",
    subtitle: "Your product's details, price, stock and readiness."
  },
  payouts: {
    title: "Payments",
    subtitle: "Orders, payouts and payment eligibility."
  },
  orders: {
    title: "Orders",
    subtitle: "Orders buyers placed with you."
  }
};

export function sellerStorePanels(mode?: string): SellerStorePanel[] {
  return PANELS_BY_MODE[(mode || "overview") as SellerStoreMode] || ALL_PANELS;
}

export function sellerStoreShowsPanel(mode: string | undefined, panel: SellerStorePanel) {
  return sellerStorePanels(mode).includes(panel);
}

/**
 * `productTitle` is used by the single-product editor only, and only when it is
 * a non-blank string. The header is the merchant's proof that Edit opened the
 * row they tapped, so it has to be the product's own name rather than a generic
 * one — but a blank or whitespace title must fall back rather than render an
 * empty header, which is what a listing saved without a name would otherwise do.
 */
export function sellerStoreHeading(mode?: string, productTitle?: string) {
  const heading = TITLES[(mode || "overview") as SellerStoreMode] || TITLES.overview;
  if (mode === "product" && productTitle && productTitle.trim()) {
    return { title: productTitle.trim(), subtitle: heading.subtitle };
  }
  return heading;
}

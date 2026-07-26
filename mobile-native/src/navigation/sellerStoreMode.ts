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
  create: ["hero", "listings", "media"],
  payouts: ["hero", "orders", "trust"],
  orders: ["hero", "orders"]
};

const TITLES: Record<SellerStoreMode, { title: string; subtitle: string }> = {
  overview: {
    title: "Seller / Store",
    subtitle: "Native marketplace control layer using PulseSoc approval, media, payout, and checkout systems."
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

export function sellerStoreHeading(mode?: string) {
  return TITLES[(mode || "overview") as SellerStoreMode] || TITLES.overview;
}

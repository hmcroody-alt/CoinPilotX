/**
 * Which name a buyer reads for a seller — one answer, one place.
 *
 * A buyer transacts with a store, not with a person. The server now projects the
 * storefront name explicitly as `seller_store_name` on every buyer payload
 * (product, cart, checkout, order, receipt), and keeps `seller_name` as an alias
 * of the same value so older payloads still render. Neither field is ever the
 * account holder's personal name; that identity is admin- and payout-side only.
 *
 * Screens call `sellerStoreName(...)` instead of writing their own
 * `a || b || "Seller"` chain. The chain is the part that rots: one screen adds
 * `display_name` to its fallbacks, a buyer sees the owner's legal name in their
 * cart, and nothing in CI notices. Routing still uses `seller_user_id` — a name
 * is never an address.
 */

const FALLBACK_STORE_NAME = "PulseSoc Store";

type SellerIdentityLike = {
  seller_store_name?: unknown;
  seller_name?: unknown;
  store_name?: unknown;
};

/** The store name, or "" when the payload genuinely carries none. */
export function sellerStoreNameOrEmpty(source: SellerIdentityLike | null | undefined): string {
  const row = source ?? {};
  for (const value of [row.seller_store_name, row.seller_name, row.store_name]) {
    const text = typeof value === "string" ? value.trim() : "";
    if (text) return text;
  }
  return "";
}

/** The string to render. Never empty, never a person's name. */
export function sellerStoreName(source: SellerIdentityLike | null | undefined): string {
  return sellerStoreNameOrEmpty(source) || FALLBACK_STORE_NAME;
}

/** First letter for an avatar placeholder, from the store name. */
export function sellerStoreInitial(source: SellerIdentityLike | null | undefined): string {
  return sellerStoreName(source).slice(0, 1).toUpperCase();
}

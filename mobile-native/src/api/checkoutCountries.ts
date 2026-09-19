/**
 * `GET /api/pulse/marketplace/cart/checkout-options` — the deployment facts the
 * checkout form cannot invent for itself: the delivery-country allowlist, and
 * whether the Marketplace card rail is switched on.
 *
 * The address country used to be a two-character text box: the buyer typed
 * `US`, and anything else — `USA`, `United States`, a lowercase `gb` — was
 * either silently uppercased into something the server rejected or accepted as
 * a country the seller does not ship to. A picker cannot produce those, which
 * is the whole reason for this module.
 *
 * A picker has its own failure mode though: offering a country the server will
 * refuse. `services/marketplace_fulfillment.validate_details` accepts only the
 * codes in `MARKETPLACE_SHIPPING_COUNTRIES` (default `US`), and Stripe's
 * `allowed_countries` reads the same variable — so the list is deployment
 * configuration, not a constant, and the client has to ask for it. That is what
 * `GET /api/pulse/marketplace/cart/checkout-options` returns.
 *
 * The names below are the display half of what the *buyer* sends: the server
 * receives the ISO-3166-1 alpha-2 code, not the name. That is the whole of the
 * buyer's contract and none of the supplier's. CJ's create-order takes
 * `shippingCountryCode` *and* `shippingCountry`, and the latter is a name — so
 * the server keeps its own copy of this table in
 * `services/marketplace_fulfillment._COUNTRY_NAMES`, and
 * `test_country_names_match_the_picker` pins the two together. Adding a country
 * here alone is a checkout that completes into an order no supplier can fill.
 *
 * The card verdict is here for the same reason as the countries: it is
 * deployment configuration. The screen used to hold its own
 * `MARKETPLACE_CARD_PAYMENTS_PAUSED = true`, which was harmless only while the
 * server's pause was also a hard-coded `true`. Now that the server reads
 * `MARKETPLACE_CARD_PAYMENTS_ENABLED`, a second copy in a shipped binary is a
 * copy that cannot be corrected without an App Store release.
 */

import { pulseApi } from "./pulseApi";

/** ISO-3166-1 alpha-2 → display name, for every code the server might allow. */
const COUNTRY_NAMES: Record<string, string> = {
  AE: "United Arab Emirates", AR: "Argentina", AT: "Austria", AU: "Australia",
  BE: "Belgium", BG: "Bulgaria", BR: "Brazil", CA: "Canada", CH: "Switzerland",
  CL: "Chile", CN: "China", CO: "Colombia", CY: "Cyprus", CZ: "Czechia",
  DE: "Germany", DK: "Denmark", EE: "Estonia", EG: "Egypt", ES: "Spain",
  FI: "Finland", FR: "France", GB: "United Kingdom", GH: "Ghana", GR: "Greece",
  HK: "Hong Kong SAR China", HR: "Croatia", HU: "Hungary", ID: "Indonesia",
  IE: "Ireland", IL: "Israel", IN: "India", IS: "Iceland", IT: "Italy",
  JP: "Japan", KE: "Kenya", KR: "South Korea", LT: "Lithuania",
  LU: "Luxembourg", LV: "Latvia", MA: "Morocco", MT: "Malta", MX: "Mexico",
  MY: "Malaysia", NG: "Nigeria", NL: "Netherlands", NO: "Norway",
  NZ: "New Zealand", PE: "Peru", PH: "Philippines", PL: "Poland",
  PT: "Portugal", RO: "Romania", SA: "Saudi Arabia", SE: "Sweden",
  SG: "Singapore", SI: "Slovenia", SK: "Slovakia", TH: "Thailand",
  TR: "Türkiye", TW: "Taiwan", UA: "Ukraine", US: "United States",
  VN: "Vietnam", ZA: "South Africa"
};

export type CheckoutCountry = { code: string; name: string };

/** The list every caller falls back to. Matches the server's own default, so a
 * client that cannot reach the options endpoint still offers exactly what an
 * unconfigured deployment accepts — never a wider list that would fail later. */
export const DEFAULT_SHIPPING_COUNTRIES: readonly string[] = ["US"];

export function countryName(code: string): string {
  const key = String(code || "").trim().toUpperCase();
  return COUNTRY_NAMES[key] || key;
}

/** Codes → sorted, display-ready options. A code with no name maps to itself
 * rather than being dropped: an unrecognised country the server *does* accept
 * must still be selectable. */
export function toCountryOptions(codes: readonly string[]): CheckoutCountry[] {
  const seen = new Set<string>();
  const out: CheckoutCountry[] = [];
  for (const raw of codes) {
    const code = String(raw || "").trim().toUpperCase();
    if (code.length !== 2 || seen.has(code)) continue;
    seen.add(code);
    out.push({ code, name: countryName(code) });
  }
  return out.sort((a, b) => a.name.localeCompare(b.name));
}

export type CheckoutOptions = {
  countries: CheckoutCountry[];
  cardPaymentsAvailable: boolean;
  cardBadge: string;
  cardUnavailableMessage: string;
};

/** What the checkout assumes when the server could not be asked.
 *
 * The two halves fail in opposite directions on purpose. The country list fails
 * *soft* to the configured default, because an empty picker blocks an order the
 * deployment would have accepted. The card rail fails *closed*, because the
 * cost of guessing wrong is a buyer sent into a card checkout the server is
 * about to refuse — and cash, which is the lane that actually works, stays open
 * either way.
 */
export const CHECKOUT_OPTIONS_FALLBACK: CheckoutOptions = {
  countries: toCountryOptions(DEFAULT_SHIPPING_COUNTRIES),
  cardPaymentsAvailable: false,
  cardBadge: "Temporarily Unavailable",
  cardUnavailableMessage:
    "Marketplace card payments are temporarily unavailable. Choose cash, local pickup, or in-person payment."
};

/** Ask the server what this checkout may offer. Never throws. */
export async function fetchCheckoutOptions(): Promise<CheckoutOptions> {
  try {
    const data = (await pulseApi("/api/pulse/marketplace/cart/checkout-options")) as {
      shipping_countries?: string[];
      card_payments_available?: boolean;
      payment_badge?: string;
      payment_unavailable_message?: string;
    };
    const codes = Array.isArray(data.shipping_countries) ? data.shipping_countries : [];
    const options = toCountryOptions(codes);
    // `=== true` rather than a truthiness test: a response that omits the field
    // is an older server, or one that answered something else entirely, and
    // neither of those said yes.
    const available = data.card_payments_available === true;
    return {
      countries: options.length ? options : CHECKOUT_OPTIONS_FALLBACK.countries,
      cardPaymentsAvailable: available,
      cardBadge: data.payment_badge || CHECKOUT_OPTIONS_FALLBACK.cardBadge,
      cardUnavailableMessage:
        data.payment_unavailable_message || CHECKOUT_OPTIONS_FALLBACK.cardUnavailableMessage
    };
  } catch {
    return CHECKOUT_OPTIONS_FALLBACK;
  }
}

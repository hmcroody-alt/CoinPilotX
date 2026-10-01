/**
 * Delivery countries for the checkout's country picker.
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
 * The names below are only the display half. The server never sees them; it
 * sees the ISO-3166-1 alpha-2 code, which is the contract.
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

/**
 * Countries this deployment will deliver to.
 *
 * Fails soft to the default: a checkout that cannot read its options should
 * still be completable by the US buyers who are the configured default, rather
 * than presenting an empty picker and blocking the order entirely.
 */
export async function fetchShippingCountries(): Promise<CheckoutCountry[]> {
  try {
    const data = (await pulseApi("/api/pulse/marketplace/cart/checkout-options")) as {
      shipping_countries?: string[];
    };
    const codes = Array.isArray(data.shipping_countries) ? data.shipping_countries : [];
    const options = toCountryOptions(codes);
    return options.length ? options : toCountryOptions(DEFAULT_SHIPPING_COUNTRIES);
  } catch {
    return toCountryOptions(DEFAULT_SHIPPING_COUNTRIES);
  }
}

/**
 * What the buyer is asked before paying, decided per order type.
 *
 * A port of `services/marketplace_fulfillment.py` — the same kind resolution and
 * the same field list — so the form rendered here is the form the server will
 * accept. The server re-derives both from the stored listing row and validates
 * again; this copy exists so the buyer sees the right questions immediately
 * rather than discovering them as a rejection.
 *
 * `tests/test_marketplace_fulfillment.py` and this module's jest test pin the
 * two together: a kind added on one side without the other fails there.
 */
import type { MarketplaceListing } from "./marketplace";

export type MarketplaceFulfillmentKind =
  | "shipping"
  | "pickup"
  | "shipping_or_pickup"
  | "digital"
  | "service_remote"
  | "service_in_person"
  | "service_choice"
  | "event_online"
  | "event_in_person"
  | "booking_remote"
  | "booking_in_person";

export const UNDECIDED_KINDS: MarketplaceFulfillmentKind[] = ["shipping_or_pickup", "service_choice"];

export type FulfillmentFieldType =
  | "name"
  | "phone"
  | "text"
  | "multiline"
  | "country"
  | "date"
  | "time"
  | "timezone"
  | "choice";

export type FulfillmentField = {
  key: string;
  type: FulfillmentFieldType;
  required: boolean;
  label: string;
  options?: string[];
};

/**
 * Every spelling of a delivery lane that has ever been stored, folded onto the
 * four this module reasons about. Mirrors `_LANE_WORDS` on the server.
 */
const LANE_WORDS: Record<string, "pickup" | "shipping" | "both" | "digital"> = {
  pickup: "pickup",
  local: "pickup",
  meetup: "pickup",
  shipping: "shipping",
  delivery: "shipping",
  both: "both",
  pickup_or_shipping: "both",
  shipping_or_pickup: "both",
  digital: "digital",
  download: "digital"
};

/**
 * The delivery lane the seller declared, or `""` if they declared none.
 *
 * `delivery_type` does not hold one. Every backend writer of that column stores
 * the *product type* in it — the publish route's INSERT lists
 * `delivery_type, product_type` against `product_type, product_type`, the CJ
 * importer hardcodes `'physical','physical'`, and the column's own default is
 * `'digital'` — so it reads `physical` for every physical listing in the table
 * and it can never read `pickup`, `shipping` or `both`.
 *
 * The seller's actual answer is `listing_metadata.delivery_options`, validated
 * server-side against exactly those three words, and it is read first here. The
 * column is consulted only for a row that declared no listing type at all,
 * where it is the only signal there is.
 *
 * Reading the column first is what made this module answer `shipping` for every
 * physical listing — one tap after the product screen printed "Local pickup"
 * from the metadata, and while the same screen's grid card offered no buy
 * button at all because `physical` matched none of its lane words either.
 */
export function deliveryLane(listing: MarketplaceListing): "" | "pickup" | "shipping" | "both" | "digital" {
  const metadata = (listing.listing_metadata || {}) as Record<string, unknown>;
  const declared = LANE_WORDS[String(metadata.delivery_options || "").trim().toLowerCase()];
  if (declared) return declared;
  if (String(listing.listing_type || listing.product_type || "").trim()) return "";
  return LANE_WORDS[String(listing.delivery_type || "").trim().toLowerCase()] || "";
}

export function resolveFulfillmentKind(listing: MarketplaceListing): MarketplaceFulfillmentKind {
  const metadata = (listing.listing_metadata || {}) as Record<string, unknown>;
  const kind = String(listing.listing_type || listing.product_type || "").trim().toLowerCase();

  if (kind === "digital") return "digital";
  if (kind === "service") {
    const location = String(metadata.service_location || "").trim().toLowerCase();
    if (location === "both") return "service_choice";
    return location === "in_person" ? "service_in_person" : "service_remote";
  }
  if (kind === "event") {
    return String(metadata.venue_mode || "").trim().toLowerCase() === "in_person"
      ? "event_in_person"
      : "event_online";
  }
  if (kind === "booking") {
    return String(metadata.meeting_mode || "").trim().toLowerCase() === "in_person"
      ? "booking_in_person"
      : "booking_remote";
  }

  // Physical, and anything legacy that never declared a type.
  const lane = deliveryLane(listing);
  if (lane === "digital") return "digital";
  if (lane === "both") return "shipping_or_pickup";
  if (lane === "pickup") return "pickup";
  return "shipping";
}

/** Settle an undecided kind with the lane the buyer picked, or return it unchanged. */
export function resolveFulfillmentChoice(
  kind: MarketplaceFulfillmentKind,
  chosen: string
): MarketplaceFulfillmentKind | null {
  if (!UNDECIDED_KINDS.includes(kind)) return kind;
  const answer = String(chosen || "").trim().toLowerCase();
  if (kind === "shipping_or_pickup") {
    return answer === "shipping" || answer === "pickup" ? (answer as MarketplaceFulfillmentKind) : null;
  }
  if (answer === "in_person" || answer === "service_in_person") return "service_in_person";
  if (answer === "remote" || answer === "service_remote") return "service_remote";
  return null;
}

export function fulfillmentNeedsAddress(kind: MarketplaceFulfillmentKind) {
  return kind === "shipping" || kind === "service_in_person" || kind === "booking_in_person";
}

const LABELS: Record<string, string> = {
  contact_name: "Full name",
  contact_phone: "Phone number",
  attendee_name: "Attendee name",
  address_line1: "Street address",
  address_line2: "Apartment or unit",
  address_city: "City",
  address_region: "State or province",
  address_postal_code: "Postal code",
  address_country: "Country",
  scheduled_date: "Date",
  scheduled_time: "Time",
  timezone: "Timezone",
  ticket_type: "Ticket type",
  notes: "Notes",
  delivery_notes: "Delivery notes",
  pickup_preference: "Pickup preference",
};

type Triple = [string, FulfillmentFieldType, boolean];

const CONTACT: Triple[] = [
  ["contact_name", "name", true],
  ["contact_phone", "phone", false],
];
const CONTACT_WITH_PHONE: Triple[] = [
  ["contact_name", "name", true],
  ["contact_phone", "phone", true],
];
const ADDRESS: Triple[] = [
  ["address_line1", "text", true],
  ["address_line2", "text", false],
  ["address_city", "text", true],
  ["address_region", "text", false],
  ["address_postal_code", "text", false],
  ["address_country", "country", true],
];
const WHEN: Triple[] = [
  ["scheduled_date", "date", true],
  ["scheduled_time", "time", true],
  ["timezone", "timezone", true],
];
const NOTES: Triple[] = [["notes", "multiline", false]];

const FIELDS: Record<MarketplaceFulfillmentKind, Triple[]> = {
  digital: [],
  shipping: [...CONTACT, ...ADDRESS, ["delivery_notes", "multiline", false]],
  pickup: [...CONTACT_WITH_PHONE, ["pickup_preference", "text", false]],
  shipping_or_pickup: [],
  service_choice: [],
  service_remote: [...CONTACT, ...WHEN, ...NOTES],
  service_in_person: [...CONTACT_WITH_PHONE, ...WHEN, ...ADDRESS, ...NOTES],
  event_online: [
    ["attendee_name", "name", true],
    ["ticket_type", "choice", true],
  ],
  event_in_person: [
    ["attendee_name", "name", true],
    ["contact_phone", "phone", false],
    ["ticket_type", "choice", true],
  ],
  booking_remote: [...CONTACT, ...WHEN, ...NOTES],
  booking_in_person: [...CONTACT_WITH_PHONE, ...WHEN, ...ADDRESS, ...NOTES],
};

/**
 * Fields the client answers from the device instead of asking the buyer.
 *
 * Only `timezone`. It is still required, still sent, and still validated
 * server-side — nothing about the contract changes. What changes is who
 * supplies it: `Intl` reports the device zone in the exact IANA form the server
 * accepts, so asking the buyer to type `America/New_York` could only ever make
 * the answer worse. The checkout renders it as context under the chosen time
 * rather than as an input.
 *
 * Kept as a list rather than a boolean on the field because the *reason* is
 * per-field. A key belongs here only when the device's answer is strictly
 * better than a typed one, which is a much narrower claim than "we could guess
 * this" — a shipping address could be guessed too, and must not be.
 */
export const AUTO_FILLED_FIELD_KEYS: readonly string[] = ["timezone"];

export function isAutoFilledField(key: string) {
  return AUTO_FILLED_FIELD_KEYS.includes(key);
}

/** The listing-type pill on the checkout's product summary — what the buyer is
 * buying, which is also why the form below asks what it asks. */
export function fulfillmentTypeLabel(kind: MarketplaceFulfillmentKind): string {
  if (kind === "digital") return "Digital product";
  if (kind.startsWith("event_")) return "Event";
  if (kind.startsWith("booking_")) return "Appointment";
  if (kind.startsWith("service_")) return "Service";
  if (kind === "pickup") return "Local pickup";
  return "Physical item";
}

export function ticketOptions(listing: MarketplaceListing): string[] {
  const metadata = (listing.listing_metadata || {}) as Record<string, unknown>;
  const tickets = metadata.tickets;
  if (!Array.isArray(tickets)) return [];
  return tickets
    .map((ticket) => String((ticket as Record<string, unknown>)?.name || "").trim())
    .filter(Boolean);
}

export function fulfillmentFields(
  kind: MarketplaceFulfillmentKind,
  tickets: readonly string[] = []
): FulfillmentField[] {
  const out: FulfillmentField[] = [];
  for (const [key, type, required] of FIELDS[kind] || []) {
    if (key === "ticket_type") {
      // A seller who published no ticket tiers is selling one undifferentiated
      // admission, so there is nothing to choose between.
      if (!tickets.length) continue;
      out.push({ key, type: "choice", required: true, label: LABELS[key], options: [...tickets] });
      continue;
    }
    out.push({ key, type, required, label: LABELS[key] });
  }
  return out;
}

/** Countries where a street and city alone will not get a parcel delivered. */
const REGION_REQUIRED = ["US", "CA", "AU", "IN", "BR", "MX", "MY", "CN", "AR", "ID"];

/**
 * Countries whose addresses are complete without a postal code. Demanding one
 * universally is how a checkout built in the US rejects a valid address in
 * Ireland or Hong Kong.
 */
const NO_POSTAL_CODE = [
  "AE", "AO", "AG", "AW", "BS", "BZ", "BJ", "BW", "BF", "BI", "CM", "CF", "KM", "CG", "CD", "CK",
  "CI", "DJ", "DM", "GQ", "ER", "FJ", "TF", "GM", "GH", "GD", "GY", "HK", "IE", "JM", "KE", "KI",
  "KP", "LY", "MO", "MW", "ML", "MR", "MU", "MS", "NR", "AN", "NU", "QA", "RW", "KN", "LC", "ST",
  "SC", "SL", "SB", "SO", "SR", "SY", "TZ", "TL", "TK", "TO", "TT", "TV", "UG", "VU", "YE", "ZW",
];

/**
 * The first field the buyer still has to fill, or null when the form is
 * complete. Only enough validation to keep the Continue button honest — the
 * server's answer is the one that decides whether a charge happens.
 */
export function firstMissingFulfillmentField(
  kind: MarketplaceFulfillmentKind,
  tickets: readonly string[],
  values: Record<string, string>
): FulfillmentField | null {
  const fields = fulfillmentFields(kind, tickets);
  for (const field of fields) {
    if (field.required && !String(values[field.key] || "").trim()) return field;
  }
  if (!fulfillmentNeedsAddress(kind)) return null;
  const country = String(values.address_country || "").trim().toUpperCase();
  const byKey = (key: string) => fields.find((field) => field.key === key) || null;
  if (REGION_REQUIRED.includes(country) && !String(values.address_region || "").trim()) {
    return byKey("address_region");
  }
  if (!NO_POSTAL_CODE.includes(country) && !String(values.address_postal_code || "").trim()) {
    return byKey("address_postal_code");
  }
  return null;
}

/**
 * The one kind a whole cart group is asked about. A group settles through a
 * single Stripe session, so it asks a single set of questions — the address if
 * anything in it ships, otherwise the scheduling or pickup answer, otherwise
 * nothing. Mirrors the selection in `marketplace_cart_routes.cart_checkout`.
 */
export function groupFulfillmentKind(
  kinds: readonly MarketplaceFulfillmentKind[]
): MarketplaceFulfillmentKind | "" {
  return (
    kinds.find(fulfillmentNeedsAddress) ||
    kinds.find(isScheduledKind) ||
    kinds.find((kind) => kind === "pickup") ||
    ""
  );
}

/** Whether this kind happens at a time the buyer has to name. */
export function isScheduledKind(kind: MarketplaceFulfillmentKind) {
  return kind.startsWith("service_") || kind.startsWith("booking_") || kind.startsWith("event_");
}

/**
 * Whether buyer and seller end up in the same room.
 *
 * This is the question the escrow/safety presentation is actually about — it is
 * advice about meeting a stranger — and it is NOT the same question as "which
 * progress strip does this order get". `ordersDashboard` used to answer both
 * with one set, which meant an in-person haircut could only get the safety panel
 * by also being described to the buyer as a parcel awaiting collection.
 */
export function isInPersonKind(kind: MarketplaceFulfillmentKind) {
  return (
    kind === "pickup" ||
    kind === "service_in_person" ||
    kind === "booking_in_person" ||
    kind === "event_in_person"
  );
}

/** One-line destination for the review step: where this order is actually going. */
export function fulfillmentDestinationSummary(
  kind: MarketplaceFulfillmentKind,
  values: Record<string, string>
): string {
  if (kind === "digital") return "Delivered to your PulseSoc account";
  if (fulfillmentNeedsAddress(kind)) {
    return [values.address_line1, values.address_line2, values.address_city, values.address_region, values.address_postal_code, values.address_country]
      .map((part) => String(part || "").trim())
      .filter(Boolean)
      .join(", ");
  }
  if (kind === "pickup") return "Collected in person from the seller";
  if (kind === "event_online") return "Joined online";
  if (kind === "event_in_person") return "Attended at the venue";
  const when = [values.scheduled_date, values.scheduled_time, values.timezone]
    .map((part) => String(part || "").trim())
    .filter(Boolean)
    .join(" ");
  return when ? `Online, ${when}` : "Online";
}

/**
 * `POST /api/pulse/delivery/estimate` — when the parcel should arrive, asked
 * before the buyer commits to anything.
 *
 * ## Why this module computes nothing
 *
 * Every number in a delivery promise comes from the server: the supplier's
 * quoted transit range, the deployment's declared handling time and buffer, the
 * origin warehouse's own working calendar, the route chosen for this parcel.
 * None of those are facts a shipped binary can hold. A build that added days to
 * a server date, or filled a gap with a plausible default, would be a *second*
 * promise for one parcel — and the two would disagree the day an operator
 * changed a variable, with the App Store release cycle standing between the
 * correction and the buyer.
 *
 * So this module is a parser, not an estimator. It turns one JSON body into a
 * typed value, refuses the shapes it cannot render honestly, and never invents a
 * date.
 *
 * ## Why a failure is not an estimate
 *
 * The interesting cases here are all failures: the server is unreachable, the
 * supplier is down, the deployment has not declared a handling time yet, the
 * listing does not ship to this country. Each of those has to come out as
 * `UNAVAILABLE` with a reason — never as a date, and never as a blank space that
 * the screen fills with old copy. `estimateUnavailable` is the only thing this
 * module produces when it does not have an answer, and it carries why.
 *
 * `reason` values that originate here rather than at the server are prefixed
 * `client_`, so a support query about `handling_time_undeclared` reaches the
 * operator who has to set a variable while `client_unreachable` reaches nobody
 * and goes away on its own.
 *
 * ## Why the request carries so little
 *
 * `variant_ref`, `quantity`, `country`. Not a postal code, not a device
 * location, not who ships it. The server resolves the destination from the
 * session it can see and reads the fulfillment type off the listing itself — a
 * body that could name either would let a caller pick a cheaper corridor or ask
 * for a supplier quote on a listing the supplier does not fulfill. The endpoint
 * refuses those fields; this module never sends them.
 *
 * ## Why the dates stay strings
 *
 * The server sends `YYYY-MM-DD` — a calendar day at the destination, with no
 * time and no zone, because "arrives Tuesday" is not a moment. Handing that to
 * `new Date("2026-03-16")` parses it as UTC midnight, which is 16 March in
 * Shenzhen and 15 March in Los Angeles: a buyer west of UTC would read a day
 * earlier than the one the server computed. So the ISO string is what this
 * module stores and `formatDeliveryDate` splits it by hand rather than going
 * through `Date` at all.
 */

import { pulseApi } from "./pulseApi";

/** Mirrors `services/delivery/estimate.py`'s state vocabulary. */
export type DeliveryState = "ESTIMATED" | "UNAVAILABLE" | "UNSUPPORTED_ROUTE";

/** Mirrors `CONFIDENCE_*`. A cached provider quote is still a provider quote,
 * one refresh behind — worth showing, worth saying it is an estimate. */
export type DeliveryConfidence = "PROVIDER_QUOTED" | "PROVIDER_CACHED" | "NONE";

/**
 * The buyer half of one quote, and nothing else.
 *
 * The server splits its answer in two and sends only this half to an
 * unauthenticated surface: no freight cost, no supplier name, no warehouse, no
 * chosen route. This type is the client's copy of that boundary — if a field
 * appears in the response that is not declared here, it is not carried into the
 * app, and the screen cannot accidentally render the platform's economics.
 */
export type DeliveryEstimate = {
  state: DeliveryState;
  /** Why, when there is no window. `null` only when `state` is `ESTIMATED`. */
  reason: string | null;
  /** `YYYY-MM-DD` at the destination, or `null`. Both present, or neither. */
  earliest: string | null;
  latest: string | null;
  confidence: DeliveryConfidence;
  /**
   * Always `false`. Kept as a field rather than dropped so that a future server
   * that starts guaranteeing a date has somewhere to say so — and read with
   * `=== true` so that until one does, no response shape can flip it. §58 and
   * §125 forbid the word "Guaranteed" over an estimate, and this is the flag a
   * copy layer would have to consult to earn it.
   */
  guaranteed: boolean;
  /** Always `true` while `guaranteed` is `false`. The two together are what
   * licenses the word "Estimated" in front of the window. */
  isEstimate: boolean;
  /**
   * `"FREE"` from the server. A string and not a number, because a surface that
   * receives `0` eventually formats it as `$0.00` beside a total. `null` when
   * the server did not say — which is not the same as free, and must not be
   * rendered as it.
   */
  shippingPrice: string | null;
};

/** Where the server decided to quote to, and how sure it is of that.
 *
 * `precision` and `tier` are here so a screen can say "to United States" rather
 * than implying a doorstep it was never told about. `known: false` means the
 * server had nothing to go on — the estimate will be `UNAVAILABLE` and the
 * honest prompt is "choose a country", not a retry.
 */
export type DeliveryDestination = {
  country: string | null;
  precision: string | null;
  tier: string | null;
  known: boolean;
};

export type DeliveryAnswer = {
  delivery: DeliveryEstimate;
  destination: DeliveryDestination;
};

/** Reasons this module produces itself. Distinguishable from the server's by
 * the prefix, because they route to different people. */
export const CLIENT_UNREACHABLE = "client_unreachable";
export const CLIENT_LISTING_UNAVAILABLE = "client_listing_unavailable";
export const CLIENT_MALFORMED = "client_malformed_response";
export const CLIENT_INCOMPLETE = "client_incomplete_window";

const UNKNOWN_DESTINATION: DeliveryDestination = {
  country: null,
  precision: null,
  tier: null,
  known: false
};

/** The only estimate this module ever builds from nothing. Carries a reason and
 * no dates, so every caller that renders it is forced through the same branch as
 * a server-side refusal. */
export function estimateUnavailable(reason: string): DeliveryEstimate {
  return {
    state: "UNAVAILABLE",
    reason,
    earliest: null,
    latest: null,
    confidence: "NONE",
    guaranteed: false,
    isEstimate: true,
    shippingPrice: null
  };
}

function answerUnavailable(reason: string, destination?: DeliveryDestination): DeliveryAnswer {
  return {
    delivery: estimateUnavailable(reason),
    destination: destination || UNKNOWN_DESTINATION
  };
}

/** `YYYY-MM-DD`, exactly. Not a loose parse: a string this module cannot be sure
 * about is a string it will not put in front of a buyer. */
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

function isoDay(raw: unknown): string | null {
  return typeof raw === "string" && ISO_DAY.test(raw) ? raw : null;
}

function text(raw: unknown): string | null {
  return typeof raw === "string" && raw.trim() ? raw : null;
}

const STATES: readonly DeliveryState[] = ["ESTIMATED", "UNAVAILABLE", "UNSUPPORTED_ROUTE"];
const CONFIDENCES: readonly DeliveryConfidence[] = ["PROVIDER_QUOTED", "PROVIDER_CACHED", "NONE"];

/**
 * One JSON body → one estimate. Never throws.
 *
 * Unknown values fail closed rather than passing through. A `state` this build
 * has never heard of comes out `UNAVAILABLE`: a newer server that adds a state
 * means older binaries are in the field, and an unrecognised state rendered as
 * if it were `ESTIMATED` is a window drawn around two nulls.
 *
 * The strictest rule is the last one. A response claiming `ESTIMATED` without
 * both dates is a malformed answer, not a partial one — half a window is not
 * "arrives from the 16th", it is a bug, and it is reported as
 * `client_incomplete_window` instead of being drawn.
 */
export function parseDeliveryEstimate(raw: unknown): DeliveryEstimate {
  if (!raw || typeof raw !== "object") return estimateUnavailable(CLIENT_MALFORMED);
  const body = raw as Record<string, unknown>;

  const state = STATES.find((known) => known === body.state);
  if (!state) return estimateUnavailable(CLIENT_MALFORMED);

  const confidence = CONFIDENCES.find((known) => known === body.confidence) || "NONE";
  const earliest = isoDay(body.earliest);
  const latest = isoDay(body.latest);

  if (state === "ESTIMATED" && !(earliest && latest)) {
    return estimateUnavailable(CLIENT_INCOMPLETE);
  }

  return {
    state,
    // A reason is required for everything that is not a window, and meaningless
    // beside one. Normalizing it here means a screen never has to ask whether an
    // empty string counts.
    reason: state === "ESTIMATED" ? null : text(body.reason) || CLIENT_MALFORMED,
    earliest: state === "ESTIMATED" ? earliest : null,
    latest: state === "ESTIMATED" ? latest : null,
    confidence: state === "ESTIMATED" ? confidence : "NONE",
    guaranteed: body.guaranteed === true,
    // `!== false` and not `=== true`: an older server that omits the field is
    // still sending an estimate, and the failure direction that matters is never
    // calling an estimate a certainty.
    isEstimate: body.is_estimate !== false,
    shippingPrice: text(body.shipping_price)
  };
}

function parseDestination(raw: unknown): DeliveryDestination {
  if (!raw || typeof raw !== "object") return UNKNOWN_DESTINATION;
  const body = raw as Record<string, unknown>;
  return {
    country: text(body.country),
    precision: text(body.precision),
    tier: text(body.tier),
    known: body.known === true
  };
}

export type DeliveryRequest = {
  /** The server's own variant reference. Opaque here: this module does not parse
   * it, and a malformed one comes back as a refusal rather than a crash. */
  variantRef: string;
  /** Defaults to 1. Freight is quoted for a parcel, and a parcel of three is a
   * different parcel — but the ceiling belongs to the cart and is enforced by
   * the server, so an out-of-range value is sent and refused rather than
   * silently clamped into a quote for goods nobody asked about. */
  quantity?: number;
  /** ISO-3166-1 alpha-2, when the buyer has said. Omitted otherwise: the server
   * resolves a destination from the session it can see, and a guessed country is
   * a confident date for the wrong continent. */
  country?: string;
};

/**
 * Ask the server for one estimate. Never throws; never returns a date it was not
 * given.
 *
 * Every non-answer is a `DeliveryAnswer` in the `UNAVAILABLE` state, which is
 * why there is no `null` in the return type: a caller cannot forget to handle
 * the failure, because the failure arrives in the same shape as the success and
 * carries a reason the screen can say out loud.
 *
 * The timeout is `pulseApi`'s own. This request is on a product page's critical
 * path and its answer is a nice-to-have relative to the price and the photos, so
 * it is left to the shared read deadline rather than given a longer one of its
 * own — a delivery line that is still spinning after fifteen seconds has already
 * failed at its job.
 */
export async function fetchDeliveryEstimate(request: DeliveryRequest): Promise<DeliveryAnswer> {
  const ref = String(request.variantRef || "").trim();
  if (!ref) return answerUnavailable(CLIENT_MALFORMED);

  const payload: Record<string, unknown> = { variant_ref: ref };
  if (typeof request.quantity === "number" && Number.isInteger(request.quantity)) {
    payload.quantity = request.quantity;
  }
  const country = String(request.country || "").trim().toUpperCase();
  if (country.length === 2) payload.country = country;

  let data: Record<string, unknown>;
  try {
    data = (await pulseApi("/api/pulse/delivery/estimate", {
      method: "POST",
      body: JSON.stringify(payload)
    })) as Record<string, unknown>;
  } catch {
    // Deliberately one reason for every transport failure. The screen's job is
    // the same whether this was a timeout, a 404 on a delisted listing or a
    // 500 — say that the date is not available and do not block the purchase.
    return answerUnavailable(CLIENT_UNREACHABLE);
  }

  if (!data || typeof data !== "object") return answerUnavailable(CLIENT_MALFORMED);
  const destination = parseDestination(data.destination);
  if (data.ok !== true) {
    // The server said no at the boundary — an invisible listing, a bad
    // reference, a malformed body. `reason` is its own vocabulary and is carried
    // through unchanged so that a support query lands on the right desk.
    return answerUnavailable(text(data.reason) || CLIENT_LISTING_UNAVAILABLE, destination);
  }
  return { delivery: parseDeliveryEstimate(data.delivery), destination };
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
];

/**
 * `"2026-03-16"` → `"16 Mar"`. Returns `null` for anything else.
 *
 * Does not go through `Date`. The server's day is already a calendar day at the
 * destination, and `new Date("2026-03-16")` is UTC midnight — so a buyer in
 * California would be shown 15 March for a parcel the server said arrives on the
 * 16th, which is precisely the off-by-one this whole package exists to avoid.
 * Splitting the string keeps the day the server chose.
 */
export function formatDeliveryDate(iso: string | null): string | null {
  if (!isoDay(iso)) return null;
  const [, month, day] = (iso as string).split("-");
  const name = MONTHS[Number(month) - 1];
  if (!name) return null;
  return `${Number(day)} ${name}`;
}

/**
 * The window as one phrase: `"16 – 21 Mar"`, or `"28 Mar – 2 Apr"` across a
 * month boundary. `null` when there is no window, so a caller that renders the
 * return value cannot print half of one.
 *
 * A single day is returned as a single day rather than a range repeated twice.
 * That is the one place this module gets *less* precise than the data, and on
 * purpose: `"16 – 16 Mar"` reads as a formatting bug and invites the reader to
 * distrust the rest.
 */
export function formatDeliveryWindow(estimate: DeliveryEstimate): string | null {
  if (estimate.state !== "ESTIMATED") return null;
  const from = formatDeliveryDate(estimate.earliest);
  const to = formatDeliveryDate(estimate.latest);
  if (!from || !to) return null;
  if (from === to) return from;
  const fromMonth = (estimate.earliest as string).slice(0, 7);
  const toMonth = (estimate.latest as string).slice(0, 7);
  if (fromMonth === toMonth) {
    // Same month: say it once. "16 – 21 Mar", not "16 Mar – 21 Mar".
    return `${from.split(" ")[0]} – ${to}`;
  }
  return `${from} – ${to}`;
}

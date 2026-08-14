/**
 * The funnel is instrumented; the secrets are not.
 *
 * Two properties are worth locking down. The eleven event names the brief lists
 * are a contract with whatever transport gets attached later, so a rename is a
 * silent data loss rather than a compile error unless something asserts them.
 * And a purchase screen must survive a broken sink — losing a metric is
 * recoverable, losing the member's ability to subscribe is not.
 */

import {
  setPremiumAnalyticsSink,
  trackPremium,
  type PremiumAnalyticsEvent
} from "../premiumAnalytics";

const REQUIRED_EVENTS: PremiumAnalyticsEvent[] = [
  "premium_tile_impression",
  "premium_tile_opened",
  "premium_product_fetch_started",
  "premium_product_fetch_success",
  "premium_product_fetch_empty",
  "premium_product_fetch_failed",
  "premium_product_missing_monthly",
  "premium_product_missing_annual",
  "premium_plan_viewed",
  "premium_monthly_selected",
  "premium_annual_selected",
  "premium_purchase_started",
  "premium_purchase_cancelled",
  "premium_purchase_verified",
  "premium_restore_started",
  "premium_restore_completed",
  "premium_manage_opened"
];

afterEach(() => setPremiumAnalyticsSink(null));

it("emits every event in the funnel under its agreed name", () => {
  const seen: string[] = [];
  setPremiumAnalyticsSink((event) => seen.push(event));
  for (const event of REQUIRED_EVENTS) trackPremium(event);
  expect(seen).toEqual(REQUIRED_EVENTS);
});

it("drops events on the floor when no transport is attached", () => {
  // The seam's resting state. Nothing is buffered, so nothing accumulates in
  // memory on a device that never gets a sink.
  expect(() => trackPremium("premium_tile_impression")).not.toThrow();
});

it("does not let a broken sink fail the flow it observes", () => {
  setPremiumAnalyticsSink(() => { throw new Error("transport down"); });
  expect(() => trackPremium("premium_purchase_started", { plan: "annual" })).not.toThrow();
});

it("passes only non-identifying scalars through", () => {
  const props: Array<Record<string, unknown> | undefined> = [];
  setPremiumAnalyticsSink((_event, payload) => props.push(payload));
  trackPremium("premium_purchase_verified", { plan: "annual", result: "verified" });
  trackPremium("premium_restore_completed", { result: "empty" });
  for (const payload of props) {
    for (const value of Object.values(payload || {})) {
      expect(["string", "number", "boolean"]).toContain(typeof value);
    }
  }
  // The union that carries these forbids objects, which is what stops a caller
  // spreading a purchase payload — receipt, JWS, transaction id — into an event.
  expect(props).toEqual([
    { plan: "annual", result: "verified" },
    { result: "empty" }
  ]);
});

/**
 * Premium funnel events.
 *
 * There is no product-analytics transport in `mobile-native`. The only
 * telemetry that exists is `core/perfTrace` and the realtime-audio counters,
 * both of which are diagnostic and both of which sit inside the protected audio
 * paths this mission may not touch.
 *
 * So this module is a *seam*, not a pipeline: the funnel is instrumented at
 * every real call site with a stable event name, and `setPremiumAnalyticsSink`
 * is where a transport gets attached when one exists. That is deliberately
 * preferable to the two alternatives — inventing an endpoint the backend does
 * not serve (events would vanish into a 404 while the code claimed to track
 * them), or leaving the funnel uninstrumented and retrofitting call sites later
 * under less careful conditions.
 *
 * What must never be passed
 * -------------------------
 * No receipt, no JWS, no transaction id, no Apple account token, no price the
 * member was charged tied to their identity. `PremiumAnalyticsProps` is
 * restricted to a small union of scalars for that reason: the type system
 * refuses the object shapes those secrets arrive in, so a future caller cannot
 * casually spread a purchase payload into an event.
 */

export type PremiumAnalyticsEvent =
  | "premium_tile_impression"
  | "premium_tile_opened"
  | "premium_status_load_started"
  | "premium_status_load_success"
  | "premium_status_load_failure"
  | "premium_status_state_free"
  | "premium_status_state_active"
  | "premium_product_fetch_failure"
  | "premium_plan_viewed"
  | "premium_monthly_selected"
  | "premium_annual_selected"
  | "premium_purchase_started"
  | "premium_purchase_cancelled"
  | "premium_purchase_verified"
  | "premium_restore_started"
  | "premium_restore_completed"
  | "premium_manage_opened";

/**
 * Event properties. Scalars only, and only ever non-identifying ones —
 * a plan name, an experience mode, a machine result code.
 */
export type PremiumAnalyticsProps = Record<string, string | number | boolean | null>;

export type PremiumAnalyticsSink = (
  event: PremiumAnalyticsEvent,
  props?: PremiumAnalyticsProps
) => void;

let sink: PremiumAnalyticsSink | null = null;

/** Attach a transport. Passing `null` detaches it (used by tests). */
export function setPremiumAnalyticsSink(next: PremiumAnalyticsSink | null): void {
  sink = next;
}

/**
 * Record a funnel event. Never throws.
 *
 * A broken analytics sink must not be able to take down a purchase screen —
 * losing a metric is recoverable, losing the member's ability to subscribe is
 * not.
 */
export function trackPremium(event: PremiumAnalyticsEvent, props?: PremiumAnalyticsProps): void {
  if (!sink) return;
  try {
    sink(event, props);
  } catch {
    // Instrumentation is never allowed to fail the flow it observes.
  }
}

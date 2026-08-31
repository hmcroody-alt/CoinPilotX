/**
 * Pulse Briefings hub events.
 *
 * Same seam pattern as `payments/premiumAnalytics`: there is no
 * product-analytics transport in `mobile-native`, so events are instrumented
 * at every real call site with a stable name and `setBriefingsAnalyticsSink`
 * is where a transport attaches when one exists.
 *
 * What must never be passed
 * -------------------------
 * No briefing body, no title, no fact values, no counts of the member's
 * unread messages, no watchlist symbols. A briefing is the owner's private
 * digest; analytics may know *that* one was opened, never what it said.
 * Props are restricted to scalars for that reason — a briefing id or a
 * preference name is fine, a payload spread is refused by the type.
 */

export type BriefingsAnalyticsEvent =
  | "briefings_tile_impression"
  | "briefings_hub_opened"
  | "briefings_history_page_loaded"
  | "briefing_opened"
  | "briefing_master_toggled"
  | "briefing_frequency_changed"
  | "briefing_topic_changed"
  | "briefing_quiet_hours_changed"
  | "briefings_marked_seen"
  | "briefings_load_failed";

/** Scalars only, and only non-identifying ones — an id, a frequency name. */
export type BriefingsAnalyticsProps = Record<string, string | number | boolean | null>;

export type BriefingsAnalyticsSink = (
  event: BriefingsAnalyticsEvent,
  props?: BriefingsAnalyticsProps
) => void;

let sink: BriefingsAnalyticsSink | null = null;

/** Attach a transport. Passing `null` detaches it (used by tests). */
export function setBriefingsAnalyticsSink(next: BriefingsAnalyticsSink | null): void {
  sink = next;
}

/** Record an event. Never throws — instrumentation must not fail the flow. */
export function trackBriefings(event: BriefingsAnalyticsEvent, props?: BriefingsAnalyticsProps): void {
  if (!sink) return;
  try {
    sink(event, props);
  } catch {
    // Losing a metric is recoverable; losing the hub is not.
  }
}

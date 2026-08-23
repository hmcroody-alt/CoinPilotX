/**
 * Discovery analytics — §14.
 *
 * ## Why this is a recorder and not a network client
 *
 * PulseSoc has no general-purpose client event ingest. The only impression
 * endpoints that exist are the advertising ones (`/api/pulse/ads/impression`,
 * `/api/pulse/ads/event`), and they are shaped around a campaign and a
 * creative — a people-card tap is not a billable ad event and must not be filed
 * as one. Inventing `/api/pulse/discovery/event` here would produce a client
 * that 404s on every call in production while passing every test, which is the
 * exact failure mode this codebase keeps finding.
 *
 * So this module owns the part that is real today: *what* an event is, and the
 * rule that each one is counted once. It emits to a sink. The default sink
 * drops events in production and logs them in development, so the instrumented
 * call sites are live, auditable, and switch on the day an ingest route exists —
 * `setDiscoveryAnalyticsSink` is the only change needed then.
 *
 * ## The invariant worth testing
 *
 * §14 requires no double-counting. An impression is not "the card rendered" —
 * FlatList mounts, unmounts and remounts a card every time it scrolls through
 * the recycling window, so a naive mount hook reports one carousel as a dozen
 * impressions. Identity here is `(event, module kind, slot, target)`, deduped
 * for the life of the feed session, and reset explicitly on refresh — because a
 * pull-to-refresh really is a new impression of whatever comes back.
 */
import type { DiscoveryModuleKind } from "./discoveryRows";

export type DiscoveryEventName =
  | "module_impression"
  | "card_impression"
  | "card_tap"
  | "see_all_tap"
  | "module_dismissed"
  /** One suggestion removed, as opposed to the whole row — they mean different things. */
  | "card_dismissed"
  | "friend_request_sent"
  | "follow_tap"
  | "group_join_tap"
  | "status_opened"
  | "reel_opened";

export type DiscoveryEvent = {
  name: DiscoveryEventName;
  /** Which module produced it. */
  kind: DiscoveryModuleKind;
  /** 0-based position of the row among discovery rows in this feed. */
  slot: number;
  /**
   * The exact destination this event is about, as a string: reel id, status id,
   * group slug, profile key. §14 requires the *specific* id, not just the kind,
   * because "someone tapped a person card" cannot answer whether the tap landed
   * on the person in the thumbnail.
   */
  target?: string;
  /** Index of the card inside its carousel, for position-in-row analysis. */
  position?: number;
  /** Why a module failed to load, for the recommendation-failure signal. */
  reason?: string;
};

export type DiscoveryAnalyticsSink = (event: DiscoveryEvent) => void;

/** Dev-visible, production-silent. Replaced wholesale once an ingest exists. */
const defaultSink: DiscoveryAnalyticsSink = (event) => {
  if (typeof __DEV__ !== "undefined" && __DEV__) {
    // eslint-disable-next-line no-console
    console.log("[discovery]", event.name, event.kind, event.slot, event.target ?? "");
  }
};

let sink: DiscoveryAnalyticsSink = defaultSink;

export function setDiscoveryAnalyticsSink(next: DiscoveryAnalyticsSink | null) {
  sink = next ?? defaultSink;
}

/**
 * Impressions already counted this feed session.
 *
 * Only impressions are deduped. A tap is a deliberate act and two taps on the
 * same card are two events; an impression is incidental and the same card
 * scrolling past twice is one.
 */
const countedImpressions = new Set<string>();

function impressionKey(event: DiscoveryEvent): string {
  return `${event.name}:${event.kind}:${event.slot}:${event.target ?? ""}`;
}

/** Call on pull-to-refresh: new rows are genuinely new impressions. */
export function resetDiscoveryImpressions() {
  countedImpressions.clear();
}

export function trackDiscoveryEvent(event: DiscoveryEvent) {
  if (event.name === "module_impression" || event.name === "card_impression") {
    const key = impressionKey(event);
    if (countedImpressions.has(key)) return;
    countedImpressions.add(key);
  }
  try {
    sink(event);
  } catch {
    // Analytics must never be able to break a feed render.
  }
}

/** Test-only: what the dedupe set currently holds. */
export function __countedDiscoveryImpressions(): string[] {
  return [...countedImpressions];
}

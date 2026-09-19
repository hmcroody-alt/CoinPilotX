/**
 * How an entitlement change reaches a phone that is already in someone's hand.
 *
 * Four of the five triggers are local acts the device performs itself: purchase
 * and restore re-read in `PremiumCenterScreen`, sign-in re-reads in
 * `session/auth.ts`, and a foreground re-reads in `PremiumFeatureGate`. An
 * ADMIN GRANT is none of those. It happens on a server the app is not talking
 * to, to a member who is looking at the screen — and if nothing carries it
 * across, the remedy for being granted Premium is to quit and relaunch. A
 * member who does not background the app never foregrounds it either, so for
 * them there is no remedy at all short of a restart they have no reason to
 * suspect they need.
 *
 * The delivery path is three links long: the server emits an event, the sync
 * poller maps it onto a subsystem, and something subscribes to that subsystem.
 * The first two were already built. The third was not, so the whole chain ran
 * and terminated in nothing — which is invisible from either end, because the
 * event is emitted correctly and the cache refreshes correctly, just never
 * because of each other.
 *
 * The routing link is checked behaviourally below. The subscription link is
 * checked against the source, for the reason `navigation/__tests__/
 * profileIdentityPropagation.test.ts` gives for doing the same: a navigator
 * that subscribes and one that does not render identically, and the difference
 * only appears in a session long enough for someone else to change something.
 */
import { readFileSync } from "fs";
import { join } from "path";

import { NATIVE_SYNC_SUBSYSTEMS, subsystemsForSyncEvent } from "../../core/eventSync";
import type { NativeSyncEvent } from "../../core/eventSync";

const navigatorSource = readFileSync(join(__dirname, "..", "..", "navigation", "AppNavigator.tsx"), "utf8");

/**
 * The premium subscription's own body, bounded at the effect that closes it.
 *
 * Bounded rather than a fixed character window on purpose: the window would
 * have to grow every time the reasoning above the call grows, and a window
 * sized to fit a comment is wide enough to start matching the next effect —
 * which is how a source assertion quietly becomes an assertion about a
 * different piece of code.
 */
function premiumSubscriptionBody(): string {
  const start = navigatorSource.indexOf('registerSyncInvalidation("premium"');
  expect(start).toBeGreaterThan(-1);
  const end = navigatorSource.indexOf("}, []);", start);
  expect(end).toBeGreaterThan(start);
  return navigatorSource.slice(start, end);
}

describe("the server's half: an entitlement event names the premium subsystem", () => {
  it("has a premium subsystem to route to at all", () => {
    expect(NATIVE_SYNC_SUBSYSTEMS).toContain("premium");
  });

  /**
   * The vocabulary an admin action can arrive under is not one word. A grant
   * may be recorded as a subscription change, an entitlement write, or a
   * founder membership, depending on which surface issued it — so the mapping
   * is checked across the family, not on a single happy-path string.
   */
  it.each([
    ["premium.granted", "the admin grant itself"],
    ["subscription.updated", "a subscription change"],
    ["entitlement.changed", "a canonical grant write"],
    ["founder.membership.created", "a founder membership"]
  ])("routes %s (%s) to premium", (eventType) => {
    const event: NativeSyncEvent = { event_type: eventType };
    expect(subsystemsForSyncEvent(event)).toContain("premium");
  });

  /**
   * An unrelated event must NOT wake the entitlement cache. Every wake is a
   * network request on someone's phone, and a mapping loose enough to catch a
   * new like would re-resolve entitlement on every interaction in the app.
   */
  it("does not route an unrelated event to premium", () => {
    expect(subsystemsForSyncEvent({ event_type: "post.liked" })).not.toContain("premium");
  });
});

describe("the client's half: something is listening", () => {
  it("subscribes the shared entitlement cache to the premium subsystem", () => {
    expect(navigatorSource).toMatch(/registerSyncInvalidation\(\s*["']premium["']/);
  });

  /**
   * The subscription is worthless if it does not re-read. Pinning the call
   * keeps a future refactor from leaving a handler that logs and returns.
   */
  it("re-reads the canonical tier when that subsystem is invalidated", () => {
    expect(premiumSubscriptionBody()).toMatch(/loadCanonicalTier\(\)/);
  });

  /**
   * And it must NOT reset first. `resetCanonicalTier` publishes UNKNOWN_TIER to
   * every gate in the app before the new answer lands, so a member reading a
   * Premium screen would watch it blank out and return, for something they did
   * not do. Sign-out has its own reset; this path has no stale identity to
   * clear, so the standing answer stays up until a better one arrives.
   */
  it("does not blank every gate while the re-read is in flight", () => {
    expect(premiumSubscriptionBody()).not.toMatch(/resetCanonicalTier\(/);
  });

  /**
   * The fallback leg. A delta poll invalidates whatever its events name, so the
   * subscription above is already reached on the ordinary path without the
   * subsystem being listed. The list matters when the sync endpoint FAILS: the
   * fallback invalidates that list and nothing else. "Sync is down" is exactly
   * when a just-granted member would otherwise stay locked.
   */
  it("includes premium in the full-resync fallback set", () => {
    const start = navigatorSource.indexOf("startNativeEventSync({");
    expect(start).toBeGreaterThan(-1);
    const call = navigatorSource.slice(start, navigatorSource.indexOf("});", start));
    expect(call).toMatch(/["']premium["']/);
  });
});

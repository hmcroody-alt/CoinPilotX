/**
 * The delivery card's push line is the one place this screen makes a promise
 * about something the user cannot see. It used to render the push PREFERENCE
 * ("not opted out") as if it were push REACHABILITY, so a member who had never
 * granted the OS prompt was told "Push notifications are on." and then waited
 * for a briefing that the server had nowhere to send.
 */
import { pushStatusKey } from "../BriefingsHubScreen";
import type { BriefingDeliveryStatus } from "../../api/briefings";
import en from "../../i18n/catalogs/en/extended.json";

function status(overrides: Partial<BriefingDeliveryStatus>): BriefingDeliveryStatus {
  return {
    enabled: true,
    frequency: "smart",
    frequencies: ["smart"],
    quiet_start: "22:00",
    quiet_end: "07:00",
    timezone: "America/Los_Angeles",
    push_enabled: true,
    briefings_feature_enabled: true,
    last_briefing: null,
    next_check_local: null,
    unseen_count: 0,
    ...overrides
  } as BriefingDeliveryStatus;
}

describe("pushStatusKey", () => {
  it("does not claim push is on when no device can receive it", () => {
    // The exact regression: preference allows push, but there is no transport.
    const key = pushStatusKey(
      status({ push_enabled: true, push_ready: false, push_blocked_reason: "no_devices" })
    );
    expect(key).toBe("briefings:status.pushNoDevice");
    expect(key).not.toBe("briefings:status.pushOn");
  });

  it("says push is on only when the server confirms a send would land", () => {
    expect(
      pushStatusKey(status({ push_ready: true, push_blocked_reason: null, push_device_count: 2 }))
    ).toBe("briefings:status.pushOn");
  });

  it("distinguishes an opt-out from a missing device", () => {
    expect(
      pushStatusKey(status({ push_enabled: false, push_ready: false, push_blocked_reason: "preference_off" }))
    ).toBe("briefings:status.pushOff");
  });

  it("reports a provider-side pause rather than blaming the user's settings", () => {
    expect(
      pushStatusKey(status({ push_ready: false, push_blocked_reason: "provider_disabled" }))
    ).toBe("briefings:status.pushPaused");
  });

  it("falls back to the preference flag against a backend that predates push_ready", () => {
    // An app can outlive its server. Absent the new field, report the only fact
    // available instead of inventing a reachability claim.
    expect(pushStatusKey(status({ push_enabled: true }))).toBe("briefings:status.pushOn");
    expect(pushStatusKey(status({ push_enabled: false }))).toBe("briefings:status.pushOff");
  });

  it("resolves every key it can return to real catalog copy", () => {
    // A key with no catalog entry renders as the raw key string on screen.
    const reasons: Array<BriefingDeliveryStatus["push_blocked_reason"]> = [
      "no_devices",
      "preference_off",
      "provider_disabled",
      null
    ];
    const keys = new Set([
      pushStatusKey(status({ push_ready: true })),
      ...reasons.map((reason) => pushStatusKey(status({ push_ready: false, push_blocked_reason: reason })))
    ]);
    expect(keys.size).toBeGreaterThan(1);
    keys.forEach((key) => {
      const leaf = key.replace("briefings:status.", "");
      expect(Object.keys(en.briefings.status)).toContain(leaf);
    });
  });
});

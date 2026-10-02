/**
 * The delivery card's "next check" line and timezone line were the two places
 * this screen stated something it had not been told.
 *
 * It printed "next check around 18:00" whenever the briefing preference was on,
 * but the server vetoes generation earlier than that — a lapsed Premium member,
 * or anyone with push off globally, is refused before the engine writes a row.
 * So the card promised an evaluation forever while nothing was ever generated,
 * and no database row existed to explain why.
 *
 * It also printed a bare "Timezone: UTC". Nobody in production has ever stored
 * a zone, so every account resolves to UTC by fallback — and presenting that as
 * a setting the user chose makes quiet hours landing in their afternoon look
 * like an engine bug rather than a missing preference.
 */
import { generationStatusKey, timezoneStatusKey } from "../BriefingsHubScreen";
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

function leaf(key: string): string {
  return key.replace("briefings:status.", "");
}

describe("generationStatusKey", () => {
  it("explains a Premium lock instead of leaving the card blank", () => {
    expect(
      generationStatusKey(
        status({ generation_ready: false, generation_blocked_reason: "premium_required" })
      )
    ).toBe("briefings:status.generationPremium");
  });

  it("explains that a global push opt-out stops the briefing itself", () => {
    // Not just the notification: the server's veto runs before generation, so
    // there is no in-app briefing either. The copy has to say that.
    expect(
      generationStatusKey(
        status({ generation_ready: false, generation_blocked_reason: "push_opt_out" })
      )
    ).toBe("briefings:status.generationPushOptOut");
  });

  it("names a platform-wide pause rather than blaming the account", () => {
    expect(
      generationStatusKey(
        status({ generation_ready: false, generation_blocked_reason: "feature_disabled" })
      )
    ).toBe("briefings:status.generationPaused");
  });

  it("does not explain the user's own switch back to them", () => {
    // The master toggle sits directly below this line and already says "off".
    expect(
      generationStatusKey(
        status({ generation_ready: false, generation_blocked_reason: "preference_off" })
      )
    ).toBeNull();
  });

  it("stays silent when generation is ready", () => {
    expect(
      generationStatusKey(status({ generation_ready: true, generation_blocked_reason: null }))
    ).toBeNull();
  });

  it("stays silent against a backend that predates the field", () => {
    // An app outlives its server. Absent generation_ready, claiming a block
    // would be the same invention in the opposite direction.
    expect(generationStatusKey(status({}))).toBeNull();
  });

  it("resolves every key it can return to real catalog copy", () => {
    // A key with no catalog entry renders as the raw key string on screen.
    const reasons: Array<BriefingDeliveryStatus["generation_blocked_reason"]> = [
      "premium_required",
      "push_opt_out",
      "feature_disabled"
    ];
    const keys = reasons.map((reason) =>
      generationStatusKey(status({ generation_ready: false, generation_blocked_reason: reason }))
    );
    expect(new Set(keys).size).toBe(reasons.length);
    keys.forEach((key) => {
      expect(Object.keys(en.briefings.status)).toContain(leaf(key as string));
    });
  });
});

describe("timezoneStatusKey", () => {
  it("marks a fallback zone as a default, not a choice", () => {
    expect(timezoneStatusKey(status({ timezone: "UTC", timezone_source: "fallback" }))).toBe(
      "briefings:status.timezoneDefault"
    );
  });

  it("treats a failed lookup as a default too", () => {
    // Either way the user never picked this zone, which is the thing that
    // misleads them; the distinction is for the log, not the card.
    expect(timezoneStatusKey(status({ timezone: "UTC", timezone_source: "error" }))).toBe(
      "briefings:status.timezoneDefault"
    );
  });

  it("says so when a stored zone could not be read", () => {
    expect(timezoneStatusKey(status({ timezone: "UTC", timezone_source: "unknown_zone" }))).toBe(
      "briefings:status.timezoneUnknown"
    );
  });

  it("states a chosen zone plainly", () => {
    expect(timezoneStatusKey(status({ timezone_source: "stored" }))).toBe(
      "briefings:status.timezone"
    );
  });

  it("states it plainly against a backend that predates the field", () => {
    expect(timezoneStatusKey(status({}))).toBe("briefings:status.timezone");
  });

  it("resolves every key it can return to real catalog copy", () => {
    const sources: Array<BriefingDeliveryStatus["timezone_source"]> = [
      "stored",
      "fallback",
      "unknown_zone",
      "error",
      undefined
    ];
    const keys = new Set(sources.map((source) => timezoneStatusKey(status({ timezone_source: source }))));
    expect(keys.size).toBe(3);
    keys.forEach((key) => {
      expect(Object.keys(en.briefings.status)).toContain(leaf(key));
    });
  });
});

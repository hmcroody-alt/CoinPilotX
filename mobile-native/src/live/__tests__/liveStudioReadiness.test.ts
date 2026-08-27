jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import {
  computeOverallReadiness,
  deriveLiveStudioStatus,
  emptyLiveStudioDraft,
  isLiveHostPlaybackId,
  LIVE_STUDIO_UPCOMING,
  mapAccountToReadiness,
  mapBatteryToReadiness,
  mapDeviceToReadiness,
  mapLatencyToNetwork,
  mapPermissionToReadiness,
  normalizeLiveStudioDraft,
  readinessSummary,
  ReadinessCheck
} from "../liveStudioReadiness";
import { livePlaybackOwnerId } from "../livePlaybackOwnership";

describe("mapPermissionToReadiness", () => {
  it("marks granted permissions ready with no action", () => {
    const check = mapPermissionToReadiness("camera", true, true);
    expect(check.level).toBe("ready");
    expect(check.action).toBeUndefined();
  });

  it("blocks and offers a request action when it can still ask", () => {
    const check = mapPermissionToReadiness("microphone", false, true);
    expect(check.level).toBe("blocked");
    expect(check.action).toBe("request-mic");
  });

  it("blocks and points to settings when it can no longer ask", () => {
    const check = mapPermissionToReadiness("camera", false, false);
    expect(check.level).toBe("blocked");
    expect(check.action).toBe("open-settings");
  });
});

describe("mapLatencyToNetwork", () => {
  it("treats a null latency as offline and blocked", () => {
    const { quality, check } = mapLatencyToNetwork(null);
    expect(quality).toBe("offline");
    expect(check.level).toBe("blocked");
    expect(check.action).toBe("retry-network");
  });

  it("classifies low latency as excellent and ready", () => {
    expect(mapLatencyToNetwork(80).quality).toBe("excellent");
    expect(mapLatencyToNetwork(80).check.level).toBe("ready");
  });

  it("classifies mid latency as good and ready", () => {
    expect(mapLatencyToNetwork(300).quality).toBe("good");
    expect(mapLatencyToNetwork(300).check.level).toBe("ready");
  });

  it("recommends caution for degraded and weak links but never blocks", () => {
    expect(mapLatencyToNetwork(600).quality).toBe("degraded");
    expect(mapLatencyToNetwork(600).check.level).toBe("recommend");
    expect(mapLatencyToNetwork(1500).quality).toBe("weak");
    expect(mapLatencyToNetwork(1500).check.level).toBe("recommend");
  });
});

describe("mapBatteryToReadiness", () => {
  it("never blocks broadcasting on battery", () => {
    expect(mapBatteryToReadiness(0.05, false).level).toBe("recommend");
    expect(mapBatteryToReadiness(0.8, true).level).toBe("recommend");
    expect(mapBatteryToReadiness(0.8, false).level).toBe("ready");
    expect(mapBatteryToReadiness(null, false).level).toBe("ready");
  });
});

describe("mapDeviceToReadiness", () => {
  it("recommends but does not block on a simulator", () => {
    expect(mapDeviceToReadiness(true).level).toBe("ready");
    expect(mapDeviceToReadiness(false).level).toBe("recommend");
  });
});

describe("mapAccountToReadiness", () => {
  it("blocks a signed-out creator and offers a way back in", () => {
    const check = mapAccountToReadiness("signedOut");
    expect(check.level).toBe("blocked");
    expect(check.action).toBe("sign-in");
  });

  /**
   * Bootstrap resolves in well under a second. Treating it as blocked would
   * flash BLOCKED across the top of the dashboard on every cold open — a
   * warning that is wrong by the time anyone has read it.
   */
  it("does not block while the session is still bootstrapping", () => {
    expect(mapAccountToReadiness("loading").level).not.toBe("blocked");
  });

  it("blocks an account the server has suspended, and says which state it is in", () => {
    const check = mapAccountToReadiness("signedIn", "suspended");
    expect(check.level).toBe("blocked");
    expect(check.detail).toContain("suspended");
    // Nothing the app can fix, so no action button that would do nothing.
    expect(check.action).toBeUndefined();
  });

  it("treats a signed-in active account as ready, including a missing status", () => {
    expect(mapAccountToReadiness("signedIn", "active").level).toBe("ready");
    expect(mapAccountToReadiness("signedIn", "ACTIVE").level).toBe("ready");
    expect(mapAccountToReadiness("signedIn", "").level).toBe("ready");
    expect(mapAccountToReadiness("signedIn", null).level).toBe("ready");
  });
});

describe("deriveLiveStudioStatus", () => {
  it("reports LIVE over everything else once a broadcast is running", () => {
    // On air with a flat battery is still on air. The dashboard must not lead
    // with a recommendation when the creator is being watched right now.
    expect(deriveLiveStudioStatus("recommend", true)).toBe("LIVE");
    expect(deriveLiveStudioStatus("blocked", true)).toBe("LIVE");
  });

  it("collapses recommendations into READY and keeps blocked distinct", () => {
    expect(deriveLiveStudioStatus("ready", false)).toBe("READY");
    expect(deriveLiveStudioStatus("recommend", false)).toBe("READY");
    expect(deriveLiveStudioStatus("blocked", false)).toBe("BLOCKED");
  });
});

describe("isLiveHostPlaybackId", () => {
  /**
   * The pin that stops the prefix drifting. `livePlaybackOwnership.ts` is a
   * protected real-time path, so the studio reads its ids rather than editing
   * it — which only works while both agree on the spelling.
   */
  it("matches the id the ownership module actually mints for a host", () => {
    expect(isLiveHostPlaybackId(livePlaybackOwnerId("host", 7))).toBe(true);
  });

  it("does not mistake watching a Live for hosting one", () => {
    // Both claim the coordinator with kind "live"; only the scope separates
    // "you are on air" from "you are in the audience".
    expect(isLiveHostPlaybackId(livePlaybackOwnerId("viewer", 7))).toBe(false);
    expect(isLiveHostPlaybackId(livePlaybackOwnerId("feed", 7))).toBe(false);
    expect(isLiveHostPlaybackId(null)).toBe(false);
    expect(isLiveHostPlaybackId(undefined)).toBe(false);
    expect(isLiveHostPlaybackId("reel:9")).toBe(false);
  });
});

describe("LIVE_STUDIO_UPCOMING", () => {
  it("covers the six tools the dashboard promises, with unique keys", () => {
    const keys = LIVE_STUDIO_UPCOMING.map((item) => item.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).toEqual(["schedule", "settings", "audience", "moderation", "analytics", "replay"]);
  });

  it("gives every row a blurb, so no row is just a word and a badge", () => {
    LIVE_STUDIO_UPCOMING.forEach((item) => {
      expect(item.label.length).toBeGreaterThan(0);
      expect(item.blurb.length).toBeGreaterThan(20);
    });
  });
});

describe("computeOverallReadiness", () => {
  const ready: ReadinessCheck = { key: "device", label: "Device", level: "ready", detail: "" };
  const recommend: ReadinessCheck = { key: "battery", label: "Battery", level: "recommend", detail: "" };
  const blocked: ReadinessCheck = { key: "camera", label: "Camera", level: "blocked", detail: "" };

  it("prioritizes blocked over recommend over ready", () => {
    expect(computeOverallReadiness([ready, recommend, blocked])).toBe("blocked");
    expect(computeOverallReadiness([ready, recommend])).toBe("recommend");
    expect(computeOverallReadiness([ready, ready])).toBe("ready");
  });
});

describe("readinessSummary", () => {
  it("returns a distinct label for each level", () => {
    const labels = new Set([
      readinessSummary("ready").label,
      readinessSummary("recommend").label,
      readinessSummary("blocked").label
    ]);
    expect(labels.size).toBe(3);
  });
});

describe("normalizeLiveStudioDraft", () => {
  it("falls back to defaults for missing or invalid values", () => {
    const draft = normalizeLiveStudioDraft(null);
    expect(draft).toEqual(emptyLiveStudioDraft());

    const invalid = normalizeLiveStudioDraft({ liveType: "bogus" as never, audience: "bogus" as never });
    expect(invalid.liveType).toBe("solo");
    expect(invalid.audience).toBe("public");
  });

  it("clamps title and description length", () => {
    const draft = normalizeLiveStudioDraft({ title: "a".repeat(300), description: "b".repeat(900) });
    expect(draft.title).toHaveLength(120);
    expect(draft.description).toHaveLength(500);
  });

  it("preserves valid enum values and boolean toggles", () => {
    const draft = normalizeLiveStudioDraft({
      liveType: "podcast",
      audience: "subscribers",
      allowComments: false,
      recordReplay: false
    });
    expect(draft.liveType).toBe("podcast");
    expect(draft.audience).toBe("subscribers");
    expect(draft.allowComments).toBe(false);
    expect(draft.recordReplay).toBe(false);
  });
});

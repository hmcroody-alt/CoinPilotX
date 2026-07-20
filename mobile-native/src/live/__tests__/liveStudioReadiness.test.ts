jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import {
  computeOverallReadiness,
  emptyLiveStudioDraft,
  liveStudioHandoffUrl,
  mapBatteryToReadiness,
  mapDeviceToReadiness,
  mapLatencyToNetwork,
  mapPermissionToReadiness,
  normalizeLiveStudioDraft,
  readinessSummary,
  ReadinessCheck
} from "../liveStudioReadiness";

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

describe("liveStudioHandoffUrl", () => {
  it("carries native context and draft settings into the studio URL", () => {
    const draft = {
      ...emptyLiveStudioDraft(),
      title: "Launch night",
      description: "Q&A with the team",
      liveType: "panel" as const,
      audience: "followers" as const,
      allowComments: false,
      recordReplay: true
    };
    const url = liveStudioHandoffUrl("https://api.example.com/", draft);
    expect(url).toContain("https://api.example.com/pulse/live/studio?");
    expect(url).toContain("context_type=native");
    expect(url).toContain("live_type=panel");
    expect(url).toContain("audience=followers");
    expect(url).toContain("comments=0");
    expect(url).toContain("record=1");
    expect(url).toContain("title=Launch+night");
  });

  it("omits empty title and description", () => {
    const url = liveStudioHandoffUrl("https://api.example.com", emptyLiveStudioDraft());
    expect(url).not.toContain("title=");
    expect(url).not.toContain("description=");
  });
});

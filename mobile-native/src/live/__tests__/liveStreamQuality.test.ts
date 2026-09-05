/**
 * Stages 24-26. Three properties are worth more than the rest of this file put
 * together, so they are asserted exhaustively rather than by example:
 *
 *   - An unauthorized client opens no capture hardware. Not "usually", not "in
 *     the cases the author thought of" — for every role, with every value of
 *     `videoRequested`.
 *   - No degradation step ever silences a participant.
 *   - The encoder ladder only ever steps down as the stage grows.
 */

import {
  assessDevicePerformance,
  planTouchesCaptureHardware,
  publisherVideoProfile,
  remoteStreamTypeFor,
  resolvePublishPlan,
  stepSilencesParticipant,
  type DegradationStep,
  type DeviceSample
} from "../liveStreamQuality";
import type { LiveRole } from "../liveParticipantRegistry";

const ROLES: LiveRole[] = ["host", "cohost", "guest", "audience"];
const STEPS: DegradationStep[] = [
  "none",
  "subscribeLow",
  "reducePublish",
  "audioOnlyRemote",
  "stopPublishVideo"
];

/** A device with nothing wrong with it. */
function healthy(overrides: Partial<DeviceSample> = {}): DeviceSample {
  return { encodeFps: 30, targetFps: 30, cpuPercent: 35, rttMs: 90, uplinkLoss: 0, ...overrides };
}

describe("Stage 25 — an audience member initialises nothing", () => {
  it("gives an audience member a plan that touches no hardware", () => {
    const plan = resolvePublishPlan({ role: "audience", serverAuthorized: true });
    expect(plan.clientRole).toBe("audience");
    expect(planTouchesCaptureHardware(plan)).toBe(false);
    expect(plan.configureEncoder).toBe(false);
    expect(plan.enableDualStream).toBe(false);
  });

  it("refuses to publish for any role the server has not authorized", () => {
    // The whole point of the gate: a client believing it is a host is not
    // sufficient to open a camera.
    for (const role of ROLES) {
      for (const videoRequested of [true, false, undefined]) {
        const plan = resolvePublishPlan({ role, serverAuthorized: false, videoRequested });
        expect(planTouchesCaptureHardware(plan)).toBe(false);
        expect(plan.clientRole).toBe("audience");
      }
    }
  });

  it("lets every authorized publishing role broadcast", () => {
    for (const role of ["host", "cohost", "guest"] as LiveRole[]) {
      const plan = resolvePublishPlan({ role, serverAuthorized: true });
      expect(plan.clientRole).toBe("broadcaster");
      expect(plan.enableVideo).toBe(true);
      expect(plan.enableAudio).toBe(true);
      expect(plan.startPreview).toBe(true);
      expect(plan.enableDualStream).toBe(true);
    }
  });

  it("opens the microphone but not the camera for an audio-only guest", () => {
    const plan = resolvePublishPlan({ role: "guest", serverAuthorized: true, videoRequested: false });
    expect(plan.enableAudio).toBe(true);
    expect(plan.enableVideo).toBe(false);
    expect(plan.startPreview).toBe(false);
    expect(plan.configureEncoder).toBe(false);
    // A simulcast layer with no video behind it is upload nobody subscribes to.
    expect(plan.enableDualStream).toBe(false);
  });

  it("enables the video MODULE for every client, audience included", () => {
    // The P0 this guards: the module is what lets a client DECODE remote
    // video. An audience plan without it joins, hears audio, and never gets a
    // first remote video frame — "waiting for host media" forever, then the
    // Web fallback. The module is not capture hardware, so this coexists with
    // Stage 25: the same plan must still touch no camera or microphone.
    for (const role of ROLES) {
      for (const serverAuthorized of [true, false]) {
        const plan = resolvePublishPlan({ role, serverAuthorized });
        expect(plan.enableVideoModule).toBe(true);
      }
    }
    const audience = resolvePublishPlan({ role: "audience", serverAuthorized: true });
    expect(audience.enableVideoModule).toBe(true);
    expect(planTouchesCaptureHardware(audience)).toBe(false);
  });

  it("returns a fresh object each time so a caller cannot mutate the shared plan", () => {
    const a = resolvePublishPlan({ role: "audience", serverAuthorized: true });
    a.enableAudio = true;
    const b = resolvePublishPlan({ role: "audience", serverAuthorized: true });
    expect(b.enableAudio).toBe(false);
  });
});

describe("Stage 24 — which stream each tile subscribes to", () => {
  it("always gives the focus tile the high stream on a healthy device", () => {
    for (const publisherCount of [1, 2, 3, 4, 5, 6, 12]) {
      expect(remoteStreamTypeFor({ publisherCount, isFocus: true })).toBe("high");
    }
  });

  it("keeps non-focus tiles high below three publishers and drops them at three", () => {
    expect(remoteStreamTypeFor({ publisherCount: 1, isFocus: false })).toBe("high");
    expect(remoteStreamTypeFor({ publisherCount: 2, isFocus: false })).toBe("high");
    expect(remoteStreamTypeFor({ publisherCount: 3, isFocus: false })).toBe("low");
    expect(remoteStreamTypeFor({ publisherCount: 6, isFocus: false })).toBe("low");
  });

  it("drops a degraded device to low everywhere, focus included", () => {
    expect(remoteStreamTypeFor({ publisherCount: 1, isFocus: true, degraded: true })).toBe("low");
    expect(remoteStreamTypeFor({ publisherCount: 6, isFocus: false, degraded: true })).toBe("low");
  });

  it("does not fall over on nonsense counts", () => {
    for (const publisherCount of [0, -4, NaN, Infinity]) {
      expect(["high", "low"]).toContain(remoteStreamTypeFor({ publisherCount, isFocus: false }));
    }
  });

  it("steps the encoder ladder down monotonically as the stage grows", () => {
    let previous = publisherVideoProfile(1);
    expect(previous).toEqual({ width: 720, height: 1280, frameRate: 30 });
    for (let count = 2; count <= 8; count += 1) {
      const next = publisherVideoProfile(count);
      expect(next.width).toBeLessThanOrEqual(previous.width);
      expect(next.height).toBeLessThanOrEqual(previous.height);
      expect(next.frameRate).toBeLessThanOrEqual(previous.frameRate);
      previous = next;
    }
  });

  it("never publishes below a legible floor, however large the stage", () => {
    for (const count of [6, 12, 40, NaN]) {
      const profile = publisherVideoProfile(count);
      expect(profile.width).toBeGreaterThanOrEqual(360);
      // Motion judder reads as "broken" far more readily than softness does.
      expect(profile.frameRate).toBeGreaterThanOrEqual(24);
    }
  });
});

describe("Stage 26 — what a struggling device gives up", () => {
  it("asks nothing of a healthy solo Live", () => {
    expect(assessDevicePerformance(healthy(), 1).step).toBe("none");
    expect(assessDevicePerformance(healthy(), 1).health).toBe("good");
  });

  it("asks nothing of a healthy device on a busy stage", () => {
    expect(assessDevicePerformance(healthy(), 6).step).toBe("none");
  });

  it("drops to low streams first when a large stage starts to bite", () => {
    const result = assessDevicePerformance(healthy({ cpuPercent: 65 }), 4);
    expect(result.step).toBe("subscribeLow");
    expect(result.health).toBe("strained");
  });

  it("does not reach for the stage-size rung on a two-person Live", () => {
    // With two publishers there are no small tiles to downgrade.
    expect(assessDevicePerformance(healthy({ cpuPercent: 65 }), 2).step).toBe("none");
  });

  it("escalates through the ladder as pressure rises", () => {
    expect(assessDevicePerformance(healthy({ cpuPercent: 78 }), 4).step).toBe("reducePublish");
    expect(assessDevicePerformance(healthy({ cpuPercent: 87 }), 4).step).toBe("audioOnlyRemote");
    expect(assessDevicePerformance(healthy({ cpuPercent: 95 }), 4).step).toBe("stopPublishVideo");
  });

  it("reacts to a stalled encoder as well as to CPU", () => {
    expect(assessDevicePerformance(healthy({ encodeFps: 24 }), 4).step).toBe("subscribeLow");
    expect(assessDevicePerformance(healthy({ encodeFps: 18 }), 4).step).toBe("reducePublish");
    expect(assessDevicePerformance(healthy({ encodeFps: 12 }), 4).step).toBe("audioOnlyRemote");
    expect(assessDevicePerformance(healthy({ encodeFps: 6 }), 4).step).toBe("stopPublishVideo");
  });

  it("reacts to uplink loss and to latency", () => {
    expect(assessDevicePerformance(healthy({ uplinkLoss: 0.12 }), 4).step).toBe("reducePublish");
    expect(assessDevicePerformance(healthy({ uplinkLoss: 0.25 }), 4).step).toBe("audioOnlyRemote");
    expect(assessDevicePerformance(healthy({ uplinkLoss: 0.4 }), 4).step).toBe("stopPublishVideo");
    expect(assessDevicePerformance(healthy({ rttMs: 600 }), 4).step).toBe("reducePublish");
    expect(assessDevicePerformance(healthy({ rttMs: 900 }), 4).step).toBe("audioOnlyRemote");
  });

  it("still protects a solo publisher whose device is genuinely failing", () => {
    // The solo shortcut must not become a hole through which a dying device
    // keeps publishing.
    expect(assessDevicePerformance(healthy({ cpuPercent: 95 }), 1).step).toBe("stopPublishVideo");
  });

  it("never degrades audio, on any sample, at any stage size", () => {
    const samples: DeviceSample[] = [
      healthy(),
      healthy({ cpuPercent: 100, encodeFps: 0, rttMs: 5000, uplinkLoss: 1 }),
      healthy({ cpuPercent: NaN, encodeFps: NaN, targetFps: 0, rttMs: -1, uplinkLoss: -1 }),
      healthy({ cpuPercent: 76 }),
      healthy({ cpuPercent: 86 })
    ];
    for (const sample of samples) {
      for (const count of [1, 2, 3, 6, 12]) {
        const result = assessDevicePerformance(sample, count);
        expect(result.sacrificesAudio).toBe(false);
        expect(stepSilencesParticipant(result.step)).toBe(false);
      }
    }
  });

  it("never silences a participant, for every step in the union", () => {
    for (const step of STEPS) {
      expect(stepSilencesParticipant(step)).toBe(false);
    }
  });

  it("reports a reason that is safe to log", () => {
    const result = assessDevicePerformance(healthy({ cpuPercent: 95 }), 4);
    expect(result.reason).toMatch(/^[a-z_]+$/);
  });

  it("survives a malformed sample without claiming a healthy device is failing", () => {
    const result = assessDevicePerformance({} as DeviceSample, 1);
    expect(STEPS).toContain(result.step);
    expect(result.sacrificesAudio).toBe(false);
  });
});

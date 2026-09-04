/**
 * Stage 55. The properties that make Live telemetry safe to leave switched on.
 *
 * These tests are worth more than they look, because the defect they guard
 * against is invisible in review: a line reading
 * `emitAgoraLiveEvent({ name: "remote_joined", uid })` is exactly what a
 * telemetry call is supposed to look like. It only becomes a privacy problem
 * once you know that in this codebase the Agora uid is the PulseSoc user id.
 *
 * So the suite asserts the guarantee at the boundary — what actually reaches
 * `console` — rather than trusting the shape of the call sites.
 */

import { participantTag, sanitizeLiveTelemetry } from "../liveTelemetryPrivacy";
import { emitAgoraLiveEvent } from "../agoraLiveTelemetry";

describe("participant pseudonyms", () => {
  test("the same person in the same Live gets the same tag", () => {
    // Without this, a log cannot be read at all: every event would describe a
    // different anonymous stranger.
    expect(participantTag(9001, 1002)).toBe(participantTag(9001, 1002));
  });

  test("two people in one Live get different tags", () => {
    expect(participantTag(9001, 1002)).not.toBe(participantTag(9001, 1003));
  });

  test("the same person in two Lives gets different tags", () => {
    // This is the property that matters most. The raw uid is stable forever and
    // across every device, which is what turns debug logs into a social graph.
    // Salting with the live id is what severs it.
    expect(participantTag(9001, 1002)).not.toBe(participantTag(9002, 1002));
  });

  test("a tag does not contain the user id", () => {
    const tag = participantTag(9001, 4242);
    expect(tag).not.toContain("4242");
    expect(tag).toMatch(/^p_[a-z0-9]+$/);
  });

  test("an unknown uid is an empty tag, not a tag for zero", () => {
    // "We do not know who this is" and "this is user 0" must not look the same
    // in a log, or the first will be read as the second.
    expect(participantTag(9001, 0)).toBe("");
    expect(participantTag(9001, undefined)).toBe("");
    expect(participantTag(9001, -1)).toBe("");
    expect(participantTag(9001, "not a number")).toBe("");
  });

  test("tags are short enough to read and stable in shape", () => {
    for (let uid = 1; uid < 200; uid += 1) {
      const tag = participantTag(9001, uid);
      expect(tag.length).toBeLessThanOrEqual(10);
    }
  });

  test("a thousand participants produce a thousand distinct tags", () => {
    // A hash collision would silently merge two people in the logs, which is
    // the one failure mode that is worse than no tag at all.
    const tags = new Set<string>();
    for (let uid = 1; uid <= 1000; uid += 1) tags.add(participantTag(9001, uid));
    expect(tags.size).toBe(1000);
  });
});

describe("event sanitisation", () => {
  test("uid is renamed, not merely rewritten", () => {
    const safe = sanitizeLiveTelemetry({ name: "remote_joined", liveId: 9001, uid: 1002 });
    // The key is gone. A reader cannot mistake the tag for an account id, and a
    // grep for `uid` across emitted telemetry finds nothing.
    expect(safe).not.toHaveProperty("uid");
    expect(safe.participant).toBe(participantTag(9001, 1002));
    expect(safe.name).toBe("remote_joined");
    expect(safe.liveId).toBe(9001);
  });

  test("the live id is kept, because a broadcast is a public object", () => {
    // Redacting it would make the telemetry unnavigable while protecting
    // nothing: the Live is in the feed under that id.
    expect(sanitizeLiveTelemetry({ liveId: 9001 }).liveId).toBe(9001);
  });

  test.each([
    "token",
    "rtcToken",
    "appId",
    "app_id",
    "appCertificate",
    "secret",
    "password",
    "credential",
    "authorization",
    "signature",
    "cookie",
    "email",
    "phone"
  ])("a field named %s never reaches the log", (key) => {
    const safe = sanitizeLiveTelemetry({ name: "join", [key]: "sensitive-value" });
    expect(Object.keys(safe)).not.toContain(key);
    expect(JSON.stringify(safe)).not.toContain("sensitive-value");
  });

  test("free text is truncated so a stack trace cannot ride along in `reason`", () => {
    const safe = sanitizeLiveTelemetry({ name: "sdk_error", reason: "x".repeat(5000) });
    expect(String(safe.reason).length).toBeLessThanOrEqual(121);
  });

  test("objects and arrays are dropped rather than serialised", () => {
    // The way an SDK stats blob ends up in a production log is someone passing
    // it through whole, once, to see what is in it.
    const safe = sanitizeLiveTelemetry({
      name: "rtc_stats",
      stats: { userId: 1002, token: "abc" },
      participants: [1002, 1003]
    });
    expect(safe).not.toHaveProperty("stats");
    expect(safe).not.toHaveProperty("participants");
    expect(JSON.stringify(safe)).not.toContain("1002");
  });

  test("media statistics pass through untouched — they are the point of the telemetry", () => {
    const safe = sanitizeLiveTelemetry({
      name: "local_video_stats",
      liveId: 9001,
      videoBitrateKbps: 2400,
      videoFps: 30,
      packetLossPercent: 0.4,
      width: 720,
      height: 1280
    });
    expect(safe).toMatchObject({
      videoBitrateKbps: 2400,
      videoFps: 30,
      packetLossPercent: 0.4,
      width: 720,
      height: 1280
    });
  });

  test("NaN and Infinity are dropped rather than logged", () => {
    const safe = sanitizeLiveTelemetry({ latencyMs: NaN, videoFps: Infinity, audioBitrateKbps: 64 });
    expect(safe).not.toHaveProperty("latencyMs");
    expect(safe).not.toHaveProperty("videoFps");
    expect(safe.audioBitrateKbps).toBe(64);
  });

  test("a malformed event does not throw", () => {
    // Telemetry must never be the thing that crashes a Live.
    expect(() => sanitizeLiveTelemetry(null as never)).not.toThrow();
    expect(sanitizeLiveTelemetry(undefined as never)).toEqual({});
  });
});

describe("the emitter is the chokepoint", () => {
  const logs: unknown[][] = [];
  let spy: jest.SpyInstance;

  beforeEach(() => {
    logs.length = 0;
    spy = jest.spyOn(console, "log").mockImplementation((...args: unknown[]) => {
      logs.push(args);
    });
  });

  afterEach(() => spy.mockRestore());

  test("a call site that passes a raw uid still logs only a tag", () => {
    // This is the assertion the whole design exists for. Call sites are allowed
    // to be naive; the emitter is not.
    emitAgoraLiveEvent({ name: "remote_joined", liveId: 9001, uid: 1002 });
    expect(logs).toHaveLength(1);
    const payload = logs[0][1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty("uid");
    expect(payload.participant).toBe(participantTag(9001, 1002));
    expect(JSON.stringify(payload)).not.toContain("1002");
  });

  test("no emitted line anywhere contains a bare user id", () => {
    // A sweep across the event vocabulary the hook actually uses, rather than
    // one representative case.
    const names = [
      "channel_joined",
      "remote_joined",
      "first_remote_audio",
      "first_remote_video",
      "local_audio_published",
      "local_video_published",
      "token_renewed",
      "token_renewal_failed",
      "role_upgraded",
      "role_demoted",
      "connection_state",
      "network_quality",
      "rtc_stats",
      "sdk_error",
      "leave"
    ];
    for (const name of names) emitAgoraLiveEvent({ name, liveId: 9001, uid: 777_123 });
    expect(logs).toHaveLength(names.length);
    for (const entry of logs) {
      expect(JSON.stringify(entry[1])).not.toContain("777123");
    }
  });

  test("quality events are still throttled after sanitisation", () => {
    // Sanitising must not have moved the throttle, or a busy Live floods the
    // log with one line per stats callback per second.
    emitAgoraLiveEvent({ name: "rtc_stats", liveId: 9001, uid: 1002 }, true);
    emitAgoraLiveEvent({ name: "rtc_stats", liveId: 9001, uid: 1002 }, true);
    emitAgoraLiveEvent({ name: "rtc_stats", liveId: 9001, uid: 1002 }, true);
    expect(logs.length).toBeLessThanOrEqual(1);
  });
});

describe("no call site bypasses the emitter", () => {
  const fs = require("fs") as typeof import("fs");
  const path = require("path") as typeof import("path");
  const hook = fs.readFileSync(path.join(__dirname, "..", "useAgoraLiveBroadcastRoom.ts"), "utf8");

  test("the hook logs through emitAgoraLiveEvent and not through console directly", () => {
    // A direct `console.log(uid)` in the hook would defeat everything above.
    // The check is on the source rather than on behaviour because the hook
    // cannot be executed under Jest — Agora's SDK is loaded by dynamic import,
    // which this repo's Babel config does not transpile.
    const direct = hook.match(/console\.(log|warn|info|debug)\s*\(/g) || [];
    expect(direct).toHaveLength(0);
  });

  test("every telemetry call in the hook goes through the emitter", () => {
    expect(hook).toContain("emitAgoraLiveEvent");
    // And the emitter it imports is the sanitising one, not a local shim.
    expect(hook).toMatch(/import\s*\{\s*emitAgoraLiveEvent\s*\}\s*from\s*"\.\/agoraLiveTelemetry"/);
  });
});

/**
 * The wiring, enforced.
 *
 * mediaQualityPolicy.test.ts proves the resolver returns the right values.
 * This file proves the adapters USE those values, and — the part that actually
 * matters — that with every flag off the object handed to `new Room({...})` is
 * indistinguishable from the object literal that was written there before this
 * layer existed.
 *
 * The baseline literals below are transcribed from commit
 * ce03e160eaf4649a8e02bc3b609a3182ca9d3859, the commit whose audio was
 * physically confirmed working. They are duplicated here on purpose: this file
 * is the independent witness. If it imported the constants from the policy
 * module, then a change to those constants would change both sides of the
 * assertion and the test would pass while the behaviour changed.
 */
import fs from "fs";
import path from "path";

import {
  buildRoomQualityOptions,
  resolveMediaQualityPlan,
  type MediaFeature
} from "../mediaQualityPolicy";
import { parseMediaQualityFlags } from "../mediaQualityFlags";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const NATIVE_SRC = path.join(REPO_ROOT, "mobile-native", "src");
const CALL_ADAPTER = path.join(NATIVE_SRC, "calls", "useNativeCallRoom.ts");
const LIVE_ADAPTER = path.join(NATIVE_SRC, "live", "useLiveBroadcastRoom.ts");
const PUBLISHER_MEDIA = path.join(NATIVE_SRC, "core", "realtimePublisherMedia.ts");

/**
 * Source with comments removed.
 *
 * Every assertion below is about what the adapters *do*. A doc comment that
 * explains why `setCameraEnabled(true)` stays bare is not a call to
 * `setCameraEnabled`, and a test that could not tell the difference would
 * quietly reward deleting the explanation. Line comments are only dropped when
 * the line begins with `//`, so a `wss://` inside a string survives.
 */
function read(file: string): string {
  return fs
    .readFileSync(file, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

/** The exact literal that used to be in useNativeCallRoom.ts. */
const CALL_BASELINE_ROOM_OPTIONS = {
  adaptiveStream: true,
  dynacast: true,
  audioCaptureDefaults: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true
  },
  publishDefaults: {
    simulcast: true,
    dtx: true,
    red: true,
    stopMicTrackOnMute: false
  }
};

/** The exact literal that used to be in useLiveBroadcastRoom.ts, expanded. */
const LIVE_BASELINE_ROOM_OPTIONS = {
  adaptiveStream: true,
  dynacast: true,
  audioCaptureDefaults: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true
  },
  videoCaptureDefaults: {
    facingMode: "user",
    frameRate: 30,
    resolution: { width: 720, height: 1280, frameRate: 30, aspectRatio: 9 / 16 }
  },
  publishDefaults: {
    videoEncoding: { maxBitrate: 2_300_000, maxFramerate: 30, priority: "medium" },
    simulcast: true,
    dtx: true,
    red: true,
    stopMicTrackOnMute: false
  }
};

function stableOptionsFor(feature: MediaFeature) {
  return buildRoomQualityOptions(resolveMediaQualityPlan({ feature }));
}

/* -------------------------------------------------------------------------- */
/* THE CENTRAL GUARANTEE                                                       */
/* -------------------------------------------------------------------------- */

describe("with every flag off, the Room options are the verified baseline", () => {
  it("reproduces the call adapter's literal exactly, for an audio call", () => {
    expect(stableOptionsFor("audio_call")).toEqual(CALL_BASELINE_ROOM_OPTIONS);
  });

  it("reproduces the call adapter's literal exactly, for a video call", () => {
    // A video call and an audio call built the SAME Room options at the
    // baseline. The camera options were absent from both, because the adapter
    // called setCameraEnabled(true) bare.
    expect(stableOptionsFor("video_call")).toEqual(CALL_BASELINE_ROOM_OPTIONS);
  });

  it("reproduces the live adapter's literal exactly, for a host", () => {
    expect(stableOptionsFor("live_host")).toEqual(LIVE_BASELINE_ROOM_OPTIONS);
  });

  it("reproduces the live adapter's literal exactly, for a guest", () => {
    expect(stableOptionsFor("live_guest")).toEqual(LIVE_BASELINE_ROOM_OPTIONS);
  });

  it("gives a viewer the audio-only baseline, with no camera configuration", () => {
    expect(stableOptionsFor("live_viewer")).toEqual(CALL_BASELINE_ROOM_OPTIONS);
  });

  it("adds no key that was not in the baseline", () => {
    for (const feature of ["audio_call", "video_call", "live_viewer"] as const) {
      const options = stableOptionsFor(feature);
      expect(Object.keys(options).sort()).toEqual([
        "adaptiveStream",
        "audioCaptureDefaults",
        "dynacast",
        "publishDefaults"
      ]);
      expect(Object.keys(options.publishDefaults).sort()).toEqual([
        "dtx",
        "red",
        "simulcast",
        "stopMicTrackOnMute"
      ]);
    }
  });

  it("sets no audio bitrate at stable, because the baseline set none", () => {
    for (const feature of ["audio_call", "video_call", "live_host", "live_guest", "live_viewer"] as const) {
      expect(stableOptionsFor(feature).publishDefaults.audioBitrate).toBeUndefined();
    }
  });

  it("sets no degradationPreference at stable, because the baseline set none", () => {
    for (const feature of ["audio_call", "video_call", "live_host", "live_guest", "live_viewer"] as const) {
      expect(stableOptionsFor(feature).publishDefaults.degradationPreference).toBeUndefined();
    }
  });

  it("holds for a malformed or absent server payload", () => {
    for (const payload of [undefined, null, {}, "", 0, [], { nonsense: true }]) {
      const options = buildRoomQualityOptions(
        resolveMediaQualityPlan({ feature: "live_host", flags: parseMediaQualityFlags(payload) })
      );
      expect(options).toEqual(LIVE_BASELINE_ROOM_OPTIONS);
    }
  });
});

/* -------------------------------------------------------------------------- */
/* WHAT ELITE ACTUALLY CHANGES                                                 */
/* -------------------------------------------------------------------------- */

describe("elite changes only what it is supposed to change", () => {
  const ELITE = {
    realtime_media_quality_v2_enabled: true,
    realtime_media_quality_v2_qa_only: false,
    audio_quality_profile: "elite",
    video_quality_profile: "elite",
    live_elite_audio_enabled: true,
    live_elite_video_enabled: true,
    live_publisher_quality_enabled: true,
    call_elite_audio_enabled: true,
    video_call_elite_quality_enabled: true
  };

  const elite = (feature: MediaFeature) =>
    buildRoomQualityOptions(
      resolveMediaQualityPlan({ feature, flags: parseMediaQualityFlags(ELITE) })
    );

  it("keeps adaptiveStream and dynacast on", () => {
    for (const feature of ["audio_call", "live_host"] as const) {
      expect(elite(feature).adaptiveStream).toBe(true);
      expect(elite(feature).dynacast).toBe(true);
    }
  });

  it("keeps every audio safety setting", () => {
    for (const feature of ["audio_call", "video_call", "live_host", "live_guest"] as const) {
      const options = elite(feature);
      expect(options.audioCaptureDefaults.echoCancellation).toBe(true);
      expect(options.publishDefaults.red).toBe(true);
      expect(options.publishDefaults.stopMicTrackOnMute).toBe(false);
    }
  });

  it("names an audio bitrate where the baseline left it to chance", () => {
    expect(elite("audio_call").publishDefaults.audioBitrate).toBe(40_000);
  });

  it("raises the live host to 1080p", () => {
    const options = elite("live_host");
    expect(options.videoCaptureDefaults?.resolution.width).toBe(1080);
    expect(options.videoCaptureDefaults?.resolution.height).toBe(1920);
    expect(options.publishDefaults.degradationPreference).toBe("maintain-framerate");
  });

  it("does not let legacy Live quality flags move publishers off the verified audio baseline", () => {
    const legacyLiveFlags = {
      realtime_media_quality_v2_enabled: true,
      realtime_media_quality_v2_qa_only: false,
      audio_quality_profile: "elite",
      video_quality_profile: "elite",
      live_elite_audio_enabled: true,
      live_elite_video_enabled: true
    };
    const host = buildRoomQualityOptions(
      resolveMediaQualityPlan({ feature: "live_host", flags: parseMediaQualityFlags(legacyLiveFlags) })
    );
    const guest = buildRoomQualityOptions(
      resolveMediaQualityPlan({ feature: "live_guest", flags: parseMediaQualityFlags(legacyLiveFlags) })
    );
    expect(host).toEqual(LIVE_BASELINE_ROOM_OPTIONS);
    expect(guest).toEqual(LIVE_BASELINE_ROOM_OPTIONS);
  });

  it("gives a video call the capture configuration it never had", () => {
    // This is the real gap in the baseline: video calls passed no options at
    // all, so resolution and bitrate were whatever LiveKit chose.
    const options = elite("video_call");
    expect(options.videoCaptureDefaults).toBeDefined();
    expect(options.publishDefaults.videoEncoding).toBeDefined();
  });

  it("still gives a viewer nothing to publish with", () => {
    const options = elite("live_viewer");
    expect(options.videoCaptureDefaults).toBeUndefined();
    expect(options.publishDefaults.videoEncoding).toBeUndefined();
  });
});

/* -------------------------------------------------------------------------- */
/* THE ADAPTERS ARE ACTUALLY WIRED                                             */
/* -------------------------------------------------------------------------- */

describe("both adapters build their Room from the policy", () => {
  const callSource = read(CALL_ADAPTER);
  const liveSource = read(LIVE_ADAPTER);

  it("constructs the Room from buildRoomQualityOptions in both adapters", () => {
    expect(callSource).toContain("new livekitClient.Room(buildRoomQualityOptions(");
    expect(liveSource).toContain("new livekitClient.Room(buildRoomQualityOptions(");
  });

  it("leaves no hand-written Room option literal behind", () => {
    // A second literal would be a second source of truth, and it is precisely
    // the one that would not get updated.
    for (const source of [callSource, liveSource]) {
      expect(source).not.toMatch(/new livekitClient\.Room\(\{/);
    }
  });

  it("resolves the plan exactly once per adapter", () => {
    for (const source of [callSource, liveSource]) {
      const occurrences = source.split("resolveMediaQualityPlan(").length - 1;
      expect(occurrences).toBe(1);
    }
  });
});

/* -------------------------------------------------------------------------- */
/* THE AUDIO FOUNDATION IS UNTOUCHED                                           */
/* -------------------------------------------------------------------------- */

describe("the wiring did not disturb the audio foundation", () => {
  const callSource = read(CALL_ADAPTER);
  const liveSource = read(LIVE_ADAPTER);

  it("still registers globals with autoConfigureAudioSession off", () => {
    expect(callSource).toContain("registerGlobals({ autoConfigureAudioSession: false })");
    expect(liveSource).toContain("autoConfigureAudioSession: false");
  });

  it("still acquires an audio lease before constructing the Room", () => {
    for (const source of [callSource, liveSource]) {
      const lease = source.indexOf("activateRealtimeAudioSession");
      const room = source.indexOf("new livekitClient.Room(");
      expect(lease).toBeGreaterThan(-1);
      expect(lease).toBeLessThan(room);
    }
  });

  it("still publishes the microphone through the one publisher", () => {
    expect(callSource).toContain("publishRealtimeMicrophone");
    expect(liveSource).toContain("publishLiveMicrophone");
  });

  it("introduced no second microphone route", () => {
    for (const source of [callSource, liveSource]) {
      expect(source).not.toContain("createLocalAudioTrack");
      expect(source).not.toContain("getUserMedia");
      expect(source).not.toContain("mediaDevices.getUserMedia");
    }
  });

  it("still enables the camera only after audio is confirmed", () => {
    // The shared call-grade coordinator returns early on audioTrackCount <= 0,
    // before it reaches the camera. Both adapters are locked to this owner by
    // realtimeAudioArchitecture.test.ts.
    const body = read(PUBLISHER_MEDIA);
    const guard = body.indexOf("if (audioTrackCount <= 0 || !options.video) return audioTrackCount;");
    const camera = body.indexOf("await options.enableCamera()");
    expect(guard).toBeGreaterThan(-1);
    expect(guard).toBeLessThan(camera);
  });

  it("keeps a bare setCameraEnabled(true) for the stable video-call path", () => {
    expect(callSource).toContain("await room.localParticipant.setCameraEnabled(true);");
  });

  it("never lets the quality layer change participant permissions", () => {
    for (const source of [callSource, liveSource]) {
      expect(source).not.toContain("setPermissions");
      expect(source).not.toContain("updateParticipant(");
    }
  });
});

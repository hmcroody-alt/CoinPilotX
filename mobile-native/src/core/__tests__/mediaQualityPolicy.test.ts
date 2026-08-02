/**
 * The media quality policy, enforced.
 *
 * The mission that produced this layer had one non-negotiable condition: the
 * verified audio path must survive it. That condition is not satisfied by a
 * careful implementation — it is satisfied by tests that fail when the
 * implementation stops being careful.
 *
 * The load-bearing test in this file is "stable reproduces the baseline". It
 * does not compare the resolver against a constant defined next to it, which
 * would only prove the file is self-consistent.
 *
 * Until the adapters were wired, the independent source WAS the adapters: the
 * literals still sat inside `new Room({...})`. Wiring deliberately removed
 * them — having one builder is the entire point — so the independent source
 * moved to where it always belonged: the tagged commit
 * `realtime-audio-stable-v1`, whose audio was physically confirmed working. A
 * constant cannot be quietly edited into agreement with a commit that is
 * already history.
 *
 * If that commit is unreachable (shallow CI clone, exported tree) the
 * historical cross-check reports itself as skipped rather than passing silently,
 * and the frozen constants are still asserted.
 */
import { execFileSync } from "child_process";
import fs from "fs";
import path from "path";

import {
  BASELINE_AUDIO_CAPTURE,
  BASELINE_AUDIO_PUBLISH,
  BASELINE_LIVE_VIDEO_CAPTURE,
  BASELINE_LIVE_VIDEO_PUBLISH,
  isMediaQualityV2Active,
  moreConservative,
  normalizeContentMode,
  normalizeMediaQualityFlag,
  normalizeProfileName,
  publishesVideo,
  resolveContentMode,
  resolveMediaQualityPlan,
  type MediaFeature,
  type MediaQualityFlagSource
} from "../mediaQualityPolicy";
import { NEUTRAL_MEDIA_CONDITIONS } from "../mediaAdaptationController";
import {
  DEFAULT_MEDIA_QUALITY_FLAGS,
  parseMediaQualityFlags
} from "../mediaQualityFlags";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..", "..");
const NATIVE_SRC = path.join(REPO_ROOT, "mobile-native", "src");
const CALL_ADAPTER = path.join(NATIVE_SRC, "calls", "useNativeCallRoom.ts");
const LIVE_ADAPTER = path.join(NATIVE_SRC, "live", "useLiveBroadcastRoom.ts");
const ENGINE = path.join(NATIVE_SRC, "core", "realtimeAudioEngine.ts");

const ALL_FEATURES: MediaFeature[] = [
  "audio_call",
  "video_call",
  "live_host",
  "live_guest",
  "live_viewer"
];

/** Every flag on, elite everywhere. The most permissive input the resolver accepts. */
const MAX_FLAGS: MediaQualityFlagSource = {
  realtimeMediaQualityV2Enabled: true,
  realtimeMediaQualityV2QaOnly: false,
  audioQualityProfile: "elite",
  videoQualityProfile: "elite",
  liveEliteVideoEnabled: true,
  liveEliteAudioEnabled: true,
  livePublisherQualityEnabled: true,
  callEliteAudioEnabled: true,
  videoCallEliteQualityEnabled: true,
  qaCohort: true
};

function read(file: string): string {
  return fs.readFileSync(file, "utf8");
}

/**
 * Strip block and line comments.
 *
 * The forbidden-API scans below check what the code can DO, not what the
 * comments discuss. These two modules explain at length why they must never
 * call setMicrophoneEnabled — naming the hazard is the point of the comment,
 * and a test that punished the explanation would push people toward code with
 * no explanation at all.
 */
function code(file: string): string {
  return read(file)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

/** The tag created when the audio path was physically confirmed working. */
const BASELINE_REF = "realtime-audio-stable-v1";

/**
 * A file as it stood at the verified baseline commit, or null if that commit is
 * not in this clone. Null is reported, never treated as agreement.
 */
function baselineSource(repoRelativePath: string): string | null {
  try {
    return execFileSync("git", ["show", `${BASELINE_REF}:${repoRelativePath}`], {
      cwd: REPO_ROOT,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"]
    });
  } catch {
    return null;
  }
}

const BASELINE_ADAPTERS = [
  "mobile-native/src/calls/useNativeCallRoom.ts",
  "mobile-native/src/live/useLiveBroadcastRoom.ts"
];

const BASELINE_REACHABLE = baselineSource(BASELINE_ADAPTERS[0]) !== null;

/* -------------------------------------------------------------------------- */
/* GATE 1: STABLE IS THE VERIFIED BASELINE                                     */
/* -------------------------------------------------------------------------- */

describe("stable reproduces the verified baseline", () => {
  it("freezes the audio capture settings the verified baseline used", () => {
    expect(BASELINE_AUDIO_CAPTURE).toEqual({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true
    });
  });

  it("freezes the audio publish settings the verified baseline used", () => {
    expect(BASELINE_AUDIO_PUBLISH).toEqual({
      dtx: true,
      red: true,
      stopMicTrackOnMute: false
    });
  });

  it("agrees with the audio literals in the tagged baseline commit itself", () => {
    if (!BASELINE_REACHABLE) {
      // Loud, not silent: this clone cannot see the tag, so the strongest check
      // in the file did not run. The frozen constants above still did.
      console.warn(
        `[mediaQualityPolicy] ${BASELINE_REF} not reachable; historical cross-check skipped.`
      );
      return;
    }
    for (const file of BASELINE_ADAPTERS) {
      const source = baselineSource(file) as string;
      expect(source).toContain("echoCancellation: true");
      expect(source).toContain("noiseSuppression: true");
      expect(source).toContain("autoGainControl: true");
      expect(source).toContain("dtx: true");
      expect(source).toContain("red: true");
      expect(source).toContain("stopMicTrackOnMute: false");
      expect(source).toContain("adaptiveStream: true");
      expect(source).toContain("dynacast: true");
    }
  });

  it("still routes both adapters through the single builder", () => {
    // The literals left the adapters on purpose. What must remain true is that
    // neither adapter has grown a second, hand-written source of truth.
    for (const file of [CALL_ADAPTER, LIVE_ADAPTER]) {
      const source = code(file);
      expect(source).toContain("new livekitClient.Room(buildRoomQualityOptions(");
      expect(source).not.toMatch(/new livekitClient\.Room\(\{/);
    }
  });

  it("matches the live video literals still present in the engine", () => {
    const source = read(ENGINE);
    expect(source).toContain("width: 720");
    expect(source).toContain("height: 1280");
    expect(source).toContain("maxBitrate: 2_300_000");
    expect(BASELINE_LIVE_VIDEO_CAPTURE.resolution.width).toBe(720);
    expect(BASELINE_LIVE_VIDEO_CAPTURE.resolution.height).toBe(1280);
    expect(BASELINE_LIVE_VIDEO_PUBLISH.videoEncoding.maxBitrate).toBe(2_300_000);
  });

  it("returns exactly the baseline objects for every feature when V2 is off", () => {
    for (const feature of ALL_FEATURES) {
      const plan = resolveMediaQualityPlan({ feature });
      expect(plan.profile).toBe("stable");
      expect(plan.audioCaptureDefaults).toEqual(BASELINE_AUDIO_CAPTURE);
      expect(plan.audioPublishDefaults).toEqual(BASELINE_AUDIO_PUBLISH);
      expect(plan.reasons).toContain("v2_disabled");
    }
  });

  it("gives a stable video call NO capture or publish options, as the baseline does", () => {
    // The baseline calls setCameraEnabled(true) with no arguments. Returning the
    // livestream's options here would be a behaviour change labelled "stable".
    const plan = resolveMediaQualityPlan({ feature: "video_call" });
    expect(plan.videoCaptureDefaults).toBeUndefined();
    expect(plan.videoPublishDefaults).toBeUndefined();
    expect(read(CALL_ADAPTER)).toContain("setCameraEnabled(true)");
  });

  it("gives a stable live host exactly the baseline live video options", () => {
    const plan = resolveMediaQualityPlan({ feature: "live_host" });
    expect(plan.videoCaptureDefaults).toEqual(BASELINE_LIVE_VIDEO_CAPTURE);
    expect(plan.videoPublishDefaults?.videoEncoding).toEqual(
      BASELINE_LIVE_VIDEO_PUBLISH.videoEncoding
    );
    expect(plan.videoPublishDefaults?.simulcast).toBe(true);
    // No degradationPreference at the baseline: it was not set, so stable must
    // not set it either.
    expect(plan.videoPublishDefaults?.degradationPreference).toBeUndefined();
  });

  it("cannot be mutated through a returned plan", () => {
    const plan = resolveMediaQualityPlan({ feature: "audio_call" });
    plan.audioCaptureDefaults.noiseSuppression = false;
    const fresh = resolveMediaQualityPlan({ feature: "audio_call" });
    expect(fresh.audioCaptureDefaults.noiseSuppression).toBe(true);
    expect(BASELINE_AUDIO_CAPTURE.noiseSuppression).toBe(true);
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 2: THE KILL SWITCH                                                     */
/* -------------------------------------------------------------------------- */

describe("the kill switch", () => {
  it("restores stable immediately when the master flag goes false", () => {
    const elite = resolveMediaQualityPlan({ feature: "live_host", flags: MAX_FLAGS });
    expect(elite.profile).toBe("elite");

    const killed = resolveMediaQualityPlan({
      feature: "live_host",
      flags: { ...MAX_FLAGS, realtimeMediaQualityV2Enabled: false }
    });
    expect(killed.profile).toBe("stable");
    expect(killed.audioCaptureDefaults).toEqual(BASELINE_AUDIO_CAPTURE);
    expect(killed.audioPublishDefaults).toEqual(BASELINE_AUDIO_PUBLISH);
    expect(killed.videoCaptureDefaults).toEqual(BASELINE_LIVE_VIDEO_CAPTURE);
  });

  it("consults no conditions once V2 is off", () => {
    // A disabled rollout that still lets thermal state change the configuration
    // is not disabled. Stable under a burning phone must equal stable under a
    // cold one.
    const hot = resolveMediaQualityPlan({
      feature: "live_host",
      flags: { realtimeMediaQualityV2Enabled: false },
      conditions: { ...NEUTRAL_MEDIA_CONDITIONS, thermalState: "critical", networkTier: "weak" }
    });
    const cold = resolveMediaQualityPlan({ feature: "live_host" });
    expect(hot.audioCaptureDefaults).toEqual(cold.audioCaptureDefaults);
    expect(hot.audioPublishDefaults).toEqual(cold.audioPublishDefaults);
    expect(hot.videoPublishDefaults).toEqual(cold.videoPublishDefaults);
  });

  it("defaults every flag off", () => {
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.realtimeMediaQualityV2Enabled).toBe(false);
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.liveEliteAudioEnabled).toBe(false);
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.liveEliteVideoEnabled).toBe(false);
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.livePublisherQualityEnabled).toBe(false);
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.callEliteAudioEnabled).toBe(false);
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.videoCallEliteQualityEnabled).toBe(false);
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.audioQualityProfile).toBe("stable");
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.videoQualityProfile).toBe("stable");
    // QA-only defaults ON: a half-written rollout config scopes to QA.
    expect(DEFAULT_MEDIA_QUALITY_FLAGS.realtimeMediaQualityV2QaOnly).toBe(true);
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 3: FLAG NORMALISATION IS STRICT                                        */
/* -------------------------------------------------------------------------- */

describe("flag normalisation", () => {
  it.each([["true"], [1], ["1"], ["yes"], [{}], [[]], [null], [undefined], [0], ["false"]])(
    "treats %p as off",
    (raw) => {
      expect(normalizeMediaQualityFlag(raw)).toBe(false);
    }
  );

  it("accepts only a literal true", () => {
    expect(normalizeMediaQualityFlag(true)).toBe(true);
  });

  it.each([["ELITE"], ["premium"], ["max"], [""], [null], [3]])(
    "resolves unknown profile %p to stable",
    (raw) => {
      expect(normalizeProfileName(raw)).toBe("stable");
    }
  );

  it("resolves unknown content mode to auto", () => {
    expect(normalizeContentMode("podcast")).toBe("auto");
    expect(normalizeContentMode(undefined)).toBe("auto");
  });

  it("survives a malformed server payload without enabling anything", () => {
    for (const payload of [null, undefined, "enabled", 42, [], { realtimeMediaQualityV2Enabled: "true" }]) {
      const parsed = parseMediaQualityFlags(payload);
      expect(isMediaQualityV2Active(parsed)).toBe(false);
      expect(resolveMediaQualityPlan({ feature: "live_host", flags: parsed }).profile).toBe("stable");
    }
  });

  it("keeps a QA-only rollout inside the QA cohort", () => {
    const qaOnly = { realtimeMediaQualityV2Enabled: true, realtimeMediaQualityV2QaOnly: true };
    expect(isMediaQualityV2Active({ ...qaOnly, qaCohort: false })).toBe(false);
    expect(isMediaQualityV2Active({ ...qaOnly, qaCohort: true })).toBe(true);
  });

  it("does not upgrade a feature that has not opted in", () => {
    const masterOnly: MediaQualityFlagSource = {
      realtimeMediaQualityV2Enabled: true,
      realtimeMediaQualityV2QaOnly: false,
      audioQualityProfile: "elite",
      videoQualityProfile: "elite"
    };
    for (const feature of ALL_FEATURES) {
      const plan = resolveMediaQualityPlan({ feature, flags: masterOnly });
      expect(plan.profile).toBe("stable");
      expect(plan.reasons).toContain("feature_not_opted_in");
    }
  });

  it("enabling calls cannot change livestream quality", () => {
    const callsOnly: MediaQualityFlagSource = {
      ...MAX_FLAGS,
      liveEliteAudioEnabled: false,
      liveEliteVideoEnabled: false,
      livePublisherQualityEnabled: false
    };
    expect(resolveMediaQualityPlan({ feature: "audio_call", flags: callsOnly }).profile).toBe("elite");
    expect(resolveMediaQualityPlan({ feature: "live_host", flags: callsOnly }).profile).toBe("stable");
  });

  it("keeps Live publishers on the verified baseline unless the publisher gate is explicit", () => {
    const legacyLiveFlags: MediaQualityFlagSource = {
      ...MAX_FLAGS,
      livePublisherQualityEnabled: false
    };
    for (const feature of ["live_host", "live_guest"] as const) {
      const plan = resolveMediaQualityPlan({ feature, flags: legacyLiveFlags });
      expect(plan.profile).toBe("stable");
      expect(plan.reasons).toContain("feature_not_opted_in");
      expect(plan.audioCaptureDefaults).toEqual(BASELINE_AUDIO_CAPTURE);
      expect(plan.audioPublishDefaults).toEqual(BASELINE_AUDIO_PUBLISH);
      expect(plan.videoCaptureDefaults).toEqual(BASELINE_LIVE_VIDEO_CAPTURE);
      expect(plan.videoPublishDefaults).toEqual(BASELINE_LIVE_VIDEO_PUBLISH);
    }
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 4: CONDITIONS CLAMP, NEVER RAISE                                       */
/* -------------------------------------------------------------------------- */

describe("condition guards are one-way", () => {
  const RANK = { resilient: 0, stable: 1, balanced: 2, elite: 3 } as const;

  it("never produces a profile above the one requested", () => {
    const thermals = ["nominal", "fair", "serious", "critical"] as const;
    const networks = ["good", "fair", "weak"] as const;
    const devices = ["high", "mid", "low"] as const;
    for (const thermalState of thermals) {
      for (const networkTier of networks) {
        for (const deviceTier of devices) {
          for (const batteryLevel of [1, 0.5, 0.25, 0.1]) {
            const plan = resolveMediaQualityPlan({
              feature: "live_host",
              flags: { ...MAX_FLAGS, audioQualityProfile: "balanced", videoQualityProfile: "balanced" },
              conditions: {
                ...NEUTRAL_MEDIA_CONDITIONS,
                thermalState,
                networkTier,
                deviceTier,
                batteryLevel
              }
            });
            expect(RANK[plan.profile]).toBeLessThanOrEqual(RANK.balanced);
          }
        }
      }
    }
  });

  it("clamps to resilient on a critical thermal state", () => {
    const plan = resolveMediaQualityPlan({
      feature: "live_host",
      flags: MAX_FLAGS,
      conditions: { ...NEUTRAL_MEDIA_CONDITIONS, thermalState: "critical" }
    });
    expect(plan.profile).toBe("resilient");
    expect(plan.requestedProfile).toBe("elite");
    expect(plan.reasons).toContain("guard_thermal");
  });

  it("keeps audio processing intact under every guard", () => {
    // Thermal, network and battery pressure may cost pixels. They may never
    // cost echo cancellation, and they may never turn the microphone off.
    for (const thermalState of ["fair", "serious", "critical"] as const) {
      const plan = resolveMediaQualityPlan({
        feature: "audio_call",
        flags: MAX_FLAGS,
        conditions: { ...NEUTRAL_MEDIA_CONDITIONS, thermalState, networkTier: "weak", batteryLevel: 0.05 }
      });
      expect(plan.audioCaptureDefaults.echoCancellation).toBe(true);
      expect(plan.audioPublishDefaults.stopMicTrackOnMute).toBe(false);
      expect(plan.audioPublishDefaults.red).toBe(true);
    }
  });

  it("takes the more conservative of the audio and video profiles", () => {
    expect(moreConservative("elite", "resilient")).toBe("resilient");
    expect(moreConservative("balanced", "elite")).toBe("balanced");
    const plan = resolveMediaQualityPlan({
      feature: "live_host",
      flags: { ...MAX_FLAGS, videoQualityProfile: "resilient" }
    });
    expect(plan.profile).toBe("resilient");
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 5: AUDIO OWNERSHIP CANNOT BE BYPASSED                                  */
/* -------------------------------------------------------------------------- */

describe("the policy layer cannot become a second media owner", () => {
  const policySource = code(path.join(NATIVE_SRC, "core", "mediaQualityPolicy.ts"));
  const adaptationSource = code(path.join(NATIVE_SRC, "core", "mediaAdaptationController.ts"));

  const FORBIDDEN = [
    "setMicrophoneEnabled",
    "setCameraEnabled",
    "localParticipant",
    "publishTrack",
    "unpublishTrack",
    "setAudioModeAsync",
    "setAppleAudioConfiguration",
    "AudioSession",
    "registerGlobals",
    "room.connect",
    "createLocalAudioTrack",
    "createLocalVideoTrack"
  ];

  it.each(FORBIDDEN)("never references %s", (marker) => {
    expect(policySource).not.toContain(marker);
    expect(adaptationSource).not.toContain(marker);
  });

  it("imports no LiveKit module and no audio engine", () => {
    for (const source of [policySource, adaptationSource]) {
      expect(source).not.toMatch(/from\s+["']@livekit/);
      expect(source).not.toMatch(/from\s+["']livekit-client/);
      expect(source).not.toMatch(/from\s+["']expo-av/);
      expect(source).not.toMatch(/from\s+["']\.\/realtimeAudioEngine/);
      expect(source).not.toMatch(/from\s+["']\.\/realtimeMicrophonePublisher/);
    }
  });

  it("is deterministic: no clocks, no randomness", () => {
    for (const source of [policySource, adaptationSource]) {
      expect(source).not.toContain("Math.random");
      expect(source).not.toContain("setTimeout");
      expect(source).not.toContain("setInterval");
    }
    // mediaQualityPolicy must not read a clock at all. The adaptation reducer
    // receives `nowMs` from its caller for the same reason.
    expect(policySource).not.toContain("Date.now");
    expect(adaptationSource).not.toContain("Date.now");
  });

  it("returns identical plans for identical inputs", () => {
    const input = {
      feature: "live_host" as const,
      flags: MAX_FLAGS,
      conditions: { ...NEUTRAL_MEDIA_CONDITIONS, networkTier: "fair" as const }
    };
    expect(resolveMediaQualityPlan(input)).toEqual(resolveMediaQualityPlan(input));
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 6: NO ZOOM, EVER                                                       */
/* -------------------------------------------------------------------------- */

describe("the host camera full-zoom regression cannot return", () => {
  const policySource = code(path.join(NATIVE_SRC, "core", "mediaQualityPolicy.ts"));

  it.each(["zoom", "applyConstraints", "getCapabilities", "deviceId", "cropFactor"])(
    "the policy layer has no way to express %s",
    (marker) => {
      expect(policySource).not.toContain(marker);
    }
  );

  it("emits only facingMode, frameRate and resolution in every capture object", () => {
    for (const profile of ["balanced", "elite", "resilient"] as const) {
      for (const feature of ["video_call", "live_host", "live_guest"] as const) {
        const plan = resolveMediaQualityPlan({
          feature,
          flags: { ...MAX_FLAGS, audioQualityProfile: profile, videoQualityProfile: profile }
        });
        if (!plan.videoCaptureDefaults) continue;
        expect(Object.keys(plan.videoCaptureDefaults).sort()).toEqual([
          "facingMode",
          "frameRate",
          "resolution"
        ]);
        expect(plan.videoCaptureDefaults.facingMode).toBe("user");
      }
    }
  });

  it("keeps a 9:16 portrait aspect at every tier, so nothing is cropped to fit", () => {
    for (const profile of ["balanced", "elite", "resilient"] as const) {
      const plan = resolveMediaQualityPlan({
        feature: "live_host",
        flags: { ...MAX_FLAGS, audioQualityProfile: profile, videoQualityProfile: profile }
      });
      const resolution = plan.videoCaptureDefaults!.resolution;
      expect(resolution.width / resolution.height).toBeCloseTo(9 / 16, 2);
      expect(resolution.aspectRatio).toBeCloseTo(9 / 16, 5);
    }
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 7: VIEWERS AND NON-PUBLISHERS                                          */
/* -------------------------------------------------------------------------- */

describe("features that do not publish never receive publish configuration", () => {
  it("a live viewer gets no camera options under any flag combination", () => {
    for (const profile of ["stable", "balanced", "elite", "resilient"] as const) {
      const plan = resolveMediaQualityPlan({
        feature: "live_viewer",
        flags: { ...MAX_FLAGS, audioQualityProfile: profile, videoQualityProfile: profile }
      });
      expect(plan.videoCaptureDefaults).toBeUndefined();
      expect(plan.videoPublishDefaults).toBeUndefined();
    }
  });

  it("an audio call gets no camera options under any flag combination", () => {
    for (const profile of ["stable", "balanced", "elite", "resilient"] as const) {
      const plan = resolveMediaQualityPlan({
        feature: "audio_call",
        flags: { ...MAX_FLAGS, audioQualityProfile: profile, videoQualityProfile: profile }
      });
      expect(plan.videoCaptureDefaults).toBeUndefined();
      expect(plan.videoPublishDefaults).toBeUndefined();
    }
  });

  it("classifies exactly the three publishing surfaces", () => {
    expect(ALL_FEATURES.filter(publishesVideo)).toEqual(["video_call", "live_host", "live_guest"]);
  });

  it("never requests voice isolation for a viewer", () => {
    const plan = resolveMediaQualityPlan({
      feature: "live_viewer",
      flags: MAX_FLAGS,
      conditions: { ...NEUTRAL_MEDIA_CONDITIONS, supportsVoiceIsolation: true }
    });
    expect(plan.audioCaptureDefaults.voiceIsolation).toBeUndefined();
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 8: NATURAL SOUND                                                       */
/* -------------------------------------------------------------------------- */

describe("audio stays natural", () => {
  it("keeps echo cancellation on in every profile, mode and feature", () => {
    for (const feature of ALL_FEATURES) {
      for (const mode of ["speech", "music", "auto"] as const) {
        for (const profile of ["stable", "balanced", "elite", "resilient"] as const) {
          const plan = resolveMediaQualityPlan({
            feature,
            flags: {
              ...MAX_FLAGS,
              audioQualityProfile: profile,
              videoQualityProfile: profile,
              mediaContentMode: mode
            }
          });
          expect(plan.audioCaptureDefaults.echoCancellation).toBe(true);
        }
      }
    }
  });

  it("requests voice isolation only on elite, only where the device supports it", () => {
    const supported = { ...NEUTRAL_MEDIA_CONDITIONS, supportsVoiceIsolation: true };
    const eliteOn = resolveMediaQualityPlan({
      feature: "audio_call",
      flags: MAX_FLAGS,
      conditions: supported
    });
    expect(eliteOn.audioCaptureDefaults.voiceIsolation).toBe(true);

    const unsupported = resolveMediaQualityPlan({ feature: "audio_call", flags: MAX_FLAGS });
    expect(unsupported.audioCaptureDefaults.voiceIsolation).toBeUndefined();

    const balanced = resolveMediaQualityPlan({
      feature: "audio_call",
      flags: { ...MAX_FLAGS, audioQualityProfile: "balanced", videoQualityProfile: "balanced" },
      conditions: supported
    });
    expect(balanced.audioCaptureDefaults.voiceIsolation).toBeUndefined();
  });

  it("resolves auto to speech, always — automatic detection cannot oscillate if it cannot switch", () => {
    for (const feature of ALL_FEATURES) {
      expect(resolveContentMode("auto", feature)).toBe("speech");
      expect(resolveContentMode(undefined, feature)).toBe("speech");
      expect(resolveContentMode("garbage", feature)).toBe("speech");
    }
  });

  it("never puts a two-way call into music mode", () => {
    expect(resolveContentMode("music", "audio_call")).toBe("speech");
    expect(resolveContentMode("music", "video_call")).toBe("speech");
    expect(resolveContentMode("music", "live_host")).toBe("music");
  });

  it("relaxes only noise suppression and gain in music mode, never echo cancellation", () => {
    const plan = resolveMediaQualityPlan({
      feature: "live_host",
      flags: { ...MAX_FLAGS, mediaContentMode: "music" }
    });
    expect(plan.contentMode).toBe("music");
    expect(plan.audioCaptureDefaults.echoCancellation).toBe(true);
    expect(plan.audioCaptureDefaults.noiseSuppression).toBe(false);
    expect(plan.audioCaptureDefaults.autoGainControl).toBe(false);
    expect(plan.audioPublishDefaults.dtx).toBe(false);
  });

  it("keeps redundant encoding on in every non-stable profile", () => {
    for (const profile of ["balanced", "elite", "resilient"] as const) {
      const plan = resolveMediaQualityPlan({
        feature: "audio_call",
        flags: { ...MAX_FLAGS, audioQualityProfile: profile, videoQualityProfile: profile }
      });
      expect(plan.audioPublishDefaults.red).toBe(true);
      expect(plan.audioPublishDefaults.stopMicTrackOnMute).toBe(false);
    }
  });

  it("raises the audio bitrate above the unset baseline without being extravagant", () => {
    const elite = resolveMediaQualityPlan({ feature: "audio_call", flags: MAX_FLAGS });
    expect(elite.audioPublishDefaults.audioBitrate).toBe(40_000);

    const resilient = resolveMediaQualityPlan({
      feature: "audio_call",
      flags: { ...MAX_FLAGS, audioQualityProfile: "resilient", videoQualityProfile: "resilient" }
    });
    expect(resilient.audioPublishDefaults.audioBitrate).toBe(24_000);
    expect(resilient.audioPublishDefaults.audioBitrate!).toBeLessThan(
      elite.audioPublishDefaults.audioBitrate!
    );
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 9: VIDEO BUDGETS                                                       */
/* -------------------------------------------------------------------------- */

describe("video budgets", () => {
  it("caps a guest below the host at the same tier", () => {
    const host = resolveMediaQualityPlan({ feature: "live_host", flags: MAX_FLAGS });
    const guest = resolveMediaQualityPlan({ feature: "live_guest", flags: MAX_FLAGS });
    expect(guest.videoPublishDefaults!.videoEncoding.maxBitrate).toBeLessThan(
      host.videoPublishDefaults!.videoEncoding.maxBitrate
    );
  });

  it("prefers frame rate over resolution under encoder pressure", () => {
    for (const feature of ["video_call", "live_host", "live_guest"] as const) {
      const plan = resolveMediaQualityPlan({ feature, flags: MAX_FLAGS });
      expect(plan.videoPublishDefaults!.degradationPreference).toBe("maintain-framerate");
    }
  });

  it("orders the tiers monotonically", () => {
    const bitrates = (["resilient", "balanced", "elite"] as const).map(
      (profile) =>
        resolveMediaQualityPlan({
          feature: "live_host",
          flags: { ...MAX_FLAGS, audioQualityProfile: profile, videoQualityProfile: profile }
        }).videoPublishDefaults!.videoEncoding.maxBitrate
    );
    expect(bitrates[0]).toBeLessThan(bitrates[1]);
    expect(bitrates[1]).toBeLessThan(bitrates[2]);
  });

  it("keeps simulcast on everywhere, so viewers can be served a layer they can afford", () => {
    for (const profile of ["stable", "balanced", "elite", "resilient"] as const) {
      const plan = resolveMediaQualityPlan({
        feature: "live_host",
        flags: { ...MAX_FLAGS, audioQualityProfile: profile, videoQualityProfile: profile }
      });
      expect(plan.videoPublishDefaults!.simulcast).toBe(true);
    }
  });
});

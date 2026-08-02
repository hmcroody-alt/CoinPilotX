/**
 * The governed media quality policy layer.
 *
 * WHAT THIS IS
 * A pure resolver. Given a feature (audio call, video call, live host, live
 * guest, live viewer), a set of server flags, and observed device/network
 * conditions, it returns the LiveKit configuration objects that the two room
 * adapters already pass to `new Room({...})`. It computes; it does not act.
 *
 * WHY IT IS PURE
 * The verified audio foundation depends on exactly one module touching the
 * device audio session and exactly one module publishing the microphone. A
 * quality layer that acquired, published, muted, or reconfigured anything would
 * become a second decision-maker for media state, which is the failure the
 * audio hard-lock exists to prevent. So this module returns plain objects and
 * has no side effects, no imports of the audio engine, and no access to a Room.
 *
 * THE STABLE GUARANTEE
 * `stable` is not "a conservative profile". It is a byte-for-byte reproduction
 * of the configuration that was physically heard working and recorded in
 * reports/realtime_audio_verified_baseline.md. Every flag defaults off, so the
 * resolver returns `stable` unless a server explicitly opts a session in. The
 * test suite asserts deep equality between `stable` output and the literals
 * copied out of the adapters, so this guarantee cannot rot silently: if someone
 * "improves" the stable profile, the test fails.
 *
 * This is what makes the kill switch real. Turning V2 off does not put the app
 * into a nearby configuration; it puts it back into the exact one that was
 * validated by ear.
 */

import {
  NEUTRAL_MEDIA_CONDITIONS,
  type MediaConditionSnapshot
} from "./mediaAdaptationController";

export type MediaQualityProfileName = "stable" | "balanced" | "elite" | "resilient";

export type MediaFeature =
  | "audio_call"
  | "video_call"
  | "live_host"
  | "live_guest"
  | "live_viewer";

/** Speech vs music handling. See resolveContentMode for why `auto` is conservative. */
export type MediaContentMode = "speech" | "music" | "auto";

/**
 * Server-delivered flags. Shape mirrors liveAudioFlags.ts deliberately: the
 * decision is made entirely on the server and arrives on the token response the
 * client already fetches, so the kill switch takes effect on the next token
 * fetch with no app release. There is no local override, because a client-side
 * flag is not a kill switch.
 */
export type MediaQualityFlagSource = {
  realtimeMediaQualityV2Enabled?: unknown;
  realtimeMediaQualityV2QaOnly?: unknown;
  audioQualityProfile?: unknown;
  videoQualityProfile?: unknown;
  liveEliteVideoEnabled?: unknown;
  liveEliteAudioEnabled?: unknown;
  callEliteAudioEnabled?: unknown;
  videoCallEliteQualityEnabled?: unknown;
  mediaContentMode?: unknown;
  qaCohort?: unknown;
};

export type AudioCaptureConfig = {
  echoCancellation: boolean;
  noiseSuppression: boolean;
  autoGainControl: boolean;
  voiceIsolation?: boolean;
  channelCount?: number;
};

export type AudioPublishConfig = {
  dtx: boolean;
  red: boolean;
  stopMicTrackOnMute: boolean;
  audioBitrate?: number;
};

export type VideoResolutionConfig = {
  width: number;
  height: number;
  frameRate: number;
  aspectRatio: number;
};

export type VideoCaptureConfig = {
  facingMode: "user" | "environment";
  frameRate: number;
  resolution: VideoResolutionConfig;
};

export type VideoPublishConfig = {
  videoEncoding: {
    maxBitrate: number;
    maxFramerate: number;
    priority: "low" | "medium" | "high";
  };
  simulcast: boolean;
  degradationPreference?: "maintain-framerate" | "maintain-resolution" | "balanced";
};

export type MediaQualityPlan = {
  /** The profile actually applied, after every guard and clamp. */
  profile: MediaQualityProfileName;
  /** The profile the flags asked for, before guards. Differs when a guard fired. */
  requestedProfile: MediaQualityProfileName;
  feature: MediaFeature;
  contentMode: MediaContentMode;
  audioCaptureDefaults: AudioCaptureConfig;
  audioPublishDefaults: AudioPublishConfig;
  /** Undefined for features that never publish video (audio call, live viewer). */
  videoCaptureDefaults?: VideoCaptureConfig;
  videoPublishDefaults?: VideoPublishConfig;
  /**
   * Machine-readable, enum-only. Every entry explains one decision, so a
   * quality complaint from the field can be traced to the reason the session
   * ran the configuration it ran.
   */
  reasons: string[];
};

/* -------------------------------------------------------------------------- */
/* THE VERIFIED BASELINE                                                       */
/* -------------------------------------------------------------------------- */

/**
 * Copied verbatim from the two adapters at commit
 * ce03e160eaf4649a8e02bc3b609a3182ca9d3859, the commit whose audio was
 * physically heard working. These are frozen so that an accidental mutation
 * anywhere cannot silently redefine what "stable" means.
 */
export const BASELINE_AUDIO_CAPTURE: AudioCaptureConfig = Object.freeze({
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true
});

export const BASELINE_AUDIO_PUBLISH: AudioPublishConfig = Object.freeze({
  dtx: true,
  red: true,
  stopMicTrackOnMute: false
});

export const BASELINE_LIVE_VIDEO_RESOLUTION: VideoResolutionConfig = Object.freeze({
  width: 720,
  height: 1280,
  frameRate: 30,
  aspectRatio: 9 / 16
});

export const BASELINE_LIVE_VIDEO_CAPTURE: VideoCaptureConfig = Object.freeze({
  facingMode: "user",
  frameRate: 30,
  resolution: BASELINE_LIVE_VIDEO_RESOLUTION
});

export const BASELINE_LIVE_VIDEO_PUBLISH: VideoPublishConfig = Object.freeze({
  videoEncoding: Object.freeze({
    maxBitrate: 2_300_000,
    maxFramerate: 30,
    priority: "medium"
  }),
  simulcast: true
});

/* -------------------------------------------------------------------------- */
/* FLAG NORMALISATION                                                          */
/* -------------------------------------------------------------------------- */

const PROFILE_NAMES: MediaQualityProfileName[] = ["stable", "balanced", "elite", "resilient"];

/**
 * Strict `=== true`, matching normalizeLiveAudioV2Flag. A malformed, truncated,
 * or absent field must be fail-safe: "false", 0, "0", and "yes" all mean off.
 */
export function normalizeMediaQualityFlag(raw: unknown): boolean {
  return raw === true;
}

/** Anything not exactly one of the four known names resolves to `stable`. */
export function normalizeProfileName(raw: unknown): MediaQualityProfileName {
  return typeof raw === "string" && (PROFILE_NAMES as string[]).includes(raw)
    ? (raw as MediaQualityProfileName)
    : "stable";
}

export function normalizeContentMode(raw: unknown): MediaContentMode {
  return raw === "music" || raw === "speech" || raw === "auto" ? raw : "auto";
}

/**
 * V2 is live for this session only when the master switch is on AND, if the
 * server marked the rollout QA-only, this client is in the QA cohort. The
 * QA-only check is deliberately AND-ed rather than OR-ed: a rollout that is
 * QA-only but reaches everyone is not a rollout, it is an incident.
 */
export function isMediaQualityV2Active(source: MediaQualityFlagSource | null | undefined): boolean {
  if (!normalizeMediaQualityFlag(source?.realtimeMediaQualityV2Enabled)) return false;
  if (normalizeMediaQualityFlag(source?.realtimeMediaQualityV2QaOnly)) {
    return normalizeMediaQualityFlag(source?.qaCohort);
  }
  return true;
}

/** Per-feature elite opt-in. Master switch must already be on. */
function isFeatureEliteEnabled(
  feature: MediaFeature,
  source: MediaQualityFlagSource | null | undefined
): boolean {
  switch (feature) {
    case "audio_call":
      return normalizeMediaQualityFlag(source?.callEliteAudioEnabled);
    case "video_call":
      return normalizeMediaQualityFlag(source?.videoCallEliteQualityEnabled);
    case "live_host":
    case "live_guest":
      return (
        normalizeMediaQualityFlag(source?.liveEliteAudioEnabled) ||
        normalizeMediaQualityFlag(source?.liveEliteVideoEnabled)
      );
    case "live_viewer":
      return normalizeMediaQualityFlag(source?.liveEliteVideoEnabled);
    default:
      return false;
  }
}

/* -------------------------------------------------------------------------- */
/* AUDIO PROFILES                                                              */
/* -------------------------------------------------------------------------- */

/**
 * Audio bitrate ladder, in bits per second.
 *
 * The baseline sets no explicit audio bitrate, so LiveKit applies its own
 * default. Naming the number is the improvement: speech at 32 kbps Opus is
 * audibly fuller than the low-20s without costing meaningful bandwidth against
 * a video stream already budgeted at 2.3 Mbps. Music needs the wider band
 * because speech-tuned bitrates smear cymbals and reverb tails.
 *
 * These are ceilings, not targets. Opus is variable-rate and will use less.
 */
const AUDIO_BITRATE = {
  speechResilient: 24_000,
  speechBalanced: 32_000,
  speechElite: 40_000,
  musicBalanced: 64_000,
  musicElite: 96_000
} as const;

function audioCaptureFor(
  profile: MediaQualityProfileName,
  feature: MediaFeature,
  contentMode: MediaContentMode,
  conditions: MediaConditionSnapshot,
  reasons: string[]
): AudioCaptureConfig {
  if (profile === "stable") return { ...BASELINE_AUDIO_CAPTURE };

  // Music mode relaxes speech-specific processing. Noise suppression and AGC
  // are what destroy music: the suppressor treats sustained tones as noise and
  // the gain control pumps on every dynamic passage. Echo cancellation stays
  // on unconditionally — without it a host on speakerphone feeds their own
  // stream back into the room, which is worse than any fidelity gain.
  if (contentMode === "music") {
    reasons.push("audio_music_mode");
    return {
      echoCancellation: true,
      noiseSuppression: false,
      autoGainControl: false,
      channelCount: profile === "elite" ? 2 : 1
    };
  }

  const config: AudioCaptureConfig = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1
  };

  // Voice isolation is a platform capability, not a setting we can wish into
  // existence. It is requested only where the device reports support, and only
  // for elite — it is the single most likely source of "my voice sounds
  // processed" complaints, so it does not belong in a broad rollout.
  if (profile === "elite" && conditions.supportsVoiceIsolation && feature !== "live_viewer") {
    config.voiceIsolation = true;
    reasons.push("audio_voice_isolation_on");
  }

  return config;
}

function audioPublishFor(
  profile: MediaQualityProfileName,
  contentMode: MediaContentMode,
  reasons: string[]
): AudioPublishConfig {
  if (profile === "stable") return { ...BASELINE_AUDIO_PUBLISH };

  // RED (redundant encoding) stays on in every profile. It is the single
  // highest-value setting for speech on a lossy network: it costs bandwidth we
  // have and buys intelligibility we cannot recover any other way.
  const base: AudioPublishConfig = {
    dtx: true,
    red: true,
    stopMicTrackOnMute: false
  };

  if (contentMode === "music") {
    // DTX cuts transmission during "silence". Music has no silence, only quiet
    // passages, and DTX audibly chops their tails.
    base.dtx = false;
    base.audioBitrate = profile === "elite" ? AUDIO_BITRATE.musicElite : AUDIO_BITRATE.musicBalanced;
    reasons.push("audio_music_bitrate");
    return base;
  }

  base.audioBitrate =
    profile === "elite"
      ? AUDIO_BITRATE.speechElite
      : profile === "resilient"
        ? AUDIO_BITRATE.speechResilient
        : AUDIO_BITRATE.speechBalanced;
  reasons.push("audio_speech_bitrate");
  return base;
}

/* -------------------------------------------------------------------------- */
/* VIDEO PROFILES                                                              */
/* -------------------------------------------------------------------------- */

const VIDEO_LADDER: Record<
  "elite" | "balanced" | "resilient",
  { resolution: VideoResolutionConfig; maxBitrate: number }
> = {
  elite: {
    resolution: { width: 1080, height: 1920, frameRate: 30, aspectRatio: 9 / 16 },
    maxBitrate: 4_000_000
  },
  balanced: {
    resolution: { width: 720, height: 1280, frameRate: 30, aspectRatio: 9 / 16 },
    maxBitrate: 2_300_000
  },
  resilient: {
    resolution: { width: 480, height: 854, frameRate: 24, aspectRatio: 9 / 16 },
    maxBitrate: 900_000
  }
};

/** Features that publish a camera. Audio calls and viewers never do. */
export function publishesVideo(feature: MediaFeature): boolean {
  return feature === "video_call" || feature === "live_host" || feature === "live_guest";
}

function videoCaptureFor(
  profile: MediaQualityProfileName,
  feature: MediaFeature,
  reasons: string[]
): VideoCaptureConfig | undefined {
  if (!publishesVideo(feature)) return undefined;

  if (profile === "stable") {
    // A video call at the baseline passes NO capture options — the adapter
    // calls setCameraEnabled(true) bare and LiveKit picks. Reproducing the
    // baseline therefore means returning undefined here, not returning the
    // livestream's options. Handing a video call the live capture object would
    // be a behavior change wearing the word "stable".
    return feature === "video_call" ? undefined : { ...BASELINE_LIVE_VIDEO_CAPTURE };
  }

  const tier = profile === "elite" ? "elite" : profile === "resilient" ? "resilient" : "balanced";
  reasons.push(`video_capture_${tier}`);

  return {
    // facingMode only. No deviceId, no zoom, no applyConstraints — the host
    // camera full-zoom regression came from constraining the capture into a
    // narrower field of view, and the defence is that there is no code here
    // capable of expressing a zoom.
    facingMode: "user",
    frameRate: VIDEO_LADDER[tier].resolution.frameRate,
    resolution: { ...VIDEO_LADDER[tier].resolution }
  };
}

function videoPublishFor(
  profile: MediaQualityProfileName,
  feature: MediaFeature,
  reasons: string[]
): VideoPublishConfig | undefined {
  if (!publishesVideo(feature)) return undefined;

  if (profile === "stable") {
    return feature === "video_call"
      ? undefined
      : { videoEncoding: { ...BASELINE_LIVE_VIDEO_PUBLISH.videoEncoding }, simulcast: true };
  }

  const tier = profile === "elite" ? "elite" : profile === "resilient" ? "resilient" : "balanced";

  // A guest shares the room's uplink with the host and every other guest.
  // Giving each guest a host-sized budget is how a four-way Live turns into a
  // slideshow, so guests are capped one tier down.
  const budget = feature === "live_guest" ? Math.round(VIDEO_LADDER[tier].maxBitrate * 0.6) : VIDEO_LADDER[tier].maxBitrate;
  if (feature === "live_guest") reasons.push("video_guest_budget_shared");

  reasons.push(`video_publish_${tier}`);

  return {
    videoEncoding: {
      maxBitrate: budget,
      maxFramerate: VIDEO_LADDER[tier].resolution.frameRate,
      priority: feature === "live_host" ? "high" : "medium"
    },
    simulcast: true,
    // The mission's degradation order reduces bitrate and resolution before
    // frame rate. That is exactly maintain-framerate: under pressure WebRTC
    // sheds pixels and keeps motion smooth. The alternative, maintain-
    // resolution, produces the stuttering slideshow people describe as "laggy"
    // even when the picture is sharp.
    degradationPreference: "maintain-framerate"
  };
}

/* -------------------------------------------------------------------------- */
/* CONTENT MODE                                                                */
/* -------------------------------------------------------------------------- */

/**
 * `auto` resolves to speech, always.
 *
 * This is not a placeholder for unfinished detection. Automatic speech/music
 * switching mid-session changes echo cancellation and gain behaviour under a
 * live speaker, and any detector will be wrong sometimes — the failure mode is
 * a host whose voice audibly changes character mid-sentence, repeatedly. The
 * mission forbids oscillation, and the only detector that provably cannot
 * oscillate is one that does not switch. Music is therefore an explicit,
 * server-set decision for sessions known to be music sessions.
 */
export function resolveContentMode(
  raw: unknown,
  feature: MediaFeature
): MediaContentMode {
  const requested = normalizeContentMode(raw);
  if (requested === "music") {
    // Music mode relaxes noise suppression and AGC. On a two-way call that
    // means the far end hears the near end's room. Calls stay on speech.
    return feature === "audio_call" || feature === "video_call" ? "speech" : "music";
  }
  return "speech";
}

/* -------------------------------------------------------------------------- */
/* THE RESOLVER                                                                */
/* -------------------------------------------------------------------------- */

export type MediaQualityInput = {
  feature: MediaFeature;
  flags?: MediaQualityFlagSource | null;
  conditions?: MediaConditionSnapshot | null;
};

/**
 * Aliased rather than redeclared. Two literals both claiming to be "neutral
 * conditions" is two things to keep in sync, and the one that drifts is the one
 * the tests do not cover.
 */
export const NEUTRAL_CONDITIONS: MediaConditionSnapshot = NEUTRAL_MEDIA_CONDITIONS;

/**
 * The single entry point. Deterministic: same input, same plan, every time.
 */
export function resolveMediaQualityPlan(input: MediaQualityInput): MediaQualityPlan {
  const { feature } = input;
  const flags = input.flags ?? null;
  const conditions = input.conditions ?? NEUTRAL_CONDITIONS;
  const reasons: string[] = [];

  const active = isMediaQualityV2Active(flags);
  if (!active) {
    // The whole point of the kill switch. No conditions are consulted, no
    // guards run, nothing is clamped — the plan is the verified baseline.
    reasons.push("v2_disabled");
    return buildStablePlan(feature, reasons);
  }

  const audioRequested = normalizeProfileName(flags?.audioQualityProfile);
  const videoRequested = normalizeProfileName(flags?.videoQualityProfile);
  // One profile governs the session. Taking the more conservative of the two
  // avoids a state where audio believes it is resilient while video believes it
  // is elite, which is how contradictory adaptation decisions get made.
  let requested = moreConservative(audioRequested, videoRequested);

  if (requested !== "stable" && !isFeatureEliteEnabled(feature, flags)) {
    // The master switch alone does not upgrade a feature. Each surface is
    // opted in by its own flag so a livestream regression cannot be caused by
    // enabling calls.
    reasons.push("feature_not_opted_in");
    return buildStablePlan(feature, reasons);
  }

  if (requested === "stable") {
    reasons.push("profile_stable_requested");
    return buildStablePlan(feature, reasons);
  }

  const guarded = applyConditionGuards(requested, conditions, reasons);
  const contentMode = resolveContentMode(flags?.mediaContentMode, feature);

  return {
    profile: guarded,
    requestedProfile: requested,
    feature,
    contentMode,
    audioCaptureDefaults: audioCaptureFor(guarded, feature, contentMode, conditions, reasons),
    audioPublishDefaults: audioPublishFor(guarded, contentMode, reasons),
    videoCaptureDefaults: videoCaptureFor(guarded, feature, reasons),
    videoPublishDefaults: videoPublishFor(guarded, feature, reasons),
    reasons
  };
}

function buildStablePlan(feature: MediaFeature, reasons: string[]): MediaQualityPlan {
  return {
    profile: "stable",
    requestedProfile: "stable",
    feature,
    contentMode: "speech",
    audioCaptureDefaults: { ...BASELINE_AUDIO_CAPTURE },
    audioPublishDefaults: { ...BASELINE_AUDIO_PUBLISH },
    videoCaptureDefaults: videoCaptureFor("stable", feature, reasons),
    videoPublishDefaults: videoPublishFor("stable", feature, reasons),
    reasons
  };
}

const RANK: Record<MediaQualityProfileName, number> = {
  resilient: 0,
  stable: 1,
  balanced: 2,
  elite: 3
};

export function moreConservative(
  a: MediaQualityProfileName,
  b: MediaQualityProfileName
): MediaQualityProfileName {
  return RANK[a] <= RANK[b] ? a : b;
}

/**
 * Device and network conditions can only ever lower the profile, never raise
 * it. A thermally-throttled phone on a weak network must not be talked into
 * elite by any combination of inputs, and expressing that as a one-way clamp
 * means no future condition can accidentally invert it.
 */
export function applyConditionGuards(
  requested: MediaQualityProfileName,
  conditions: MediaConditionSnapshot,
  reasons: string[]
): MediaQualityProfileName {
  let result = requested;

  if (conditions.thermalState === "critical" || conditions.thermalState === "serious") {
    result = moreConservative(result, "resilient");
    reasons.push("guard_thermal");
  } else if (conditions.thermalState === "fair") {
    result = moreConservative(result, "balanced");
    reasons.push("guard_thermal_fair");
  }

  if (conditions.networkTier === "weak") {
    result = moreConservative(result, "resilient");
    reasons.push("guard_network_weak");
  } else if (conditions.networkTier === "fair") {
    result = moreConservative(result, "balanced");
    reasons.push("guard_network_fair");
  }

  if (conditions.deviceTier === "low") {
    result = moreConservative(result, "resilient");
    reasons.push("guard_device_low");
  } else if (conditions.deviceTier === "mid") {
    result = moreConservative(result, "balanced");
    reasons.push("guard_device_mid");
  }

  // A phone below 15% and not charging is minutes from shutting down. Elite
  // capture is not the thing to spend the remainder on, and the user would
  // rather the call survive than look sharp while it dies.
  if (!conditions.charging && conditions.batteryLevel <= 0.15) {
    result = moreConservative(result, "resilient");
    reasons.push("guard_battery_low");
  } else if (!conditions.charging && conditions.batteryLevel <= 0.3) {
    result = moreConservative(result, "balanced");
    reasons.push("guard_battery_fair");
  }

  return result;
}

/* -------------------------------------------------------------------------- */
/* ROOM OPTIONS                                                                */
/* -------------------------------------------------------------------------- */

export type RoomQualityOptions = {
  adaptiveStream: boolean;
  dynacast: boolean;
  audioCaptureDefaults: AudioCaptureConfig;
  videoCaptureDefaults?: VideoCaptureConfig;
  publishDefaults: Record<string, unknown>;
};

/**
 * Turn a plan into the exact object literal shape both adapters already hand to
 * `new Room({...})`.
 *
 * This exists so there is ONE place that decides what a Room's quality options
 * look like. Two adapters each assembling their own object from the same plan
 * is two places for the stable path to drift, and the whole guarantee of this
 * mission is that stable does not drift.
 *
 * Verified by test: for every feature, the `stable` output of this function is
 * deep-equal to the literal that was in the adapter before this layer existed —
 * including the absence of keys. A video call gets no videoCaptureDefaults and
 * no videoEncoding, because that is what it had.
 */
export function buildRoomQualityOptions(plan: MediaQualityPlan): RoomQualityOptions {
  const publishDefaults: Record<string, unknown> = {};

  if (plan.videoPublishDefaults) {
    publishDefaults.videoEncoding = { ...plan.videoPublishDefaults.videoEncoding };
    if (plan.videoPublishDefaults.degradationPreference) {
      publishDefaults.degradationPreference = plan.videoPublishDefaults.degradationPreference;
    }
  }

  // simulcast stays true even for audio-only features. That is what the
  // baseline did, and an audio-only room ignores it.
  publishDefaults.simulcast = plan.videoPublishDefaults ? plan.videoPublishDefaults.simulcast : true;
  publishDefaults.dtx = plan.audioPublishDefaults.dtx;
  publishDefaults.red = plan.audioPublishDefaults.red;
  publishDefaults.stopMicTrackOnMute = plan.audioPublishDefaults.stopMicTrackOnMute;
  if (plan.audioPublishDefaults.audioBitrate !== undefined) {
    publishDefaults.audioBitrate = plan.audioPublishDefaults.audioBitrate;
  }

  const options: RoomQualityOptions = {
    // Both were already explicitly true at the baseline. They are restated
    // rather than omitted so that reading this function tells you the whole
    // Room configuration, not the part that changed.
    adaptiveStream: true,
    dynacast: true,
    audioCaptureDefaults: { ...plan.audioCaptureDefaults },
    publishDefaults
  };

  if (plan.videoCaptureDefaults) {
    options.videoCaptureDefaults = {
      ...plan.videoCaptureDefaults,
      resolution: { ...plan.videoCaptureDefaults.resolution }
    };
  }

  return options;
}

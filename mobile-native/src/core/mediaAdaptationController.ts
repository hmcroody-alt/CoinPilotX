/**
 * Deterministic media adaptation.
 *
 * WHAT THIS IS
 * A pure reducer. It observes network, thermal, battery and device signals and
 * decides how far down a fixed degradation ladder the session should sit. It
 * returns a state and a description of what should change. It does not touch a
 * Room, a track, the audio session, or the microphone.
 *
 * WHY IT IS A REDUCER AND NOT A CONTROLLER THAT ACTS
 * The verified audio foundation depends on exactly one owner of media state.
 * An adaptation module that could call setMicrophoneEnabled, unpublish, or
 * reconnect would be a second owner, and the failure mode is not theoretical:
 * a bad-network heuristic that mutes a microphone produces a call where one
 * side is silent and nobody can tell why. So this module computes intent; the
 * adapters decide whether to apply it, and the adapters may only apply the
 * video-side parts.
 *
 * WHY HYSTERESIS IS THE WHOLE DESIGN
 * A four-second elevator dip is not a network condition worth reacting to, and
 * a session that re-encodes every time RTT twitches looks worse than one that
 * simply held still. Downgrades therefore need agreement across consecutive
 * samples, upgrades need more agreement plus a dwell time, and recovery walks
 * up one rung at a time. The asymmetry is deliberate: protecting a call is
 * urgent, embellishing one is not.
 */

/* -------------------------------------------------------------------------- */
/* OBSERVED CONDITIONS                                                         */
/* -------------------------------------------------------------------------- */

export type NetworkTier = "good" | "fair" | "weak";
export type ThermalState = "nominal" | "fair" | "serious" | "critical";
export type DeviceTier = "high" | "mid" | "low";

/**
 * Everything the quality policy is allowed to know about the environment.
 *
 * Deliberately small and deliberately non-identifying. There is no IP, no
 * carrier, no device model string, no location. A capability question ("can
 * this device do voice isolation") is answered as a boolean rather than by
 * shipping an OS version the backend could fingerprint.
 */
export type MediaConditionSnapshot = {
  networkTier: NetworkTier;
  thermalState: ThermalState;
  /** 0..1. Values outside the range are clamped by normalizeConditions. */
  batteryLevel: number;
  charging: boolean;
  deviceTier: DeviceTier;
  supportsVoiceIsolation: boolean;
};

export const NEUTRAL_MEDIA_CONDITIONS: MediaConditionSnapshot = Object.freeze({
  networkTier: "good",
  thermalState: "nominal",
  batteryLevel: 1,
  charging: false,
  deviceTier: "high",
  supportsVoiceIsolation: false
});

const NETWORK_TIERS: NetworkTier[] = ["good", "fair", "weak"];
const THERMAL_STATES: ThermalState[] = ["nominal", "fair", "serious", "critical"];
const DEVICE_TIERS: DeviceTier[] = ["high", "mid", "low"];

/**
 * Unknown input resolves to the neutral value, not the worst one.
 *
 * The alternative — treating "unknown" as "bad" — sounds cautious but means
 * every device whose thermal API is unavailable is permanently held at
 * resilient quality. Absence of a signal is not evidence of a problem.
 */
export function normalizeConditions(
  raw: Partial<Record<keyof MediaConditionSnapshot, unknown>> | null | undefined
): MediaConditionSnapshot {
  if (!raw) return { ...NEUTRAL_MEDIA_CONDITIONS };

  const battery =
    typeof raw.batteryLevel === "number" && Number.isFinite(raw.batteryLevel)
      ? Math.min(1, Math.max(0, raw.batteryLevel))
      : 1;

  return {
    networkTier: NETWORK_TIERS.includes(raw.networkTier as NetworkTier)
      ? (raw.networkTier as NetworkTier)
      : "good",
    thermalState: THERMAL_STATES.includes(raw.thermalState as ThermalState)
      ? (raw.thermalState as ThermalState)
      : "nominal",
    batteryLevel: battery,
    charging: raw.charging === true,
    deviceTier: DEVICE_TIERS.includes(raw.deviceTier as DeviceTier)
      ? (raw.deviceTier as DeviceTier)
      : "high",
    supportsVoiceIsolation: raw.supportsVoiceIsolation === true
  };
}

/* -------------------------------------------------------------------------- */
/* NETWORK CLASSIFICATION                                                      */
/* -------------------------------------------------------------------------- */

/**
 * LiveKit's ConnectionQuality enum, as observed at the adapter. Kept as a
 * string union rather than importing the SDK enum so this module stays free of
 * SDK imports and remains testable without a Room.
 */
export type ObservedConnectionQuality = "excellent" | "good" | "poor" | "lost" | "unknown";

/**
 * Both adapters already subscribe to ConnectionQualityChanged and currently do
 * nothing with it. This is the translation that makes the existing signal
 * actionable, with no new subscription and no new event source.
 *
 * `lost` maps to weak rather than to a distinct tier: the reconnection logic
 * owns disconnection, and a quality layer that also reacted to it would be two
 * systems responding to one event.
 */
export function classifyConnectionQuality(raw: unknown): NetworkTier {
  switch (raw) {
    case "excellent":
      return "good";
    case "good":
      return "good";
    case "poor":
      return "fair";
    case "lost":
      return "weak";
    default:
      return "good";
  }
}

/* -------------------------------------------------------------------------- */
/* THE DEGRADATION LADDER                                                      */
/* -------------------------------------------------------------------------- */

/**
 * The mission's degradation order, encoded as data so that a test can assert
 * it rather than a reviewer having to trust that the code implements the
 * document. The order is not a preference; it is the correctness requirement.
 *
 *   1. Maintain audio publication and playback.
 *   2. Reduce unnecessary video layers.
 *   3. Reduce video bitrate.
 *   4. Reduce capture resolution.
 *   5. Reduce frame rate.
 *   6. Pause remote video only as a last resort.
 *
 * Frame rate is late in the list on purpose. Dropping frames is the change a
 * viewer notices first — a sharp slideshow reads as "broken", a slightly soft
 * but fluid image reads as "fine". Resolution is the cheaper sacrifice.
 */
export type DegradationRung =
  | "full_quality"
  | "reduce_layers"
  | "reduce_bitrate"
  | "reduce_resolution"
  | "reduce_frame_rate"
  | "pause_remote_video";

export const DEGRADATION_LADDER: readonly DegradationRung[] = Object.freeze([
  "full_quality",
  "reduce_layers",
  "reduce_bitrate",
  "reduce_resolution",
  "reduce_frame_rate",
  "pause_remote_video"
]);

/**
 * Audio never appears on the ladder. This constant exists so the guarantee is
 * something a test can read, not something a reader has to infer from an
 * absence. If a future rung named anything audio-related is added, the test
 * asserting this list against DEGRADATION_LADDER fails.
 */
export const AUDIO_DEGRADATION_RUNGS: readonly string[] = Object.freeze([]);

export const MAX_RUNG = DEGRADATION_LADDER.length - 1;

/** The last rung is reachable only when the caller explicitly permits it. */
export const LAST_RESORT_RUNG = DEGRADATION_LADDER.indexOf("pause_remote_video");

/* -------------------------------------------------------------------------- */
/* HYSTERESIS CONSTANTS                                                        */
/* -------------------------------------------------------------------------- */

/**
 * Two bad samples to descend, five good samples to ascend, and fifteen seconds
 * of dwell before any upgrade.
 *
 * At a one-second sampling interval that is roughly two seconds to protect a
 * degrading call and at least twenty before improving a recovered one. The
 * numbers are asymmetric because the costs are asymmetric: reacting late to a
 * collapsing network costs the call, reacting late to a recovered one costs a
 * few seconds of slightly softer video that nobody was complaining about.
 */
export const ADAPTATION_TUNING = Object.freeze({
  samplesToDescend: 2,
  samplesToAscend: 5,
  minMsBetweenChanges: 4_000,
  minMsBeforeUpgrade: 15_000,
  /** Ascending more than one rung per decision is how oscillation starts. */
  maxRungsPerUpgrade: 1,
  /**
   * Descending may skip rungs. A network that has genuinely collapsed should
   * not spend four sample windows walking down politely.
   */
  maxRungsPerDowngrade: 2
});

/* -------------------------------------------------------------------------- */
/* STATE                                                                       */
/* -------------------------------------------------------------------------- */

export type MediaAdaptationState = {
  /** Index into DEGRADATION_LADDER. */
  rung: number;
  /** Consecutive samples agreeing the session should be lower than it is. */
  pressureSamples: number;
  /** Consecutive samples agreeing the session could be higher than it is. */
  reliefSamples: number;
  /** Timestamp of the last rung change; 0 before any change has occurred. */
  lastChangeAtMs: number;
  /** Set once the session has ever been degraded. Telemetry only. */
  everDegraded: boolean;
};

export function createAdaptationState(): MediaAdaptationState {
  return {
    rung: 0,
    pressureSamples: 0,
    reliefSamples: 0,
    lastChangeAtMs: 0,
    everDegraded: false
  };
}

export type AdaptationSample = {
  conditions: MediaConditionSnapshot;
  /** Monotonic milliseconds. The caller supplies it so the reducer stays pure. */
  nowMs: number;
  /**
   * Whether pausing remote video is permitted for this surface. A one-to-one
   * video call that pauses the far end's camera has become an audio call
   * without telling anyone, so callers pass false unless the surface can
   * tolerate it (a many-participant livestream viewer can).
   */
  allowRemoteVideoPause?: boolean;
};

export type AdaptationDecision = {
  state: MediaAdaptationState;
  rung: DegradationRung;
  /** True only on the sample where the rung actually moved. */
  changed: boolean;
  direction: "none" | "down" | "up";
  /** Enum-only, safe for telemetry. */
  reasons: string[];
  /**
   * Always true. Present as an explicit field so the value is asserted by the
   * regression suite on every decision rather than assumed.
   */
  audioPreserved: true;
};

/* -------------------------------------------------------------------------- */
/* PRESSURE MODEL                                                              */
/* -------------------------------------------------------------------------- */

/**
 * The rung each condition, on its own, argues for. The session sits at the
 * deepest rung any single condition demands — conditions do not average.
 *
 * Averaging would let a cool device on a collapsing network cancel out into
 * "fine", which is exactly the case the ladder exists for.
 */
export function targetRungFor(
  conditions: MediaConditionSnapshot,
  allowRemoteVideoPause: boolean,
  reasons: string[]
): number {
  let target = 0;

  const demand = (rung: DegradationRung, reason: string) => {
    const index = DEGRADATION_LADDER.indexOf(rung);
    if (index > target) {
      target = index;
      reasons.push(reason);
    } else if (index === target && index > 0) {
      reasons.push(reason);
    }
  };

  switch (conditions.networkTier) {
    case "weak":
      demand("reduce_frame_rate", "network_weak");
      break;
    case "fair":
      demand("reduce_bitrate", "network_fair");
      break;
    default:
      break;
  }

  switch (conditions.thermalState) {
    case "critical":
      demand("reduce_frame_rate", "thermal_critical");
      break;
    case "serious":
      demand("reduce_resolution", "thermal_serious");
      break;
    case "fair":
      demand("reduce_layers", "thermal_fair");
      break;
    default:
      break;
  }

  if (conditions.deviceTier === "low") {
    demand("reduce_resolution", "device_low");
  } else if (conditions.deviceTier === "mid") {
    demand("reduce_layers", "device_mid");
  }

  if (!conditions.charging && conditions.batteryLevel <= 0.15) {
    demand("reduce_resolution", "battery_critical");
  } else if (!conditions.charging && conditions.batteryLevel <= 0.3) {
    demand("reduce_bitrate", "battery_low");
  }

  // The last rung is never reached by accumulated pressure. Pausing remote
  // video is a decision, not a consequence, and it requires both an explicit
  // permission from the caller and a genuinely lost network.
  if (target >= LAST_RESORT_RUNG) {
    target = LAST_RESORT_RUNG - 1;
  }
  if (allowRemoteVideoPause && conditions.networkTier === "weak" && conditions.thermalState === "critical") {
    target = LAST_RESORT_RUNG;
    reasons.push("last_resort_pause_remote_video");
  }

  return target;
}

/* -------------------------------------------------------------------------- */
/* THE REDUCER                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * Same state plus same sample yields the same decision, always. No clocks read
 * internally, no randomness, no I/O.
 *
 * Note what this function never returns: an instruction to mute, unpublish,
 * re-acquire, or reconnect. Adjusting quality never reconnects the room —
 * a renegotiation is invisible to the user, a reconnect is a hole in the call.
 */
export function reduceAdaptation(
  state: MediaAdaptationState,
  sample: AdaptationSample
): AdaptationDecision {
  const reasons: string[] = [];
  const conditions = normalizeConditions(sample.conditions);
  const allowPause = sample.allowRemoteVideoPause === true;
  const target = targetRungFor(conditions, allowPause, reasons);

  const wantsDown = target > state.rung;
  const wantsUp = target < state.rung;

  let pressureSamples = wantsDown ? state.pressureSamples + 1 : 0;
  let reliefSamples = wantsUp ? state.reliefSamples + 1 : 0;

  const sinceChange = state.lastChangeAtMs === 0 ? Number.MAX_SAFE_INTEGER : sample.nowMs - state.lastChangeAtMs;

  const settled = (next: MediaAdaptationState, direction: "none" | "down" | "up", changed: boolean): AdaptationDecision => ({
    state: next,
    rung: DEGRADATION_LADDER[next.rung],
    changed,
    direction,
    reasons,
    audioPreserved: true
  });

  const unchanged: MediaAdaptationState = {
    ...state,
    pressureSamples,
    reliefSamples
  };

  if (wantsDown) {
    if (pressureSamples < ADAPTATION_TUNING.samplesToDescend) {
      reasons.push("hold_awaiting_confirmation");
      return settled(unchanged, "none", false);
    }
    if (sinceChange < ADAPTATION_TUNING.minMsBetweenChanges) {
      reasons.push("hold_change_cooldown");
      return settled(unchanged, "none", false);
    }
    const step = Math.min(target - state.rung, ADAPTATION_TUNING.maxRungsPerDowngrade);
    const rung = Math.min(MAX_RUNG, state.rung + step);
    reasons.push("degrade");
    return settled(
      {
        rung,
        pressureSamples: 0,
        reliefSamples: 0,
        lastChangeAtMs: sample.nowMs,
        everDegraded: true
      },
      "down",
      true
    );
  }

  if (wantsUp) {
    if (reliefSamples < ADAPTATION_TUNING.samplesToAscend) {
      reasons.push("hold_awaiting_stability");
      return settled(unchanged, "none", false);
    }
    if (sinceChange < ADAPTATION_TUNING.minMsBeforeUpgrade) {
      reasons.push("hold_upgrade_dwell");
      return settled(unchanged, "none", false);
    }
    // One rung. A session that has just survived a bad network does not get
    // handed full quality back in a single step to find out whether the
    // recovery was real.
    const rung = Math.max(target, state.rung - ADAPTATION_TUNING.maxRungsPerUpgrade);
    reasons.push("recover_one_step");
    return settled(
      {
        rung,
        pressureSamples: 0,
        reliefSamples: 0,
        lastChangeAtMs: sample.nowMs,
        everDegraded: state.everDegraded
      },
      "up",
      true
    );
  }

  reasons.push("steady");
  return settled(unchanged, "none", false);
}

/* -------------------------------------------------------------------------- */
/* APPLYING A RUNG                                                             */
/* -------------------------------------------------------------------------- */

export type RungEffects = {
  /** Multiplier applied to the profile's video bitrate ceiling. */
  bitrateScale: number;
  /** Multiplier applied to capture width and height. */
  resolutionScale: number;
  /** Multiplier applied to capture and encode frame rate. */
  frameRateScale: number;
  /** Whether simulcast's upper layers should still be offered. */
  fullLayers: boolean;
  /** Whether remote video subscriptions should be paused. */
  pauseRemoteVideo: boolean;
  /** Invariant, at every rung. */
  audioUnchanged: true;
};

/**
 * Each rung is cumulative with the ones above it: descending to
 * reduce_resolution keeps the bitrate reduction from the rung before. A ladder
 * where each step undid the previous one would produce a session that got
 * *more* expensive as the network got worse.
 */
export function effectsForRung(rung: DegradationRung): RungEffects {
  const base: RungEffects = {
    bitrateScale: 1,
    resolutionScale: 1,
    frameRateScale: 1,
    fullLayers: true,
    pauseRemoteVideo: false,
    audioUnchanged: true
  };

  const index = DEGRADATION_LADDER.indexOf(rung);
  if (index <= 0) return base;

  if (index >= DEGRADATION_LADDER.indexOf("reduce_layers")) {
    base.fullLayers = false;
  }
  if (index >= DEGRADATION_LADDER.indexOf("reduce_bitrate")) {
    base.bitrateScale = 0.6;
  }
  if (index >= DEGRADATION_LADDER.indexOf("reduce_resolution")) {
    base.bitrateScale = 0.45;
    base.resolutionScale = 0.75;
  }
  if (index >= DEGRADATION_LADDER.indexOf("reduce_frame_rate")) {
    base.bitrateScale = 0.35;
    base.resolutionScale = 0.66;
    base.frameRateScale = 0.8;
  }
  if (index >= LAST_RESORT_RUNG) {
    base.pauseRemoteVideo = true;
  }

  return base;
}

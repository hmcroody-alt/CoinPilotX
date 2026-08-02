/**
 * The adaptation reducer, enforced.
 *
 * Two properties matter more than any individual behaviour here.
 *
 * First: audio is never on the degradation ladder. Under a critical thermal
 * state on a lost network with a dying battery, the reducer's answer is still
 * "spend less on video". The moment it can answer "stop sending audio", the
 * layer has become capable of producing the exact failure — a silent call
 * nobody can explain — that the audio hard-lock exists to prevent.
 *
 * Second: it does not oscillate. A quality controller that flaps looks worse
 * than one that never adapts at all, because the viewer sees the picture change
 * repeatedly and reads that as brokenness. The oscillation tests below drive a
 * flapping network for a hundred samples and assert a small bounded number of
 * changes, which is a property no amount of reading the code would establish.
 */
import {
  ADAPTATION_TUNING,
  AUDIO_DEGRADATION_RUNGS,
  DEGRADATION_LADDER,
  LAST_RESORT_RUNG,
  NEUTRAL_MEDIA_CONDITIONS,
  classifyConnectionQuality,
  createAdaptationState,
  effectsForRung,
  normalizeConditions,
  reduceAdaptation,
  targetRungFor,
  type AdaptationSample,
  type MediaAdaptationState,
  type MediaConditionSnapshot
} from "../mediaAdaptationController";

const GOOD: MediaConditionSnapshot = { ...NEUTRAL_MEDIA_CONDITIONS };
const FAIR: MediaConditionSnapshot = { ...NEUTRAL_MEDIA_CONDITIONS, networkTier: "fair" };
const WEAK: MediaConditionSnapshot = { ...NEUTRAL_MEDIA_CONDITIONS, networkTier: "weak" };
const MELTING: MediaConditionSnapshot = {
  ...NEUTRAL_MEDIA_CONDITIONS,
  networkTier: "weak",
  thermalState: "critical",
  batteryLevel: 0.05,
  deviceTier: "low"
};

/** Drive the reducer over a sequence of conditions at a fixed sample interval. */
function run(
  conditions: MediaConditionSnapshot[],
  options: { intervalMs?: number; startMs?: number; allowRemoteVideoPause?: boolean } = {}
) {
  const interval = options.intervalMs ?? 1_000;
  let state: MediaAdaptationState = createAdaptationState();
  let nowMs = options.startMs ?? 100_000;
  const decisions = [];
  for (const snapshot of conditions) {
    const sample: AdaptationSample = {
      conditions: snapshot,
      nowMs,
      allowRemoteVideoPause: options.allowRemoteVideoPause
    };
    const decision = reduceAdaptation(state, sample);
    state = decision.state;
    decisions.push(decision);
    nowMs += interval;
  }
  return { state, decisions };
}

function repeat(snapshot: MediaConditionSnapshot, times: number): MediaConditionSnapshot[] {
  return Array.from({ length: times }, () => snapshot);
}

/* -------------------------------------------------------------------------- */
/* GATE 1: AUDIO IS NEVER DEGRADED                                             */
/* -------------------------------------------------------------------------- */

describe("audio is never on the ladder", () => {
  it("has no audio rung", () => {
    expect(AUDIO_DEGRADATION_RUNGS).toEqual([]);
    for (const rung of DEGRADATION_LADDER) {
      expect(rung).not.toMatch(/audio|mic|microphone|mute/i);
    }
  });

  it("reports audioPreserved on every decision, under every condition", () => {
    const { decisions } = run(
      [...repeat(GOOD, 3), ...repeat(FAIR, 5), ...repeat(MELTING, 20), ...repeat(GOOD, 20)],
      { allowRemoteVideoPause: true }
    );
    for (const decision of decisions) {
      expect(decision.audioPreserved).toBe(true);
    }
  });

  it("leaves audio untouched at every rung, including the last resort", () => {
    for (const rung of DEGRADATION_LADDER) {
      expect(effectsForRung(rung).audioUnchanged).toBe(true);
    }
  });

  it("never reaches a state that could stop the microphone", () => {
    const { state } = run(repeat(MELTING, 60), { allowRemoteVideoPause: true });
    const effects = effectsForRung(DEGRADATION_LADDER[state.rung]);
    // Video may be paused. Audio scaling does not exist as a concept here, and
    // that absence is the guarantee.
    expect(Object.keys(effects)).not.toContain("audioBitrateScale");
    expect(Object.keys(effects)).not.toContain("pauseAudio");
    expect(effects.audioUnchanged).toBe(true);
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 2: THE DEGRADATION ORDER IS THE MISSION'S ORDER                        */
/* -------------------------------------------------------------------------- */

describe("degradation order", () => {
  it("matches the specified order exactly", () => {
    expect([...DEGRADATION_LADDER]).toEqual([
      "full_quality",
      "reduce_layers",
      "reduce_bitrate",
      "reduce_resolution",
      "reduce_frame_rate",
      "pause_remote_video"
    ]);
  });

  it("sheds layers before bitrate, bitrate before resolution, resolution before frame rate", () => {
    const index = (rung: string) => DEGRADATION_LADDER.indexOf(rung as never);
    expect(index("reduce_layers")).toBeLessThan(index("reduce_bitrate"));
    expect(index("reduce_bitrate")).toBeLessThan(index("reduce_resolution"));
    expect(index("reduce_resolution")).toBeLessThan(index("reduce_frame_rate"));
    expect(index("reduce_frame_rate")).toBeLessThan(index("pause_remote_video"));
  });

  it("makes each rung cumulative with the ones above it", () => {
    let previousBitrate = Infinity;
    let previousResolution = Infinity;
    let previousFrameRate = Infinity;
    for (const rung of DEGRADATION_LADDER) {
      const effects = effectsForRung(rung);
      expect(effects.bitrateScale).toBeLessThanOrEqual(previousBitrate);
      expect(effects.resolutionScale).toBeLessThanOrEqual(previousResolution);
      expect(effects.frameRateScale).toBeLessThanOrEqual(previousFrameRate);
      previousBitrate = effects.bitrateScale;
      previousResolution = effects.resolutionScale;
      previousFrameRate = effects.frameRateScale;
    }
  });

  it("touches frame rate only at the frame-rate rung and below", () => {
    for (const rung of ["full_quality", "reduce_layers", "reduce_bitrate", "reduce_resolution"] as const) {
      expect(effectsForRung(rung).frameRateScale).toBe(1);
    }
    expect(effectsForRung("reduce_frame_rate").frameRateScale).toBeLessThan(1);
  });

  it("pauses remote video only at the last rung", () => {
    for (const rung of DEGRADATION_LADDER.slice(0, LAST_RESORT_RUNG)) {
      expect(effectsForRung(rung).pauseRemoteVideo).toBe(false);
    }
    expect(effectsForRung("pause_remote_video").pauseRemoteVideo).toBe(true);
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 3: PAUSING REMOTE VIDEO IS A DECISION, NOT A CONSEQUENCE               */
/* -------------------------------------------------------------------------- */

describe("the last resort", () => {
  it("is unreachable when the caller does not permit it", () => {
    const { state } = run(repeat(MELTING, 80), { allowRemoteVideoPause: false });
    expect(state.rung).toBeLessThan(LAST_RESORT_RUNG);
    expect(effectsForRung(DEGRADATION_LADDER[state.rung]).pauseRemoteVideo).toBe(false);
  });

  it("is unreachable by accumulated pressure alone even when permitted", () => {
    // Bad battery, low device tier and serious thermals together must not add up
    // to pausing the far end's camera. Only a genuinely lost network plus a
    // critical thermal state does that.
    const grim: MediaConditionSnapshot = {
      ...NEUTRAL_MEDIA_CONDITIONS,
      networkTier: "fair",
      thermalState: "serious",
      deviceTier: "low",
      batteryLevel: 0.05
    };
    const reasons: string[] = [];
    expect(targetRungFor(grim, true, reasons)).toBeLessThan(LAST_RESORT_RUNG);
  });

  it("is reachable on a lost network with a critical thermal state, when permitted", () => {
    const reasons: string[] = [];
    expect(targetRungFor(MELTING, true, reasons)).toBe(LAST_RESORT_RUNG);
    expect(reasons).toContain("last_resort_pause_remote_video");
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 4: HYSTERESIS                                                          */
/* -------------------------------------------------------------------------- */

describe("hysteresis", () => {
  it("ignores a single bad sample", () => {
    const { decisions, state } = run([GOOD, WEAK, GOOD, GOOD]);
    expect(state.rung).toBe(0);
    expect(decisions.every((decision) => !decision.changed)).toBe(true);
  });

  it("acts after the configured number of consecutive bad samples", () => {
    const { decisions } = run(repeat(WEAK, ADAPTATION_TUNING.samplesToDescend + 1));
    const changed = decisions.filter((decision) => decision.changed);
    expect(changed.length).toBeGreaterThan(0);
    expect(changed[0].direction).toBe("down");
  });

  it("does not recover on the first good sample", () => {
    const conditions = [...repeat(WEAK, 6), GOOD];
    const { decisions } = run(conditions);
    expect(decisions[decisions.length - 1].changed).toBe(false);
    expect(decisions[decisions.length - 1].reasons).toContain("hold_awaiting_stability");
  });

  it("requires a dwell time before any upgrade", () => {
    // Sampling every 200ms, five good samples arrive in one second — well inside
    // the 15-second dwell. Sample count alone must not be enough.
    const { state } = run([...repeat(WEAK, 6), ...repeat(GOOD, 10)], { intervalMs: 200 });
    expect(state.rung).toBeGreaterThan(0);
  });

  it("recovers one rung at a time, never straight to full quality", () => {
    const { decisions } = run([...repeat(MELTING, 10), ...repeat(GOOD, 40)], { intervalMs: 5_000 });
    const upgrades = decisions.filter((decision) => decision.direction === "up");
    expect(upgrades.length).toBeGreaterThan(0);
    for (const upgrade of upgrades) {
      expect(upgrade.reasons).toContain("recover_one_step");
    }
    // Every upgrade moved exactly one rung.
    let previous = -1;
    for (const decision of decisions) {
      if (decision.direction !== "up") continue;
      const rung = DEGRADATION_LADDER.indexOf(decision.rung);
      if (previous >= 0) expect(previous - rung).toBe(1);
      previous = rung;
    }
  });

  it("eventually returns to full quality once conditions hold", () => {
    const { state } = run([...repeat(WEAK, 10), ...repeat(GOOD, 120)], { intervalMs: 5_000 });
    expect(state.rung).toBe(0);
    expect(state.everDegraded).toBe(true);
  });

  it("descends faster than it ascends", () => {
    expect(ADAPTATION_TUNING.samplesToDescend).toBeLessThan(ADAPTATION_TUNING.samplesToAscend);
    expect(ADAPTATION_TUNING.minMsBetweenChanges).toBeLessThan(ADAPTATION_TUNING.minMsBeforeUpgrade);
    expect(ADAPTATION_TUNING.maxRungsPerUpgrade).toBeLessThanOrEqual(
      ADAPTATION_TUNING.maxRungsPerDowngrade
    );
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 5: NO OSCILLATION                                                      */
/* -------------------------------------------------------------------------- */

describe("does not oscillate", () => {
  it("changes rarely under a network that flaps every sample", () => {
    const flapping = Array.from({ length: 100 }, (_, index) => (index % 2 === 0 ? GOOD : WEAK));
    const { decisions } = run(flapping);
    const changes = decisions.filter((decision) => decision.changed).length;
    // Alternating samples never accumulate two consecutive agreeing samples, so
    // the correct number of changes is zero. Allowing a small margin keeps the
    // test about the property rather than about the exact constant.
    expect(changes).toBeLessThanOrEqual(2);
  });

  it("changes a bounded number of times under a noisy but degrading network", () => {
    const noisy = Array.from({ length: 200 }, (_, index) => {
      if (index % 7 === 0) return GOOD;
      if (index % 3 === 0) return FAIR;
      return WEAK;
    });
    const { decisions } = run(noisy);
    const changes = decisions.filter((decision) => decision.changed).length;
    expect(changes).toBeLessThanOrEqual(12);
  });

  it("honours a cooldown between consecutive changes", () => {
    const { decisions } = run(repeat(MELTING, 30), { intervalMs: 250 });
    const changeTimes: number[] = [];
    let nowMs = 100_000;
    for (const decision of decisions) {
      if (decision.changed) changeTimes.push(nowMs);
      nowMs += 250;
    }
    for (let index = 1; index < changeTimes.length; index += 1) {
      expect(changeTimes[index] - changeTimes[index - 1]).toBeGreaterThanOrEqual(
        ADAPTATION_TUNING.minMsBetweenChanges
      );
    }
  });

  it("is stable: identical input sequences produce identical final states", () => {
    const sequence = [...repeat(FAIR, 8), ...repeat(GOOD, 8), ...repeat(WEAK, 8)];
    expect(run(sequence).state).toEqual(run(sequence).state);
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 6: THERMAL AND BATTERY                                                 */
/* -------------------------------------------------------------------------- */

describe("thermal and battery pressure", () => {
  it("degrades video when the device is thermally serious", () => {
    const hot = { ...NEUTRAL_MEDIA_CONDITIONS, thermalState: "serious" as const };
    const { state } = run(repeat(hot, 10));
    expect(state.rung).toBeGreaterThan(0);
    expect(effectsForRung(DEGRADATION_LADDER[state.rung]).audioUnchanged).toBe(true);
  });

  it("reduces demand on a nearly flat battery, but not while charging", () => {
    const flat = { ...NEUTRAL_MEDIA_CONDITIONS, batteryLevel: 0.05, charging: false };
    const charging = { ...flat, charging: true };
    expect(targetRungFor(flat, false, [])).toBeGreaterThan(0);
    expect(targetRungFor(charging, false, [])).toBe(0);
  });

  it("takes the deepest rung any single condition demands, not an average", () => {
    // A cool device on a collapsing network must not average out into "fine".
    const mixed: MediaConditionSnapshot = {
      ...NEUTRAL_MEDIA_CONDITIONS,
      thermalState: "nominal",
      deviceTier: "high",
      networkTier: "weak"
    };
    expect(targetRungFor(mixed, false, [])).toBe(
      DEGRADATION_LADDER.indexOf("reduce_frame_rate")
    );
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 7: INPUT NORMALISATION                                                 */
/* -------------------------------------------------------------------------- */

describe("condition normalisation", () => {
  it("treats an unknown signal as neutral, not as bad", () => {
    // Every device whose thermal API is unavailable would otherwise be held at
    // resilient forever. Absence of a signal is not evidence of a problem.
    expect(normalizeConditions(null)).toEqual(NEUTRAL_MEDIA_CONDITIONS);
    expect(normalizeConditions({ thermalState: "on fire" }).thermalState).toBe("nominal");
    expect(normalizeConditions({ networkTier: 3 }).networkTier).toBe("good");
    expect(normalizeConditions({ deviceTier: undefined }).deviceTier).toBe("high");
  });

  it("clamps battery level into 0..1", () => {
    expect(normalizeConditions({ batteryLevel: 42 }).batteryLevel).toBe(1);
    expect(normalizeConditions({ batteryLevel: -3 }).batteryLevel).toBe(0);
    expect(normalizeConditions({ batteryLevel: NaN }).batteryLevel).toBe(1);
    expect(normalizeConditions({ batteryLevel: 0.42 }).batteryLevel).toBe(0.42);
  });

  it("requires a literal true for charging and voice isolation", () => {
    expect(normalizeConditions({ charging: "true" }).charging).toBe(false);
    expect(normalizeConditions({ charging: 1 }).charging).toBe(false);
    expect(normalizeConditions({ supportsVoiceIsolation: "yes" }).supportsVoiceIsolation).toBe(false);
    expect(normalizeConditions({ supportsVoiceIsolation: true }).supportsVoiceIsolation).toBe(true);
  });

  it("maps LiveKit connection quality onto network tiers", () => {
    expect(classifyConnectionQuality("excellent")).toBe("good");
    expect(classifyConnectionQuality("good")).toBe("good");
    expect(classifyConnectionQuality("poor")).toBe("fair");
    expect(classifyConnectionQuality("lost")).toBe("weak");
    expect(classifyConnectionQuality("unknown")).toBe("good");
    expect(classifyConnectionQuality(undefined)).toBe("good");
  });
});

/* -------------------------------------------------------------------------- */
/* GATE 8: THE REDUCER IS A REDUCER                                            */
/* -------------------------------------------------------------------------- */

describe("purity", () => {
  it("does not mutate the state it is given", () => {
    const state = createAdaptationState();
    const snapshot = { ...state };
    reduceAdaptation(state, { conditions: MELTING, nowMs: 1_000 });
    reduceAdaptation(state, { conditions: MELTING, nowMs: 2_000 });
    expect(state).toEqual(snapshot);
  });

  it("does not mutate the conditions it is given", () => {
    const conditions = { ...MELTING };
    const snapshot = { ...conditions };
    reduceAdaptation(createAdaptationState(), { conditions, nowMs: 1_000 });
    expect(conditions).toEqual(snapshot);
  });

  it("emits only machine-readable reason codes", () => {
    const { decisions } = run([...repeat(WEAK, 10), ...repeat(GOOD, 40)], { intervalMs: 5_000 });
    for (const decision of decisions) {
      for (const reason of decision.reasons) {
        expect(reason).toMatch(/^[a-z0-9_]+$/);
      }
    }
  });

  it("starts at full quality with no history", () => {
    const state = createAdaptationState();
    expect(state.rung).toBe(0);
    expect(state.everDegraded).toBe(false);
    expect(state.lastChangeAtMs).toBe(0);
    expect(effectsForRung(DEGRADATION_LADDER[state.rung])).toEqual({
      bitrateScale: 1,
      resolutionScale: 1,
      frameRateScale: 1,
      fullLayers: true,
      pauseRemoteVideo: false,
      audioUnchanged: true
    });
  });
});

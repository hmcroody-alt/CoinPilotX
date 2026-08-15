/**
 * Pure, deterministic state machine for phone-tilt navigation.
 *
 * No React, no sensors, no timers — callers feed it samples/events plus the
 * current timestamp, and it returns the next state and any side-effect
 * requests (haptic, navigation commit). This keeps every transition unit
 * testable without mocking DeviceMotion.
 *
 * State model (mission §8):
 *   unavailable          sensors missing / no permission — swipe only
 *   disabled             user chose Swipe Only, or flags off
 *   calibrating          capturing neutral baseline
 *   neutral              inside dead zone, ready
 *   preview-left/right   outside dead zone — parallax preview only
 *   armed                past commit threshold, hold timer running
 *   committed            hold satisfied — navigation fires exactly once
 *   returning-to-neutral must re-enter dead zone before next gesture
 *   cooldown             brief lockout after a committed transition
 *   suspended            context makes tilt unsafe (typing, menus, flat, …)
 *
 * Safety invariants encoded here:
 *   - A commit requires neutral → preview → armed → hold elapsed. No path
 *     commits from suspension, cooldown, or returning-to-neutral.
 *   - After a commit the machine will not commit again until the device
 *     returns to neutral AND cooldown has elapsed.
 *   - Touch (drag) always wins: TOUCH_START from any active state suspends
 *     tilt until touch ends and neutral is re-entered.
 *   - In parallax mode the machine never leaves preview: no armed/committed.
 */

export type MotionMode = "tilt" | "parallax" | "swipe-only";

export type MotionState =
  | "unavailable"
  | "disabled"
  | "calibrating"
  | "neutral"
  | "preview-left"
  | "preview-right"
  | "armed"
  | "committed"
  | "returning-to-neutral"
  | "cooldown"
  | "suspended";

export type TiltDirection = "left" | "right";

export type MotionSensitivity = "low" | "medium" | "high";

export interface MotionConfig {
  /** Radians of roll (after baseline subtraction) treated as neutral. */
  deadZoneRad: number;
  /** Beyond this, preview parallax begins. Must be >= deadZoneRad. */
  previewRad: number;
  /** Beyond this, the commit hold timer arms. Must be > previewRad. */
  commitRad: number;
  /** Sustained-hold requirement before a commit fires (mission: 300–450ms). */
  holdMs: number;
  /** Lockout after a committed transition. */
  cooldownMs: number;
  /** |pitch| above this ≈ device lying flat → suspend. */
  flatPitchRad: number;
  /** Total acceleration variance above this ≈ walking/unstable → suspend. */
  instabilityThreshold: number;
}

/** Sensitivity presets. Higher sensitivity = smaller angles, shorter hold. */
export const MOTION_CONFIGS: Record<MotionSensitivity, MotionConfig> = {
  low: {
    deadZoneRad: 0.10,
    previewRad: 0.16,
    commitRad: 0.30,
    holdMs: 450,
    cooldownMs: 900,
    flatPitchRad: 0.25,
    instabilityThreshold: 2.2
  },
  medium: {
    deadZoneRad: 0.08,
    previewRad: 0.12,
    commitRad: 0.24,
    holdMs: 380,
    cooldownMs: 750,
    flatPitchRad: 0.25,
    instabilityThreshold: 2.6
  },
  high: {
    deadZoneRad: 0.06,
    previewRad: 0.09,
    commitRad: 0.19,
    holdMs: 300,
    cooldownMs: 600,
    flatPitchRad: 0.25,
    instabilityThreshold: 3.0
  }
};

export interface MotionMachine {
  state: MotionState;
  mode: MotionMode;
  config: MotionConfig;
  /** Set while armed: when (now >= armedAt + holdMs) the commit fires. */
  armedAt: number | null;
  armedDirection: TiltDirection | null;
  /** Set on commit: cooldown runs until committedAt + cooldownMs. */
  committedAt: number | null;
  /** Whether a touch drag is currently holding the machine suspended. */
  touchActive: boolean;
  /** External suspension reasons currently asserted (keyboard, menu, …). */
  suspensionReasons: ReadonlySet<string>;
}

export interface TiltSample {
  /** Roll minus calibrated baseline, radians. Positive = tilt right. */
  roll: number;
  /** Pitch relative to holding position, radians. */
  pitch: number;
  /** Smoothed total-acceleration magnitude deviation (instability metric). */
  instability: number;
}

export interface MotionEffects {
  /** Fire pager navigation once, in this direction. */
  commit: TiltDirection | null;
  /** Request a haptic tick (commit confirmation). */
  haptic: "commit" | null;
  /**
   * Parallax preview progress, -1..1 (negative = left). 0 everywhere except
   * preview/armed states. Consumers map this to a small translateX.
   */
  previewProgress: number;
}

const NO_EFFECTS: MotionEffects = { commit: null, haptic: null, previewProgress: 0 };

export function createMotionMachine(
  mode: MotionMode,
  sensitivity: MotionSensitivity,
  available: boolean
): MotionMachine {
  return {
    state: !available ? "unavailable" : mode === "swipe-only" ? "disabled" : "calibrating",
    mode,
    config: MOTION_CONFIGS[sensitivity],
    armedAt: null,
    armedDirection: null,
    committedAt: null,
    touchActive: false,
    suspensionReasons: new Set()
  };
}

/** States in which sensor samples are processed at all. */
const ACTIVE_STATES: ReadonlySet<MotionState> = new Set([
  "neutral",
  "preview-left",
  "preview-right",
  "armed",
  "returning-to-neutral",
  "cooldown"
]);

function idle(machine: MotionMachine, state: MotionState): MotionMachine {
  return { ...machine, state, armedAt: null, armedDirection: null };
}

/** Calibration completed: baseline captured by the caller, enter neutral. */
export function calibrationComplete(machine: MotionMachine): MotionMachine {
  if (machine.state !== "calibrating") return machine;
  return idle(machine, resolveIdleState(machine));
}

/** Where an idle machine should sit given current suspension/touch. */
function resolveIdleState(machine: MotionMachine): MotionState {
  if (machine.touchActive || machine.suspensionReasons.size > 0) return "suspended";
  return "neutral";
}

/** Assert or clear an external suspension reason (keyboard, menu, sheet…). */
export function setSuspension(machine: MotionMachine, reason: string, active: boolean): MotionMachine {
  const reasons = new Set(machine.suspensionReasons);
  if (active) reasons.add(reason);
  else reasons.delete(reason);
  const next: MotionMachine = { ...machine, suspensionReasons: reasons };
  if (next.state === "unavailable" || next.state === "disabled" || next.state === "calibrating") {
    return next;
  }
  if (reasons.size > 0 || next.touchActive) return idle(next, "suspended");
  if (next.state === "suspended") return idle(next, "returning-to-neutral");
  return next;
}

/** Touch always wins: drag start suspends, drag end requires re-neutral. */
export function touchStart(machine: MotionMachine): MotionMachine {
  const next: MotionMachine = { ...machine, touchActive: true };
  if (next.state === "unavailable" || next.state === "disabled" || next.state === "calibrating") {
    return next;
  }
  return idle(next, "suspended");
}

export function touchEnd(machine: MotionMachine): MotionMachine {
  const next: MotionMachine = { ...machine, touchActive: false };
  if (next.state !== "suspended") return next;
  if (next.suspensionReasons.size > 0) return next;
  return idle(next, "returning-to-neutral");
}

/**
 * Process one filtered sensor sample. Returns the next machine plus effect
 * requests. `now` is a monotonic ms timestamp supplied by the caller.
 */
export function processSample(
  machine: MotionMachine,
  sample: TiltSample,
  now: number
): { machine: MotionMachine; effects: MotionEffects } {
  const { config } = machine;
  const sampleUnsafe =
    Math.abs(sample.pitch) > config.flatPitchRad || sample.instability > config.instabilityThreshold;

  if (machine.state === "suspended") {
    // External suspensions (touch, keyboard, menus…) only clear via their
    // own events. Sample-driven suspension self-clears once motion is safe,
    // but always through returning-to-neutral — never straight to a gesture.
    if (machine.touchActive || machine.suspensionReasons.size > 0 || sampleUnsafe) {
      return { machine, effects: NO_EFFECTS };
    }
    return { machine: idle(machine, "returning-to-neutral"), effects: NO_EFFECTS };
  }

  if (!ACTIVE_STATES.has(machine.state)) {
    return { machine, effects: NO_EFFECTS };
  }

  // Conservative suspensions from the sample itself (flat / unstable motion).
  if (sampleUnsafe) {
    return { machine: idle(machine, "suspended"), effects: NO_EFFECTS };
  }

  const absRoll = Math.abs(sample.roll);
  const inDeadZone = absRoll <= config.deadZoneRad;

  if (machine.state === "cooldown") {
    const elapsed = machine.committedAt !== null && now - machine.committedAt >= config.cooldownMs;
    if (elapsed && inDeadZone) {
      return { machine: { ...idle(machine, "neutral"), committedAt: null }, effects: NO_EFFECTS };
    }
    return { machine, effects: NO_EFFECTS };
  }

  if (machine.state === "returning-to-neutral") {
    if (inDeadZone) return { machine: idle(machine, "neutral"), effects: NO_EFFECTS };
    return { machine, effects: NO_EFFECTS };
  }

  // neutral / preview / armed
  const direction: TiltDirection = sample.roll > 0 ? "right" : "left";
  const previewProgress = computePreviewProgress(sample.roll, config);

  if (inDeadZone) {
    return { machine: idle(machine, "neutral"), effects: NO_EFFECTS };
  }

  if (absRoll < config.previewRad) {
    // Between dead zone and preview threshold: slight depth cue only.
    // Dropping here from armed simply cancels the arm — nothing committed.
    return { machine: idle(machine, "neutral"), effects: { ...NO_EFFECTS, previewProgress } };
  }

  // Parallax-only mode never arms or commits.
  if (machine.mode === "parallax") {
    return {
      machine: idle(machine, direction === "left" ? "preview-left" : "preview-right"),
      effects: { ...NO_EFFECTS, previewProgress }
    };
  }

  if (absRoll < config.commitRad) {
    // Preview zone. Falling back out of armed requires re-neutral? No —
    // dropping below commit before hold elapses simply cancels the arm.
    return {
      machine: idle(machine, direction === "left" ? "preview-left" : "preview-right"),
      effects: { ...NO_EFFECTS, previewProgress }
    };
  }

  // Commit zone.
  if (machine.state !== "armed" || machine.armedDirection !== direction) {
    return {
      machine: { ...machine, state: "armed", armedAt: now, armedDirection: direction },
      effects: { ...NO_EFFECTS, previewProgress }
    };
  }

  if (machine.armedAt !== null && now - machine.armedAt >= config.holdMs) {
    return {
      machine: {
        ...idle(machine, "cooldown"),
        committedAt: now
      },
      effects: { commit: direction, haptic: "commit", previewProgress: 0 }
    };
  }

  return { machine, effects: { ...NO_EFFECTS, previewProgress } };
}

/** Map roll to -1..1 preview progress, 0 inside the dead zone. */
export function computePreviewProgress(roll: number, config: MotionConfig): number {
  const abs = Math.abs(roll);
  if (abs <= config.deadZoneRad) return 0;
  const span = config.commitRad - config.deadZoneRad;
  const progress = Math.min(1, (abs - config.deadZoneRad) / span);
  return roll < 0 ? -progress : progress;
}

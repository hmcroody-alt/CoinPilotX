import {
  MOTION_CONFIGS,
  calibrationComplete,
  computePreviewProgress,
  createMotionMachine,
  processSample,
  setSuspension,
  touchEnd,
  touchStart,
  type MotionMachine,
  type TiltSample
} from "../motionStateMachine";

const cfg = MOTION_CONFIGS.medium;

function sample(roll: number, overrides: Partial<TiltSample> = {}): TiltSample {
  return { roll, pitch: 0, instability: 0, ...overrides };
}

function ready(mode: "tilt" | "parallax" = "tilt"): MotionMachine {
  return calibrationComplete(createMotionMachine(mode, "medium", true));
}

/** Drive a full neutral → preview → armed → hold → commit sequence. */
function driveToCommit(machine: MotionMachine, roll: number, start = 1000) {
  let result = processSample(machine, sample(roll), start); // arms
  result = processSample(result.machine, sample(roll), start + cfg.holdMs + 10);
  return result;
}

describe("motion state machine", () => {
  it("starts unavailable without sensors, disabled in swipe-only mode", () => {
    expect(createMotionMachine("tilt", "medium", false).state).toBe("unavailable");
    expect(createMotionMachine("swipe-only", "medium", true).state).toBe("disabled");
    expect(createMotionMachine("tilt", "medium", true).state).toBe("calibrating");
  });

  it("calibration completes into neutral", () => {
    expect(ready().state).toBe("neutral");
  });

  it("stays neutral inside the dead zone with zero preview", () => {
    const { machine, effects } = processSample(ready(), sample(cfg.deadZoneRad * 0.5), 0);
    expect(machine.state).toBe("neutral");
    expect(effects.previewProgress).toBe(0);
    expect(effects.commit).toBeNull();
  });

  it("enters preview past the preview threshold, no commit", () => {
    const { machine, effects } = processSample(ready(), sample(cfg.previewRad + 0.01), 0);
    expect(machine.state).toBe("preview-right");
    expect(effects.previewProgress).toBeGreaterThan(0);
    expect(effects.commit).toBeNull();

    const left = processSample(ready(), sample(-(cfg.previewRad + 0.01)), 0);
    expect(left.machine.state).toBe("preview-left");
    expect(left.effects.previewProgress).toBeLessThan(0);
  });

  it("arms in the commit zone but does not commit before the hold elapses", () => {
    const armed = processSample(ready(), sample(cfg.commitRad + 0.02), 1000);
    expect(armed.machine.state).toBe("armed");
    expect(armed.effects.commit).toBeNull();

    const early = processSample(armed.machine, sample(cfg.commitRad + 0.02), 1000 + cfg.holdMs - 50);
    expect(early.effects.commit).toBeNull();
    expect(early.machine.state).toBe("armed");
  });

  it("commits with haptic after a sustained hold, then enters cooldown", () => {
    const { machine, effects } = driveToCommit(ready(), cfg.commitRad + 0.02);
    expect(effects.commit).toBe("right");
    expect(effects.haptic).toBe("commit");
    expect(machine.state).toBe("cooldown");

    const leftCommit = driveToCommit(ready(), -(cfg.commitRad + 0.02));
    expect(leftCommit.effects.commit).toBe("left");
  });

  it("dropping out of the commit zone before the hold cancels the arm", () => {
    const armed = processSample(ready(), sample(cfg.commitRad + 0.02), 1000);
    const dropped = processSample(armed.machine, sample(cfg.previewRad + 0.01), 1100);
    expect(dropped.machine.state).toBe("preview-right");
    expect(dropped.machine.armedAt).toBeNull();
    // Re-entering the commit zone restarts the hold from scratch.
    const rearmed = processSample(dropped.machine, sample(cfg.commitRad + 0.02), 1200);
    expect(rearmed.machine.armedAt).toBe(1200);
    expect(rearmed.effects.commit).toBeNull();
  });

  it("requires return-to-neutral AND cooldown before the next commit", () => {
    const committed = driveToCommit(ready(), cfg.commitRad + 0.02, 1000);
    const commitTime = committed.machine.committedAt as number;

    // Still tilted after commit: no second commit, ever.
    const heldOver = processSample(committed.machine, sample(cfg.commitRad + 0.02), commitTime + cfg.cooldownMs + 500);
    expect(heldOver.effects.commit).toBeNull();
    expect(heldOver.machine.state).toBe("cooldown");

    // Back to neutral before cooldown elapses: still locked.
    const earlyNeutral = processSample(committed.machine, sample(0), commitTime + 50);
    expect(earlyNeutral.machine.state).toBe("cooldown");

    // Neutral + cooldown elapsed: ready again.
    const released = processSample(committed.machine, sample(0), commitTime + cfg.cooldownMs + 10);
    expect(released.machine.state).toBe("neutral");

    // And a fresh gesture commits normally.
    const next = driveToCommit(released.machine, cfg.commitRad + 0.02, commitTime + cfg.cooldownMs + 100);
    expect(next.effects.commit).toBe("right");
  });

  it("parallax mode previews but never arms or commits", () => {
    const machine = ready("parallax");
    const deep = processSample(machine, sample(cfg.commitRad + 0.1), 0);
    expect(deep.machine.state).toBe("preview-right");
    expect(deep.effects.commit).toBeNull();
    const held = processSample(deep.machine, sample(cfg.commitRad + 0.1), 10_000);
    expect(held.effects.commit).toBeNull();
    expect(held.machine.state).toBe("preview-right");
  });

  it("touch always wins: drag suspends, release requires re-neutral", () => {
    const armed = processSample(ready(), sample(cfg.commitRad + 0.02), 1000).machine;
    const dragging = touchStart(armed);
    expect(dragging.state).toBe("suspended");
    // Samples during touch are inert.
    const during = processSample(dragging, sample(cfg.commitRad + 0.5), 5000);
    expect(during.effects.commit).toBeNull();
    expect(during.machine.state).toBe("suspended");

    const released = touchEnd(dragging);
    expect(released.state).toBe("returning-to-neutral");
    // Still tilted: no gesture until neutral.
    const tilted = processSample(released, sample(cfg.commitRad + 0.02), 6000);
    expect(tilted.machine.state).toBe("returning-to-neutral");
    const neutral = processSample(released, sample(0), 6000);
    expect(neutral.machine.state).toBe("neutral");
  });

  it("external suspension reasons stack and clear through returning-to-neutral", () => {
    let machine = ready();
    machine = setSuspension(machine, "keyboard", true);
    machine = setSuspension(machine, "create-console", true);
    expect(machine.state).toBe("suspended");
    machine = setSuspension(machine, "keyboard", false);
    expect(machine.state).toBe("suspended");
    machine = setSuspension(machine, "create-console", false);
    expect(machine.state).toBe("returning-to-neutral");
  });

  it("suspends on flat device or unstable motion and self-recovers safely", () => {
    const flat = processSample(ready(), sample(0, { pitch: cfg.flatPitchRad + 0.1 }), 0);
    expect(flat.machine.state).toBe("suspended");

    const shaky = processSample(ready(), sample(0, { instability: cfg.instabilityThreshold + 1 }), 0);
    expect(shaky.machine.state).toBe("suspended");

    // Recovery goes through returning-to-neutral, never straight to a commit.
    const safe = processSample(shaky.machine, sample(cfg.commitRad + 0.5), 100);
    expect(safe.machine.state).toBe("returning-to-neutral");
    expect(safe.effects.commit).toBeNull();
  });

  it("unavailable and disabled machines ignore samples entirely", () => {
    const unavailable = createMotionMachine("tilt", "medium", false);
    expect(processSample(unavailable, sample(1), 0).machine.state).toBe("unavailable");
    const disabled = createMotionMachine("swipe-only", "medium", true);
    expect(processSample(disabled, sample(1), 0).machine.state).toBe("disabled");
  });

  it("preview progress is clamped to ±1 and zero in the dead zone", () => {
    expect(computePreviewProgress(0, cfg)).toBe(0);
    expect(computePreviewProgress(cfg.deadZoneRad, cfg)).toBe(0);
    expect(computePreviewProgress(10, cfg)).toBe(1);
    expect(computePreviewProgress(-10, cfg)).toBe(-1);
  });

  it("hold windows across sensitivities stay within the 300–450ms mission band", () => {
    for (const preset of Object.values(MOTION_CONFIGS)) {
      expect(preset.holdMs).toBeGreaterThanOrEqual(300);
      expect(preset.holdMs).toBeLessThanOrEqual(450);
      expect(preset.previewRad).toBeGreaterThanOrEqual(preset.deadZoneRad);
      expect(preset.commitRad).toBeGreaterThan(preset.previewRad);
    }
  });
});

/**
 * The loop policy, tested as a state machine rather than through a player.
 *
 * Requirements 3–8 describe a sequence that takes fifteen real seconds to
 * observe and that depends on a decoder, a network and a device clock. Driven
 * through `advancePreviewLoop` they become a list of positions and the
 * decisions that follow from them, which is both faster and strictly more
 * honest: a test that watched a real player for two loops would pass on a
 * machine where the seek silently failed and playback merely ran on past five
 * seconds.
 *
 * The case that actually motivates the design is `mid-seek reports`. It cannot
 * be caught by inspection and it is what produces judder on a real phone.
 */
import {
  DISCOVERY_PREVIEW_LOOP_WINDOW_MS,
  DISCOVERY_PREVIEW_PROGRESS_INTERVAL_MS,
  advancePreviewLoop,
  initialPreviewLoopState
} from "../previewPlayback";

/**
 * Run a list of position reports through the machine, collecting every rewind.
 *
 * Returns the positions at which a rewind was issued, so a test can assert
 * *when* the loop turned over rather than merely how many times.
 */
function drive(positions: number[]) {
  let state = initialPreviewLoopState();
  const rewindsAt: number[] = [];
  for (const position of positions) {
    const result = advancePreviewLoop(state, position);
    state = result.state;
    if (result.action === "rewind") rewindsAt.push(position);
  }
  return { rewindsAt, state };
}

/** Positions a player would report over one pass, at the real callback rate. */
function passUpTo(limit: number) {
  const positions: number[] = [];
  for (let t = 0; t <= limit; t += DISCOVERY_PREVIEW_PROGRESS_INTERVAL_MS) positions.push(t);
  return positions;
}

describe("the loop window is five seconds", () => {
  it("caps the preview at five seconds", () => {
    expect(DISCOVERY_PREVIEW_LOOP_WINDOW_MS).toBe(5000);
  });

  it("reports position often enough that the overshoot is imperceptible", () => {
    // The rewind can only be issued on a callback, so the interval *is* the
    // worst-case overshoot past five seconds. A quarter second would be visible
    // as playing into second six; this must stay well under it.
    expect(DISCOVERY_PREVIEW_PROGRESS_INTERVAL_MS).toBeLessThanOrEqual(150);
    expect(DISCOVERY_PREVIEW_PROGRESS_INTERVAL_MS).toBeGreaterThan(0);
  });
});

describe("requirements 3, 4 — playback reaches five seconds and seeks back to zero", () => {
  it("issues no rewind for any position inside the window", () => {
    const { rewindsAt } = drive(passUpTo(DISCOVERY_PREVIEW_LOOP_WINDOW_MS - 1));

    expect(rewindsAt).toEqual([]);
  });

  it("issues exactly one rewind on reaching the window", () => {
    const { rewindsAt } = drive([4000, 4875, 5000]);

    expect(rewindsAt).toEqual([5000]);
  });

  it("rewinds on the first report at or past the window, not on an exact match only", () => {
    // The player is under no obligation to land on 5000 exactly. A test written
    // against equality would pass here and never fire on a device.
    const { rewindsAt } = drive([4900, 5013]);

    expect(rewindsAt).toEqual([5013]);
  });
});

describe("requirements 5, 6, 7, 8 — the loop continues, pass after pass", () => {
  it("turns over once per pass across four consecutive passes", () => {
    const positions: number[] = [];
    for (let pass = 0; pass < 4; pass += 1) {
      positions.push(...passUpTo(DISCOVERY_PREVIEW_LOOP_WINDOW_MS));
    }

    const { rewindsAt } = drive(positions);

    // Four passes, four turnovers — not three, and not one per callback.
    expect(rewindsAt).toHaveLength(4);
    expect(rewindsAt.every((position) => position === DISCOVERY_PREVIEW_LOOP_WINDOW_MS)).toBe(true);
  });

  it("keeps looping indefinitely rather than settling after some number of passes", () => {
    const positions: number[] = [];
    for (let pass = 0; pass < 50; pass += 1) positions.push(0, 2500, 5000);

    const { rewindsAt } = drive(positions);

    expect(rewindsAt).toHaveLength(50);
  });

  it("re-arms as soon as the player confirms it is back inside the window", () => {
    const first = advancePreviewLoop(initialPreviewLoopState(), 5000);
    expect(first.action).toBe("rewind");

    const landed = advancePreviewLoop(first.state, 0);
    expect(landed.state.rewinding).toBe(false);

    // Armed again: the very next crossing must turn over.
    expect(advancePreviewLoop(landed.state, 5000).action).toBe("rewind");
  });
});

describe("mid-seek reports — why the rewind is latched", () => {
  it("does not issue a second seek while the first is still in flight", () => {
    // A seek takes time. Every callback that arrives before it lands still
    // reads a position past the window; without the latch each one fires
    // another seek and the loop point judders.
    const { rewindsAt } = drive([5000, 5125, 5250, 5375, 5500]);

    expect(rewindsAt).toEqual([5000]);
  });

  it("stays latched through a long stall rather than accumulating seeks", () => {
    const stalled = Array.from({ length: 200 }, (_, index) => 5000 + index * 125);

    const { rewindsAt, state } = drive(stalled);

    expect(rewindsAt).toHaveLength(1);
    expect(state.rewinding).toBe(true);
  });

  it("resumes normal looping after a stalled seek finally lands", () => {
    const { rewindsAt } = drive([5000, 5125, 5250, 0, 2500, 5000]);

    expect(rewindsAt).toEqual([5000, 5000]);
  });
});

describe("reports that say nothing", () => {
  it("ignores a position the player has not established yet", () => {
    for (const position of [Number.NaN, Number.POSITIVE_INFINITY, -1]) {
      expect(advancePreviewLoop(initialPreviewLoopState(), position).action).toBe("none");
    }
  });

  it("leaves the latch untouched by an unusable report", () => {
    const latched = advancePreviewLoop(initialPreviewLoopState(), 5000).state;

    // NaN must not be read as "back inside the window" and clear the latch —
    // that would let the next report fire a duplicate seek.
    expect(advancePreviewLoop(latched, Number.NaN).state.rewinding).toBe(true);
  });
});

describe("a clip shorter than the window", () => {
  it("never asks for a rewind, because the player's own loop wraps it", () => {
    // A three-second clip reports 0…2900 and then wraps to 0 natively. The
    // guard must stay out of the way: a seek here would fight the native loop.
    const { rewindsAt } = drive([0, 1500, 2900, 0, 1500, 2900, 0]);

    expect(rewindsAt).toEqual([]);
  });
});

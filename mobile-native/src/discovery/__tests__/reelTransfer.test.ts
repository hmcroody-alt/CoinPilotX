/**
 * The handoff slot: consumed once, never wrong, never stale.
 *
 * Every assertion here is about a way the *wrong* reel could reach the player,
 * because that is the only failure this module can have. Handing over the right
 * reel is easy; the interesting cases are all the ones where the slot still has
 * something in it and it should not be used.
 */
import type { PulseReel } from "../../api/reels";
import {
  REEL_TRANSFER_TTL_MS,
  __reelTransferStaged,
  clearReelTransfer,
  peekReelTransfer,
  stageReelTransfer,
  takeReelTransfer
} from "../reelTransfer";

const reel = (id: number): PulseReel => ({ id, reel_id: id, title: `reel ${id}` }) as PulseReel;

beforeEach(() => clearReelTransfer());
afterEach(() => clearReelTransfer());

describe("staging", () => {
  it("hands back a nonce and parks the reel", () => {
    const nonce = stageReelTransfer(reel(42));

    expect(nonce).toEqual(expect.stringContaining("42"));
    expect(peekReelTransfer(42)).toEqual(reel(42));
  });

  it("gives two taps on the same reel two different nonces", () => {
    // The player keys its reload on the nonce. If a repeat tap produced the same
    // one, the second tap on an already-open reel would do nothing at all, which
    // reads as a dead card.
    const first = stageReelTransfer(reel(42));
    const second = stageReelTransfer(reel(42));

    expect(first).not.toBe(second);
  });

  it("refuses a reel with no id, so the caller can decline to navigate", () => {
    expect(stageReelTransfer({ title: "no id" } as PulseReel)).toBeNull();
    expect(stageReelTransfer({ id: 0 } as PulseReel)).toBeNull();
    expect(stageReelTransfer({ id: Number.NaN } as PulseReel)).toBeNull();
    expect(__reelTransferStaged()).toBe(false);
  });

  it("accepts a reel identified only by reel_id", () => {
    // The feed serialiser emits both; some lanes have historically omitted `id`.
    expect(stageReelTransfer({ reel_id: 7 } as PulseReel)).toEqual(expect.stringContaining("7"));
    expect(peekReelTransfer(7)).toEqual({ reel_id: 7 });
  });
});

describe("taking", () => {
  it("returns the reel once and only once", () => {
    stageReelTransfer(reel(42));

    expect(takeReelTransfer(42)).toEqual(reel(42));
    expect(takeReelTransfer(42)).toBeNull();
  });

  it("returns nothing for a different reel, and clears the slot", () => {
    // The parked navigation is not happening. Leaving reel 42 in the slot would
    // let it seed a later, unrelated visit to the player.
    stageReelTransfer(reel(42));

    expect(takeReelTransfer(99)).toBeNull();
    expect(__reelTransferStaged()).toBe(false);
  });

  it("returns nothing when nothing was staged", () => {
    expect(takeReelTransfer(42)).toBeNull();
    expect(peekReelTransfer(42)).toBeNull();
  });

  it("compares ids numerically, so a string id from a param still matches", () => {
    stageReelTransfer(reel(42));

    expect(takeReelTransfer("42" as unknown as number)).toEqual(reel(42));
  });
});

describe("expiry", () => {
  it("stops handing over a reel that has been parked too long", () => {
    const staged = 1_000_000;
    stageReelTransfer(reel(42), staged);

    expect(peekReelTransfer(42, staged + REEL_TRANSFER_TTL_MS)).toEqual(reel(42));
    expect(peekReelTransfer(42, staged + REEL_TRANSFER_TTL_MS + 1)).toBeNull();
  });

  it("clears the slot when it finds it expired", () => {
    const staged = 1_000_000;
    stageReelTransfer(reel(42), staged);

    expect(takeReelTransfer(42, staged + REEL_TRANSFER_TTL_MS + 1)).toBeNull();
    expect(__reelTransferStaged()).toBe(false);
  });
});

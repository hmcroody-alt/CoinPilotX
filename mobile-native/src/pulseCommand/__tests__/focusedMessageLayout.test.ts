/**
 * The focused-message overlay's arithmetic, at the edges where it is wrong.
 *
 * The happy case -- a message in the middle of the screen, room above and
 * below -- is the one a screenshot already proves. These tests are about the
 * first message in a thread, the last one, and a message too tall for the
 * window, because those are the presses that produce a reaction strip off the
 * top of the screen or a menu whose last item is under the home indicator.
 *
 * Mutation contract:
 *   - removing the top clamp must turn "near the top" red;
 *   - removing the bottom clamp must turn "near the bottom" red;
 *   - shrinking the bubble before the menu must turn "gives up menu height
 *     before bubble height" red;
 *   - letting the menu shrink past MIN_MENU_HEIGHT must turn "never shrinks
 *     the menu below its floor" red.
 */

import {
  FOCUS_GAP,
  MIN_MENU_HEIGHT,
  REACTION_STRIP_HEIGHT,
  focusedMessageLayout
} from "../focusedMessageLayout";

/** A phone-shaped window with a notch and a home indicator already inset. */
const VIEWPORT = { top: 60, bottom: 800, width: 393 };

function layout(bubble: Partial<{ x: number; y: number; width: number; height: number }>, menuHeight = 300) {
  return focusedMessageLayout({
    bubble: { x: 16, y: 400, width: 280, height: 64, ...bubble },
    viewport: VIEWPORT,
    menuHeight
  });
}

describe("a message with room on both sides", () => {
  it("leaves the bubble exactly where the finger left it", () => {
    const result = layout({ y: 400, height: 64 });
    expect(result.bubbleTop).toBe(400);
    expect(result.shifted).toBe(false);
  });

  it("puts the reactions above and the actions below, and nothing overlaps", () => {
    const result = layout({ y: 400, height: 64 });
    expect(result.reactionsTop).toBe(400 - REACTION_STRIP_HEIGHT - FOCUS_GAP);
    expect(result.reactionsTop + REACTION_STRIP_HEIGHT).toBeLessThanOrEqual(result.bubbleTop);
    expect(result.bubbleTop + result.bubbleHeight).toBeLessThanOrEqual(result.menuTop);
  });

  it("does not shrink anything it did not have to", () => {
    const result = layout({ y: 400, height: 64 }, 300);
    expect(result.menuHeight).toBe(300);
    expect(result.menuScrolls).toBe(false);
    expect(result.bubbleClipped).toBe(false);
  });
});

describe("a message near the top of the screen", () => {
  it("pushes the group down rather than drawing reactions off-screen", () => {
    // The first message in a thread: reactions would want to start at y=4.
    const result = layout({ y: 70, height: 64 });
    expect(result.reactionsTop).toBeGreaterThanOrEqual(VIEWPORT.top);
    expect(result.shifted).toBe(true);
  });

  it("moves the bubble down by exactly the amount the strip needed", () => {
    const result = layout({ y: 70, height: 64 });
    expect(result.reactionsTop).toBe(VIEWPORT.top);
    expect(result.bubbleTop).toBe(VIEWPORT.top + REACTION_STRIP_HEIGHT + FOCUS_GAP);
  });
});

describe("a message near the bottom of the screen", () => {
  it("lifts the group so the last action is not under the home indicator", () => {
    // The newest message, sitting just above the composer.
    const result = layout({ y: 700, height: 64 }, 300);
    expect(result.menuTop + result.menuHeight).toBeLessThanOrEqual(VIEWPORT.bottom);
    expect(result.shifted).toBe(true);
  });

  it("lifts it no further than it had to", () => {
    const result = layout({ y: 700, height: 64 }, 300);
    // Bottom-aligned: the group ends exactly at the viewport floor.
    expect(result.menuTop + result.menuHeight).toBe(VIEWPORT.bottom);
    // And it went up, not down.
    expect(result.bubbleTop).toBeLessThan(700);
  });

  it("keeps a short menu's bubble closer to where it was than a tall menu's", () => {
    const shortMenu = layout({ y: 700, height: 64 }, 200);
    const tallMenu = layout({ y: 700, height: 64 }, 380);
    expect(shortMenu.bubbleTop).toBeGreaterThan(tallMenu.bubbleTop);
  });
});

describe("when the three bands do not fit at all", () => {
  it("gives up menu height before bubble height", () => {
    // A tall bubble plus a tall menu in a short window.
    const result = focusedMessageLayout({
      bubble: { x: 16, y: 200, width: 280, height: 380 },
      viewport: { top: 0, bottom: 640, width: 393 },
      menuHeight: 400
    });
    expect(result.menuScrolls).toBe(true);
    expect(result.bubbleClipped).toBe(false);
    expect(result.bubbleHeight).toBe(380);
  });

  it("never shrinks the menu below its floor, clipping the bubble instead", () => {
    // Bubble so tall that even an empty menu would not fit beside it.
    const result = focusedMessageLayout({
      bubble: { x: 16, y: 100, width: 280, height: 700 },
      viewport: { top: 0, bottom: 640, width: 393 },
      menuHeight: 400
    });
    expect(result.menuHeight).toBe(MIN_MENU_HEIGHT);
    expect(result.bubbleClipped).toBe(true);
  });

  it("still fits inside the viewport in the worst case", () => {
    const result = focusedMessageLayout({
      bubble: { x: 16, y: 100, width: 280, height: 700 },
      viewport: { top: 0, bottom: 640, width: 393 },
      menuHeight: 400
    });
    expect(result.reactionsTop).toBeGreaterThanOrEqual(0);
    expect(result.menuTop + result.menuHeight).toBeLessThanOrEqual(640);
  });

  it("does not produce a negative box even in a window smaller than the chrome", () => {
    const result = focusedMessageLayout({
      bubble: { x: 16, y: 10, width: 280, height: 200 },
      viewport: { top: 0, bottom: 120, width: 393 },
      menuHeight: 300
    });
    expect(result.bubbleHeight).toBeGreaterThanOrEqual(0);
    expect(result.menuHeight).toBeGreaterThanOrEqual(0);
  });
});

describe("a message that cannot be reacted to", () => {
  it("does not reserve a gap for a strip it is not drawing", () => {
    const withStrip = focusedMessageLayout({
      bubble: { x: 16, y: 400, width: 280, height: 64 },
      viewport: VIEWPORT,
      menuHeight: 300
    });
    const withoutStrip = focusedMessageLayout({
      bubble: { x: 16, y: 400, width: 280, height: 64 },
      viewport: VIEWPORT,
      menuHeight: 300,
      reactionsHeight: 0
    });
    // With no strip the bubble sits at its own coordinates and the group
    // starts there too -- no empty band above it.
    expect(withoutStrip.reactionsTop).toBe(withoutStrip.bubbleTop);
    expect(withStrip.bubbleTop - withStrip.reactionsTop).toBe(REACTION_STRIP_HEIGHT + FOCUS_GAP);
  });
});

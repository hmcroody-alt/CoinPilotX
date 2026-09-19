/**
 * Where the focused message, its reaction strip and its action menu go.
 *
 * A long press is supposed to feel like the conversation stepped back and left
 * one message lit. That only reads as "this message" if the bubble stays where
 * the finger left it -- a menu that slides up from the bottom of the screen is
 * a menu about the app, not about the thing under your thumb.
 *
 * So the default is: nothing moves. Reactions sit above the bubble, actions
 * below it, and the bubble keeps the coordinates it already had. Everything
 * here is about the cases where that is impossible -- a message near the top
 * of the screen has no room above it for reactions, one near the bottom has
 * no room below for a menu, and a long message in a short window has room for
 * neither.
 *
 * This is a pure function of rectangles for one reason: the interesting part
 * is the arithmetic at the edges, and the arithmetic at the edges is exactly
 * what is impossible to see in a screenshot of the happy case. The first
 * message in a thread and the last one are the two most likely to be long
 * pressed and the two most likely to overflow.
 */

export type Rect = { x: number; y: number; width: number; height: number };

/** The usable band of the screen, already inset for safe areas and keyboard. */
export type Viewport = { top: number; bottom: number; width: number };

export type FocusedMessageLayout = {
  /** Top of the reaction strip, in window coordinates. */
  reactionsTop: number;
  /** Top of the focused bubble. Equals the original `y` unless space forced a move. */
  bubbleTop: number;
  /** Height the bubble is allowed to draw in; less than its natural height only when clipped. */
  bubbleHeight: number;
  /** Top of the action menu. */
  menuTop: number;
  /** Height the menu may use. Below its natural height means it must scroll. */
  menuHeight: number;
  /** True when the bubble could not stay where the user pressed it. */
  shifted: boolean;
  /** True when the menu had to be shortened and therefore has to scroll. */
  menuScrolls: boolean;
  /** True when the bubble itself had to be clipped to fit the group. */
  bubbleClipped: boolean;
};

/** Reaction strip is a fixed-height row of glyph buttons. */
export const REACTION_STRIP_HEIGHT = 56;
/** Breathing room between the three bands. */
export const FOCUS_GAP = 10;
/**
 * The menu never shrinks below this. Past this point a scrolling menu is more
 * usable than a shorter one, and the bubble gives up height instead -- the
 * actions are why the overlay is open, the bubble is context.
 */
export const MIN_MENU_HEIGHT = 176;

export function focusedMessageLayout(input: {
  bubble: Rect;
  viewport: Viewport;
  /** Natural height of the action menu at this message's action count. */
  menuHeight: number;
  /** Omitted or zero when the message cannot be reacted to. */
  reactionsHeight?: number;
}): FocusedMessageLayout {
  const { bubble, viewport } = input;
  const reactionsHeight = Math.max(0, input.reactionsHeight ?? REACTION_STRIP_HEIGHT);
  // A reaction strip of zero height should not also claim a gap.
  const topGap = reactionsHeight > 0 ? FOCUS_GAP : 0;
  const available = Math.max(0, viewport.bottom - viewport.top);

  /**
   * Shrink order when the three bands do not fit: the menu gives up height
   * first, down to `MIN_MENU_HEIGHT`, and only then does the bubble get
   * clipped. Reactions never shrink -- a half-height row of emoji is not a
   * smaller control, it is a broken one.
   */
  const chromeHeight = reactionsHeight + topGap + FOCUS_GAP;
  let menuHeight = Math.max(0, input.menuHeight);
  let bubbleHeight = Math.max(0, bubble.height);

  let overflow = chromeHeight + bubbleHeight + menuHeight - available;
  if (overflow > 0) {
    const menuGive = Math.min(overflow, Math.max(0, menuHeight - MIN_MENU_HEIGHT));
    menuHeight -= menuGive;
    overflow -= menuGive;
  }
  if (overflow > 0) {
    const bubbleGive = Math.min(overflow, bubbleHeight);
    bubbleHeight -= bubbleGive;
    overflow -= bubbleGive;
  }
  if (overflow > 0) {
    // Nothing left to give: the window is shorter than the minimum menu plus
    // the reaction strip. Let the menu take what remains rather than render
    // a negative box.
    menuHeight = Math.max(0, menuHeight - overflow);
  }

  const groupHeight = chromeHeight + bubbleHeight + menuHeight;

  // Preferred position keeps the bubble exactly where it was pressed.
  const preferredTop = bubble.y - reactionsHeight - topGap;
  const lowestTop = viewport.bottom - groupHeight;
  const reactionsTop = Math.max(viewport.top, Math.min(preferredTop, lowestTop));

  const bubbleTop = reactionsTop + reactionsHeight + topGap;
  const menuTop = bubbleTop + bubbleHeight + FOCUS_GAP;

  return {
    reactionsTop,
    bubbleTop,
    bubbleHeight,
    menuTop,
    menuHeight,
    shifted: Math.round(bubbleTop) !== Math.round(bubble.y),
    menuScrolls: menuHeight < Math.max(0, input.menuHeight),
    bubbleClipped: bubbleHeight < Math.max(0, bubble.height)
  };
}

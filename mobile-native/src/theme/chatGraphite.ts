/**
 * GRAPHITE — the direct-message conversation surface.
 *
 * Scoped to one screen on purpose. `buildTheme` pins `activeTheme = "dark"`, so
 * a new `ThemeMode` would type-check and render nowhere; this ships the way
 * `messengerTheme` and `presenceTheme` do, as a fixed token set the conversation
 * screen consumes directly. Nothing here mutates `colors` or `PulseBackground`,
 * so no other surface changes.
 *
 * ## The shared steps now come from `graphite.ts`
 *
 * Six of the values below — chrome, both canvas stops, the sunken step and the
 * two text weights — were promoted into `theme/graphite.ts` when the Profile
 * surface adopted the same system. They are re-exported through this object by
 * reference, not re-typed, so the two screens cannot drift: retuning the canvas
 * in one place moves both, which is the whole point of the promotion. The values
 * are byte-identical to what this file shipped with, and
 * `__tests__/chatGraphiteContrast.test.ts` pins every one of them literally, so
 * the refactor is proven to be a refactor.
 *
 * Everything still declared inline here is conversation-only: the bubbles, their
 * borders, the reply inset, the sender accent, the control fill and the shadow.
 * A Profile change must not be able to move a chat bubble.
 *
 * ## The hierarchy, as luminance
 *
 * Header and footer are the darkest anchored surfaces, the canvas is a middle
 * graphite, and the bubbles sit one step above it. Written as relative
 * luminance, which is the only form in which "one step lighter" is checkable:
 *
 *   composerSurface 0.019 < headerSurface 0.027 < canvasBottom 0.047
 *                          < canvasTop 0.050 < outgoing 0.091 < incoming 0.094
 *
 * Outgoing and incoming land within 0.4% of each other in luminance, which is
 * deliberate: they are told apart by hue, by which edge of the screen they hang
 * off, and by which corner is squared — never by weight. That is what keeps the
 * screen legible in grayscale and under a red/green deficiency, and it is why
 * the blue is a *deep* cobalt rather than the brighter blue a sender bubble
 * usually gets.
 *
 * ## Two measured deviations from the approved baseline
 *
 * `secondaryText` is `#C8D0DB` rather than the specified `#BEC6D1`, and
 * `senderAccent` is `#7BDFFF` rather than `colors.accentStrong` (`#61D8FF`).
 * Both are the same hue, both are lifts of roughly one step, and both exist for
 * one reason: on the incoming bubble the specified values measure 4.24:1 and
 * 4.44:1, and timestamps, delivery labels and the sender name are all small
 * text, so 4.5:1 is the bar they have to clear rather than 3:1. The lifted
 * values measure 4.70:1 and 4.81:1. Every other value here is the approved
 * baseline unchanged.
 */

import { graphite } from "./graphite";

export const chatGraphite = {
  /**
   * Header, footer and both safe areas. The darkest thing on screen — a chrome
   * container that out-brightens its own content reads as the subject.
   */
  headerSurface: graphite.chrome,

  /** The conversation canvas, top to bottom. A single restrained vertical run. */
  canvasTop: graphite.canvasTop,
  canvasBottom: graphite.canvasBottom,

  /** Incoming/recipient bubbles. One visible step lighter than the canvas. */
  incomingSurface: "#505761",
  incomingBorder: "rgba(214, 222, 232, 0.20)",

  /** Outgoing/sender bubbles. Kept blue so the sender is recognised instantly. */
  outgoingSurface: "#24549B",
  outgoingBorder: "#4D8FE9",

  /** The composer field, one step under the footer it sits in. */
  composerSurface: graphite.sunken,

  /**
   * An inset inside a bubble: reply previews and quoted content. Translucent
   * rather than solid so the same token works inside the gray incoming bubble
   * and the blue outgoing one without either needing its own value.
   */
  insetSurface: "rgba(20, 25, 32, 0.30)",

  primaryText: graphite.primaryText,
  /** Timestamps, delivery labels, previews. See the deviation note above. */
  secondaryText: graphite.secondaryText,

  /** Sender names and the reply rail. See the deviation note above. */
  senderAccent: "#7BDFFF",

  /** Header/canvas separation, control edges. Quiet by design. */
  quietDivider: graphite.quietDivider,

  /** Icon-button fill in the header and the composer. */
  controlSurface: "#323842",

  /** Neutral, not teal: a lift under the footer, not a glow around it. */
  shadow: "#080B0F",
  shadowOpacity: 0.18
} as const;

export type ChatGraphite = typeof chatGraphite;

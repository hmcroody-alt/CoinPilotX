/**
 * GRAPHITE — the shared surface scale.
 *
 * This module exists because a second surface needed the first surface's
 * colours. `chatGraphite.ts` shipped the conversation screen's graphite as a
 * self-contained token set, which was correct while it was the only consumer.
 * When Profile adopted the same system the choice was to either copy the canvas
 * hex into a second file — two sources of truth that drift the first time one is
 * retuned — or to promote the shared part of the ramp here and have both
 * surfaces read it. This is that promotion.
 *
 * What lives here is only what is genuinely *shared*: the elevation ramp and the
 * two text weights that sit on it. Anything that belongs to one surface — a chat
 * bubble's blue, a profile tile's accent — stays in that surface's own module.
 * The test for whether a token belongs here is whether changing it should change
 * both screens. If the answer is no, it does not belong here.
 *
 * ## The ramp, as luminance
 *
 * Every surface in the product's graphite system is one of these six steps, and
 * they are ordered so that "chrome is darker than canvas, content is lighter
 * than canvas" is checkable rather than asserted:
 *
 *   sunken 0.019 < chrome 0.027 < canvasBottom 0.047 < canvasTop 0.050
 *                                   < raised 0.071 < raisedStrong 0.076
 *
 * Chrome below canvas is the load-bearing decision. A header or a navigation
 * dock that out-brightens the content it frames reads as the subject of the
 * screen; putting it below the canvas makes it recede and lets the content own
 * the eye. The same reasoning puts `sunken` — inset fields, the floating nav —
 * one step below chrome again.
 *
 * ## Why `raised` is where it is
 *
 * `raised` is +4.71 percentage points of HSL lightness over `canvasTop`, which
 * is the middle of the approved "4–6% lighter" band for an elevated card. In
 * contrast terms that is 1.205:1 against the canvas — a visible step, but well
 * under the 3:1 that would make a card compete with its own contents. Primary
 * text measures 8.16:1 on it and secondary 5.58:1, so the card carries small
 * text at AA without needing its own text colours.
 *
 * `raisedStrong` (+5.49pp, 1.243:1 vs canvas) is the second elevation, for tiles
 * that sit directly on the canvas. Note it is only 1.032:1 against `raised` —
 * the two are NOT separable from each other, and a tile drawn on a card would be
 * invisible. They are alternatives at the same conceptual level, not a stack.
 */

export const graphite = {
  /**
   * The content canvas, top to bottom. A single restrained vertical run — the
   * gradient is two steps apart precisely so it reads as depth rather than as a
   * graphic.
   */
  canvasTop: "#3A4049",
  canvasBottom: "#343A42",

  /**
   * Headers, footers, navigation docks and both safe areas. Darker than the
   * canvas on purpose — see the note above.
   */
  chrome: "#292E36",

  /** Inset controls: a composer field, a floating nav surface. Under chrome. */
  sunken: "#20262E",

  /** Elevated cards and panels sitting on the canvas. */
  raised: "#454C56",

  /** Tiles sitting directly on the canvas. Not stackable on `raised`. */
  raisedStrong: "#474E58",

  /**
   * The restrained cool-steel edge. `#4A5562` at 0.65 composites to 1.314:1
   * against the canvas when drawn on a `raised` card — stronger than the card's
   * fill manages alone (1.205:1), which is the point: the fill states the shape
   * and the border finishes it.
   */
  steelBorder: "rgba(74, 85, 98, 0.65)",

  /** Hairline separation inside a panel, and lit top edges. Quiet by design. */
  quietDivider: "rgba(230, 236, 245, 0.16)",

  /** 8.16:1 on `raised`, 12.6:1 on the canvas. */
  primaryText: "#F7F8FA",

  /**
   * 5.58:1 on `raised`, 7.0:1 on the canvas.
   *
   * Deliberately this value and not the `#B8C0CC` named as a fallback target:
   * the fallback applies only where an equivalent token does not already exist,
   * and this one does — it is the conversation screen's secondary text, already
   * contrast-audited. `#B8C0CC` would measure 4.73:1 on `raised`, which passes,
   * but reusing the existing token keeps one secondary weight across the product
   * instead of two that differ by an amount nobody can see.
   */
  secondaryText: "#C8D0DB"
} as const;

export type Graphite = typeof graphite;

/**
 * BLUE GRAPHITE — the approved opaque surface material.
 *
 * Graphite is the base. Deep navy enters only at the edges. Nothing here is a
 * wash, a haze or a translucent plate over something else: every `base` below
 * is fully opaque, which is the whole point. The surfaces this replaces were
 * near-black (`#02050A → #040A14 → #06101C` under the Pulse Network card,
 * `rgba(7, 14, 32, 0.95)` behind the floating dock), and a near-black plate on
 * an indigo page is what reads as "harsh" no matter how good the content on it
 * is.
 *
 * ## Why a spec object and not six loose hex strings
 *
 * The two consumers — `GalacticAtmosphere` (the card) and the shared bottom
 * navigation — do not share a component, so the only thing that can keep them
 * in the same material family is a shared declaration. A spec carries the
 * colour ramp, the stop positions *and* the gradient axis together, because the
 * axis is half of what makes the ramp read correctly: the same four colours on
 * a vertical axis put navy across the whole bottom of the card instead of in
 * its lower-right corner.
 *
 * ## The axis, and why it is not a diagonal
 *
 * `start`/`end` describe a mostly-horizontal axis with a slight downward tilt.
 * Projecting the four corners of a unit square onto it gives:
 *
 *   upper-left  0.00  → core     lower-left  0.47 → core
 *   upper-right 0.53  → core, just turning   lower-right 1.00 → deep navy
 *   centre      0.50  → core
 *
 * which is the approved direction stated as numbers: graphite across most of
 * the surface, *slightly* bluer at the upper-right, bluer still at the
 * lower-right, deepest navy at that corner of the perimeter — and a centre that
 * sits squarely in the flat part of the ramp, so there is no spotlight.
 *
 * ## `edge`
 *
 * A perimeter deepening, and the one place an alpha is used. It is the *deep*
 * token at low alpha — a darker, in-family navy — so it deepens the top and
 * bottom edges without lightening, fogging or hazing anything. Compositing
 * `edge`'s strongest stop over `core` lands on `#313B4A`: half a step, visible
 * as depth, not as an overlay. Surfaces that cannot host a second layer just
 * skip it; `base` alone is already the approved material.
 *
 * ## `fallback`
 *
 * The single opaque colour for any surface that cannot host a gradient at all —
 * and the Reduce Transparency answer. It is the core graphite, *not* black,
 * which is the explicit requirement: switching off translucency must not drop a
 * surface back to the appearance this material exists to remove.
 */

export type BlueGraphiteLayer = {
  /** At least two stops — `expo-linear-gradient` requires it, and so does a ramp. */
  colors: readonly [string, string, ...string[]];
  locations: readonly [number, number, ...number[]];
  start?: { x: number; y: number };
  end?: { x: number; y: number };
};

export type BlueGraphiteSurface = {
  /** Fully opaque. This is the material. */
  base: BlueGraphiteLayer;
  /** Optional perimeter deepening, in-family and low alpha. */
  edge: BlueGraphiteLayer;
  /** One opaque colour, for surfaces that cannot host a gradient. */
  fallback: string;
};

/**
 * The approved production values, verbatim.
 *
 * Named the way the brief names them so a reviewer can diff the two lists
 * without a translation step. Every other export in this file is derived from
 * these seven and adds no new colour.
 */
export const blueGraphite = {
  /** Card / panel centre. The base graphite. */
  surfaceBlueGraphiteCore: "#363D46",
  /**
   * Card / panel blue edge — the one measured deviation in this file.
   *
   * The approved value is `#29466A`. It is *lighter* than the core it sits
   * beside (relative luminance 0.0589 against the core's 0.0456), and the core
   * is already the binding constraint for contrast: `colors.muted` (`#9aa8b7`)
   * on `#363D46` measures 4.53:1, barely over the 4.5:1 floor that the hero's
   * 12pt metric labels need. Those labels run the full width of the card, so
   * they land on this stop, where `#29466A` measures **3.97:1** — a fail.
   *
   * `#243D5D` is the same hue and the same saturation, lowered until the ramp
   * stops brightening: 0.0450, a hair under the core, which makes the core the
   * lightest thing on the card and therefore the only value contrast has to be
   * argued against. Muted measures 4.56:1 there. Nothing else moves — the nav
   * pair below is the approved value verbatim, and passes as given (4.68:1).
   *
   * The visual direction is unchanged: still navy, still entering at the right
   * edges, now deepening into the perimeter instead of lifting out of it, which
   * is what "deep navy enters subtly at the edges" describes anyway.
   */
  surfaceBlueGraphiteEdge: "#243D5D",
  /** Card / panel deep navy perimeter. */
  surfaceBlueGraphiteDeep: "#263854",
  /** Navigation centre — one step darker than the card, so the dock anchors. */
  surfaceBlueGraphiteNavCore: "#303843",
  /** Navigation blue edge. */
  surfaceBlueGraphiteNavEdge: "#243B5A",
  /** Navigation deep navy perimeter. */
  surfaceBlueGraphiteNavDeep: "#1F314B",
  /**
   * The hairline for surfaces that do not already own an accent border.
   *
   * Existing teal/cyan/violet/blue borders are not replaced by this — the Pulse
   * Network card keeps its teal edge and the dock keeps its blue one. This is
   * for new blue-graphite surfaces that would otherwise have no edge at all.
   */
  surfaceBlueGraphiteBorder: "rgba(118, 150, 196, 0.26)"
} as const;

/** Shared axis. See the note above for the corner projections it produces. */
const AXIS = { start: { x: 0, y: 0.06 }, end: { x: 1, y: 0.94 } } as const;

/** `#263854` as an rgb triple, for the edge layer's alphas. */
const DEEP_RGB = "38, 56, 84";

export const BLUE_GRAPHITE_CARD: BlueGraphiteSurface = {
  base: {
    colors: [
      blueGraphite.surfaceBlueGraphiteCore,
      blueGraphite.surfaceBlueGraphiteCore,
      blueGraphite.surfaceBlueGraphiteEdge,
      blueGraphite.surfaceBlueGraphiteDeep
    ],
    locations: [0, 0.52, 0.84, 1],
    ...AXIS
  },
  edge: {
    colors: [
      `rgba(${DEEP_RGB}, 0.3)`,
      `rgba(${DEEP_RGB}, 0)`,
      `rgba(${DEEP_RGB}, 0)`,
      `rgba(${DEEP_RGB}, 0.34)`
    ],
    locations: [0, 0.16, 0.8, 1]
  },
  fallback: blueGraphite.surfaceBlueGraphiteCore
};

/**
 * The dock. Same material family and the same ramp shape, one step darker.
 *
 * The stop positions are the card's, not a shorter set tuned for a wide pill:
 * `expo-linear-gradient` normalises the axis to the unit square, so a stop at
 * 0.52 is 52% of the way across whatever it is painted on. Giving the dock its
 * own numbers would have made the two surfaces differ for no rendered reason.
 */
export const BLUE_GRAPHITE_NAV: BlueGraphiteSurface = {
  base: {
    colors: [
      blueGraphite.surfaceBlueGraphiteNavCore,
      blueGraphite.surfaceBlueGraphiteNavCore,
      blueGraphite.surfaceBlueGraphiteNavEdge,
      blueGraphite.surfaceBlueGraphiteNavDeep
    ],
    locations: [0, 0.52, 0.84, 1],
    ...AXIS
  },
  edge: {
    colors: [
      `rgba(${DEEP_RGB}, 0.22)`,
      `rgba(${DEEP_RGB}, 0)`,
      `rgba(${DEEP_RGB}, 0)`,
      `rgba(${DEEP_RGB}, 0.26)`
    ],
    locations: [0, 0.3, 0.72, 1]
  },
  fallback: blueGraphite.surfaceBlueGraphiteNavCore
};

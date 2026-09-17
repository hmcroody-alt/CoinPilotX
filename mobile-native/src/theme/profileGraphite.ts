/**
 * The Profile surface's graphite mapping.
 *
 * The shared ramp lives in `theme/graphite.ts` and is the same object the
 * conversation screen reads, so the Profile canvas is *the* approved chat
 * graphite rather than a second approximation of it. What this module adds is
 * the part that is specific to Profile: which of that ramp's steps each Profile
 * element sits on, and — the reason this is a function rather than a constant —
 * what happens under the themes graphite is not for.
 *
 * ## Why a resolver and not a token object
 *
 * Profile has to satisfy three statements at once:
 *
 *   - Dark/Graphite gets the layered graphite hierarchy.
 *   - Black stays *genuinely* black, not graphite-dimmed.
 *   - White and Light Futuristic stay readable light pages.
 *
 * A fixed token set can only satisfy the first. So the graphite ramp is applied
 * conditionally and every other theme keeps its own palette, which is also the
 * only version of this that cannot break a theme it was never designed for: for
 * a non-graphite theme this resolver returns nothing but that palette's existing
 * semantic roles.
 *
 * ## How "is this the graphite theme" is decided
 *
 * By the palette's own canvas value, matched against the dark palette's
 * `background`. Two other ways were available and both are worse:
 *
 *   - Luminance thresholding cannot do it. The dark palette's `#050910` has a
 *     relative luminance of 0.0026 and Black's `#000000` has 0.0000 — the two
 *     themes this has to tell apart are 0.26 percentage points apart, well
 *     inside the noise of any threshold that would also have to place the light
 *     palettes.
 *   - Passing `useTheme().mode` down would be semantically cleanest, but every
 *     Profile stylesheet is a module-scope `createThemedStyles` factory, and
 *     those are rebuilt by `applyPaletteToLegacyColors` bumping the palette
 *     epoch — i.e. by the palette, not by a React value. Reading the palette is
 *     therefore what actually re-runs at the right moment; threading the mode
 *     would require rewriting every style sheet into a hook first.
 *
 * `resolveProfileAccent` in `profileNeon.ts` already matches a palette value to
 * recover intent the object does not carry, so this is the established shape.
 *
 * Note that high-contrast dark overrides `background` to `#000000`, so enabling
 * Increased Contrast moves Profile off graphite and onto the black ramp. That is
 * the correct direction: the black ramp has strictly more separation.
 */

import { graphite } from "./graphite";
import type { Palette } from "./ThemeContext";

/** The dark palette's canvas. The one value that means "graphite applies". */
export const GRAPHITE_THEME_CANVAS = "#050910";

/**
 * Every surface and text weight Profile draws with, already resolved for the
 * active theme. Components read these instead of picking between `graphite`,
 * `colors` and a hardcoded hex at each call site.
 */
export type ProfileSurface = {
  /** The content canvas, top and bottom. Equal values mean "draw it flat". */
  canvasTop: string;
  canvasBottom: string;
  /** Chrome that frames content — recedes below the canvas where it can. */
  chrome: string;
  /** Elevated cards and panels sitting on the canvas. */
  raised: string;
  /**
   * Tiles sitting directly on the canvas.
   *
   * Only 1.032:1 against `raised` on the graphite ramp, so a tile drawn on a
   * card would be invisible. These are alternatives at one conceptual level,
   * not a two-step stack — see the note in `graphite.ts`.
   */
  raisedStrong: string;
  /** The 1px edge that finishes a card. Restrained cool steel on graphite. */
  border: string;
  /** Hairline separation inside a panel, and lit top edges. */
  divider: string;
  primaryText: string;
  secondaryText: string;
  /**
   * Middle stop of the *generated* hero field — the decorative cover drawn for
   * accounts that never uploaded one. Near-opaque canvas, so the field resolves
   * into the page instead of ending at a seam.
   *
   * Only ever used when there is no photo. Over a real cover this stop is
   * dropped entirely rather than lowered, because a full-bleed layer over the
   * middle of a photograph is fog no matter how weak it is, and the middle of a
   * cover photograph is where the face is.
   */
  coverFieldMid: string;
  /**
   * The single localized fade where a real cover meets the page body, expressed
   * as the canvas at ~45%.
   *
   * This is the *only* thing drawn over an uploaded cover. It is confined to the
   * bottom stop of a ramp whose middle stop sits at 74% of the hero, so the top
   * three quarters of the photograph — the subject — is untouched, and what the
   * fade buys is the contrast the avatar and the name need where they cross the
   * join.
   */
  coverScrim: string;
  /**
   * True only on the graphite ramp.
   *
   * Components use it to decide whether the graphite treatment's *decisions*
   * apply — not just its colours. A card fill 4–6% lighter than the canvas is
   * the separation on graphite; on White the page and the card are both white
   * and the border is the only thing carrying the edge, so a screen that keyed
   * card separation on fill alone would lose it there.
   */
  isGraphite: boolean;
};

/**
 * Resolve the Profile surface from whichever palette is live.
 *
 * Pure, and takes the palette explicitly, so every theme can be asserted
 * against without booting a provider — which matters because `buildTheme` pins
 * `activeTheme = "dark"`, making Black/White/Light Futuristic unreachable at
 * runtime today. Their mappings are still real code and still tested; the pin
 * is what stops a user from seeing them, not this module.
 */
export function resolveProfileSurface(palette: Palette): ProfileSurface {
  if (palette.background !== GRAPHITE_THEME_CANVAS) {
    // Not graphite's theme. Hand back this palette's own roles untouched: the
    // brief was a graphite Profile, not a Profile that overrides four themes.
    //
    // `raised` and `raisedStrong` are deliberately the same step here. Black's
    // ramp already separates them (#070a0d / #10161c), but White's `surface` IS
    // its `background`, so an elevation built from fill would vanish on exactly
    // the theme whose promise is a plain page. Both elevations therefore carry
    // the palette border, and the border is what states the edge.
    return {
      canvasTop: palette.background,
      canvasBottom: palette.background,
      chrome: palette.surfaceRaised,
      raised: palette.surface,
      raisedStrong: palette.surfaceRaised,
      border: palette.border,
      divider: palette.border,
      primaryText: palette.text,
      secondaryText: palette.muted,
      coverFieldMid: `${palette.background}f2`,
      coverScrim: `${palette.background}73`,
      isGraphite: false
    };
  }
  return {
    canvasTop: graphite.canvasTop,
    canvasBottom: graphite.canvasBottom,
    chrome: graphite.chrome,
    raised: graphite.raised,
    raisedStrong: graphite.raisedStrong,
    border: graphite.steelBorder,
    divider: graphite.quietDivider,
    primaryText: graphite.primaryText,
    secondaryText: graphite.secondaryText,
    // Eight-digit hex rather than `rgba()` so both themes spell alpha the same
    // way and one helper can read either.
    coverFieldMid: `${graphite.canvasTop}f2`,
    coverScrim: `${graphite.canvasBottom}73`,
    isGraphite: true
  };
}

/**
 * `resolveProfileSurface` with a one-entry cache, keyed on the palette values it
 * actually reads.
 *
 * Profile renders ~18 tiles, 4 stats and up to 5 actions per pass, and each of
 * those small components needs two or three of these strings. Resolving inside
 * each of them would allocate a `ProfileSurface` per component per render, which
 * is the "repeated theme-object creation during render" the brief rules out;
 * threading the object down as a prop instead would mean touching nine `<Action>`
 * call sites to move a value that is global by nature.
 *
 * The key is the six palette fields the resolver reads, not `background` alone
 * and not the palette's object identity. Identity is unusable because
 * `applyPaletteToLegacyColors` mutates the shared `legacyColors` object in place,
 * so it never changes. `background` alone is unsafe for a different reason: the
 * graphite branch does key only on it, but the *non*-graphite branch returns five
 * other roles, and high-contrast dark and Black share `#000000` while differing
 * elsewhere — so a background-keyed cache would hand Black's surfaces to
 * high-contrast dark. Keying on everything that is read is correct by
 * construction: identical inputs cannot have a different output from a pure
 * function.
 */
let surfaceCacheKey: string | null = null;
let surfaceCacheValue: ProfileSurface | null = null;

export function profileSurface(palette: Palette): ProfileSurface {
  const key = `${palette.background}|${palette.surface}|${palette.surfaceRaised}|${palette.border}|${palette.text}|${palette.muted}`;
  if (key !== surfaceCacheKey || !surfaceCacheValue) {
    surfaceCacheKey = key;
    surfaceCacheValue = resolveProfileSurface(palette);
  }
  return surfaceCacheValue;
}

/**
 * Press feedback for Profile controls.
 *
 * One value, in the brief's 120–180ms band, used by every Profile press so the
 * screen has a single tempo. Opacity rather than scale: a scale press on a
 * 25%-width grid tile visibly reflows nothing but still costs a layout read,
 * and opacity is what the rest of this screen already uses.
 */
export const PROFILE_PRESS_DURATION_MS = 140;

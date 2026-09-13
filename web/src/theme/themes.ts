/**
 * Which appearance the web client actually renders.
 *
 * This file is four lines of real code and a long explanation, because the
 * explanation is the part that stops the four lines from being "simplified"
 * into a theme switcher by someone who sees four palettes sitting in
 * `tokens.css` and reasonably concludes they are meant to be selectable.
 *
 * They are not. `mobile-native/src/theme/ThemeContext.tsx:212-214` reads:
 *
 *     // Dark is the only released appearance for now.
 *     // Keep the other theme implementations intact for future activation.
 *     const activeTheme: ThemeMode = "dark";
 *
 * `buildTheme` accepts the member's stored `appearance.theme` and then ignores
 * it when choosing a palette. Black, Light Futuristic and White are complete,
 * reviewed, and unreachable in the shipped binary -- native's own `__testing`
 * export exists because `ThemeProvider` has no way to produce them.
 *
 * The web client inherits that decision rather than making its own. A theme
 * switcher here would give browser members four appearances the app withholds,
 * which is the "native is the source of truth" rule broken in the direction
 * that is easy to miss: the obvious violation is restyling the app to match the
 * website, but shipping web-only product surface is the same inversion wearing
 * a friendlier face. The palettes are ported so that lifting the pin is one
 * line on each platform on the same day, instead of a web project that starts
 * after native is already done.
 *
 * `scripts/ops/native_theme_parity_gate.py` asserts this constant still equals
 * native's literal. If native activates theme selection and web does not, that
 * gate goes red -- which is the intended way for this file to be revisited.
 */

/** The appearance modes the native `ThemeMode` union defines. */
export const THEME_MODES = [
  "system",
  "dark",
  "black",
  "light_futuristic",
  "white",
] as const;

export type ThemeMode = (typeof THEME_MODES)[number];

/**
 * The pinned appearance. Mirrors `activeTheme` in native `buildTheme`.
 *
 * Not read from storage, not read from `prefers-color-scheme`, and that second
 * one is deliberate: honouring the OS scheme would resolve to Light Futuristic
 * for a member on a light-mode laptop, which is precisely the unreachable
 * palette this pin exists to keep unreachable.
 */
export const ACTIVE_THEME: ThemeMode = "dark";

/**
 * Apply the appearance to the document root.
 *
 * Accessibility state is separate from palette state because native treats them
 * separately: high contrast is a partial overlay over whatever palette is
 * active, not a palette of its own, and reduce-transparency is a derived flag
 * that native ORs with high contrast. `tokens.css` reproduces both with
 * attribute selectors, so this function only has to publish the inputs.
 */
export function applyTheme(
  root: HTMLElement,
  options: { highContrast?: boolean; reduceTransparency?: boolean } = {},
): void {
  root.setAttribute("data-theme", ACTIVE_THEME);

  // Native: `reduceTransparency: appearance.reduceTransparency || accessibility.highContrast`.
  // The OR is reproduced here rather than left to CSS so the attribute reflects
  // the effective value, which is what any JS reading it back will expect.
  const highContrast = options.highContrast === true;
  const reduceTransparency = options.reduceTransparency === true || highContrast;

  // Absent rather than "0" when off. An attribute that is always present with a
  // falsy value is the kind that gets matched with `[data-hc]` by accident.
  if (highContrast) root.setAttribute("data-hc", "1");
  else root.removeAttribute("data-hc");

  if (reduceTransparency) root.setAttribute("data-reduce-transparency", "1");
  else root.removeAttribute("data-reduce-transparency");
}

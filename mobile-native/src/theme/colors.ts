/**
 * The dark palette — and, through `ThemeContext`'s `DARK`, the app's default.
 *
 * The surface values here are blue graphite (`theme/blueGraphite.ts`), not the
 * near-blacks they replaced. This is the single highest-leverage edit in the
 * platform migration: ~50 screens read `colors.surface` / `colors.surfaceRaised`
 * directly, so moving two literals restyles all of them at once instead of
 * asking 96 files to each pick a shade.
 *
 * It does not leak into the other themes. `BLACK`, `LIGHT_FUTURISTIC`,
 * `LIGHT_CLASSIC` and both high-contrast palettes spread this object and then
 * override every one of the five keys below, so theme isolation here is
 * structural rather than a rule someone has to remember.
 *
 * `background` is deliberately untouched: the approved direction keeps the deep
 * page and puts graphite *on* it.
 *
 * ## Two accents had to move with the surfaces
 *
 * Lightening a surface costs contrast for every light foreground on it, and
 * `danger` and `intelligence` were the two that stopped clearing 4.5:1 (3.76 and
 * 3.56 on `raised`). No graphite light enough to stop reading as near-black
 * saves them: `intelligence` needs a surface below L=0.026, which is darker than
 * even the `inset` level. So the choice was the accents or the migration, and
 * both were lifted by the smallest hue- and saturation-preserving step that
 * clears the floor.
 *
 * Neither lift is free. Both tokens are also used as opaque fills with white
 * text on them, and white-on-danger was *already* failing at 2.92:1 before this
 * change; lifting takes it to 2.40:1. That is 13 `danger` fills and 7
 * `intelligence` fills made worse against 104 and 7 text sites made correct. The
 * fills need dark text rather than a darker fill, which is a separate change —
 * recorded here so it is not mistaken for an oversight.
 */
export const colors = {
  background: "#050910",
  /**
   * `BLUE_GRAPHITE_LEVELS.panel` and `.raised`, spelled out rather than
   * imported — the one deliberate duplication in this file.
   *
   * Importing them is the obvious move and it is wrong here. Two separate
   * gates (`native_theme_parity_gate.py`, `web_token_authority_gate.py`) read
   * this object by regex, and both lose a key the moment its value stops being
   * a string literal. One of them then reports PASS on the remaining 21 keys,
   * so the two most important colours in the platform would go unchecked while
   * the gate said everything agreed.
   *
   * `blueGraphite.test.ts` closes the duplication from the other side: it
   * allows exactly this file to restate the levels, and asserts the values
   * still match. Two copies with a test between them beat one copy that
   * disables the tooling.
   */
  surface: "#303843",
  surfaceRaised: "#363D46",
  text: "#f4f7fb",
  muted: "#9aa8b7",
  accent: "#32e6b3",
  accentStrong: "#61d8ff",
  warning: "#f3c461",
  /** Was `#ff5f7e`; +6.2% lightness, same hue. 4.57:1 on raised. */
  danger: "#ff7f97",
  /**
   * Moved because the surfaces moved, not as a style choice.
   *
   * A hairline only reads if it is lighter than the surface it divides. The old
   * `#203746` cleared the old surface by 1.50:1 and the old raised by 1.35:1;
   * against graphite it *inverts* — 1.04:1 on panel, which is invisible. Leaving
   * it would have quietly erased every divider in the app while every
   * screenshot still looked broadly correct.
   *
   * The replacement is not a new colour: it is the already-approved
   * `blueGraphite.surfaceBlueGraphiteBorder` (`rgba(118, 150, 196, 0.26)`)
   * composited over panel. That restores the original relationship almost
   * exactly — 1.45:1 on panel, 1.34:1 on raised — which is why it is stated as
   * the flat result rather than as an alpha: most consumers here paint a
   * `borderColor` on an opaque surface, where the two are identical.
   */
  border: "#425065",
  /** Was `#9f7cff`; +5.2% lightness, same hue. 4.58:1 on raised. */
  intelligence: "#b297ff",
  creator: "#42e7d4",
  economy: "#f6c85d",
  safety: "#3ff0a0",
  crypto: "#62e0ff",
  disabled: "#51606c",
  focus: "#8df7ff",
  /** Panel, at the alpha it always had. Over the page it lands on `#28303A`. */
  glass: "rgba(48, 56, 67, 0.82)",
  /** Raised, likewise. Lands on `#333A43`. */
  glassStrong: "rgba(54, 61, 70, 0.94)",
  signalDim: "rgba(50, 230, 179, 0.12)",
  signalSoft: "rgba(97, 216, 255, 0.12)",
  dangerSoft: "rgba(255, 95, 126, 0.14)",
  warningSoft: "rgba(243, 196, 97, 0.14)"
};

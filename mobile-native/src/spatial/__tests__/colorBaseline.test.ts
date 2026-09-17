import { colors } from "../../theme/colors";

/**
 * Color regression gate for the spatial console mission.
 *
 * The mission locks the production palette: spatial surfaces may only use
 * these exact tokens, and no work on this branch may alter their values.
 * If a value here changes intentionally, that is a product decision that
 * must be approved outside this branch — do not just update the snapshot.
 *
 * Four values were re-recorded for the approved blue-graphite platform surface
 * migration, which is exactly the kind of out-of-branch product decision the
 * paragraph above demands. What changed and why:
 *
 *   surface / surfaceRaised   the near-blacks became the graphite levels
 *   glass / glassStrong       the same two colours at the alphas they had
 *   border                    forced, not chosen: `#203746` was lighter than
 *                             the surfaces it divided and is darker than
 *                             graphite, so every divider would have vanished
 *   danger / intelligence     the only two accents that stopped clearing
 *                             4.5:1 on the lighter surfaces; lifted by the
 *                             smallest hue-preserving step that clears it
 *
 * `background` and the other nine accents are untouched — they were tuned
 * against near-black and still pass on graphite. That is the half of this test
 * worth having: it proves nobody moved them while they were nearby.
 */
describe("locked production palette", () => {
  it("matches the recorded baseline exactly", () => {
    expect(colors).toEqual({
      background: "#050910",
      surface: "#303843",
      surfaceRaised: "#363D46",
      text: "#f4f7fb",
      muted: "#9aa8b7",
      accent: "#32e6b3",
      accentStrong: "#61d8ff",
      warning: "#f3c461",
      danger: "#ff7f97",
      border: "#425065",
      intelligence: "#b297ff",
      creator: "#42e7d4",
      economy: "#f6c85d",
      safety: "#3ff0a0",
      crypto: "#62e0ff",
      disabled: "#51606c",
      focus: "#8df7ff",
      glass: "rgba(48, 56, 67, 0.82)",
      glassStrong: "rgba(54, 61, 70, 0.94)",
      signalDim: "rgba(50, 230, 179, 0.12)",
      signalSoft: "rgba(97, 216, 255, 0.12)",
      dangerSoft: "rgba(255, 95, 126, 0.14)",
      warningSoft: "rgba(243, 196, 97, 0.14)"
    });
  });
});

import { colors } from "../../theme/colors";

/**
 * Color regression gate for the spatial console mission.
 *
 * The mission locks the production palette: spatial surfaces may only use
 * these exact tokens, and no work on this branch may alter their values.
 * If a value here changes intentionally, that is a product decision that
 * must be approved outside this branch — do not just update the snapshot.
 */
describe("locked production palette", () => {
  it("matches the recorded baseline exactly", () => {
    expect(colors).toEqual({
      background: "#050910",
      surface: "#0b141c",
      surfaceRaised: "#111f2a",
      text: "#f4f7fb",
      muted: "#9aa8b7",
      accent: "#32e6b3",
      accentStrong: "#61d8ff",
      warning: "#f3c461",
      danger: "#ff5f7e",
      border: "#203746",
      intelligence: "#9f7cff",
      creator: "#42e7d4",
      economy: "#f6c85d",
      safety: "#3ff0a0",
      crypto: "#62e0ff",
      disabled: "#51606c",
      focus: "#8df7ff",
      glass: "rgba(11, 24, 34, 0.82)",
      glassStrong: "rgba(15, 36, 50, 0.94)",
      signalDim: "rgba(50, 230, 179, 0.12)",
      signalSoft: "rgba(97, 216, 255, 0.12)",
      dangerSoft: "rgba(255, 95, 126, 0.14)",
      warningSoft: "rgba(243, 196, 97, 0.14)"
    });
  });
});

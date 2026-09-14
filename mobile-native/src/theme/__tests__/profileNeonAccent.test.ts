/**
 * The profile palette went dead in production once and no test noticed, because
 * every fixture in the suite builds its own theme object. `/api/pulse/profile`
 * substitutes a whole default theme when the user has no theme row, so
 * `accent_color` is never absent and never null — it is the app-wide teal. The
 * cases below are the payloads the real backend actually sends.
 */

import { profileNeon, resolveProfileAccent, usesNeonRamp } from "../profileNeon";

const SERVER_DEFAULT_THEME_ACCENT = "#32e6b3";

describe("resolveProfileAccent", () => {
  it("falls back to the neon blue for the theme the server invents", () => {
    expect(resolveProfileAccent(SERVER_DEFAULT_THEME_ACCENT)).toBe(profileNeon.electric);
  });

  it("falls back for a missing, empty or null accent", () => {
    expect(resolveProfileAccent(undefined)).toBe(profileNeon.electric);
    expect(resolveProfileAccent(null)).toBe(profileNeon.electric);
    expect(resolveProfileAccent("   ")).toBe(profileNeon.electric);
  });

  it("keeps an accent the owner actually chose", () => {
    expect(resolveProfileAccent("#ff8a00")).toBe("#ff8a00");
  });

  it("matches the server default case-insensitively", () => {
    expect(resolveProfileAccent("#32E6B3")).toBe(profileNeon.electric);
  });

  it("only lets the un-themed profile use the multi-stop ramps", () => {
    expect(usesNeonRamp(resolveProfileAccent(SERVER_DEFAULT_THEME_ACCENT))).toBe(true);
    expect(usesNeonRamp(resolveProfileAccent("#ff8a00"))).toBe(false);
  });
});

describe("profile ramps", () => {
  // Each ramp is read straight into LinearGradient, which needs at least two
  // stops and renders nothing useful with one.
  it("every ramp has enough stops to interpolate", () => {
    for (const ramp of [profileNeon.primaryAction, profileNeon.identityRing, profileNeon.horizon, profileNeon.tileCycle]) {
      expect(ramp.length).toBeGreaterThanOrEqual(2);
    }
  });

  it("leads with blue and tails warm, so the surface reads as one palette", () => {
    expect(profileNeon.primaryAction[0]).toMatch(/^#[0-9a-f]{6}$/i);
    expect(profileNeon.primaryAction[profileNeon.primaryAction.length - 1]).toBe(profileNeon.magenta);
    expect(profileNeon.identityRing[profileNeon.identityRing.length - 1]).toBe(profileNeon.magenta);
  });
});

import React, { ReactNode } from "react";
import { render } from "@testing-library/react-native";
import { AccessibilityInfo, Animated, AppState, View } from "react-native";

const mockRemoveBattery = jest.fn();
jest.mock("expo-battery", () => ({
  isLowPowerModeEnabledAsync: jest.fn(async () => false),
  addLowPowerModeListener: jest.fn(() => ({ remove: mockRemoveBattery }))
}));

/**
 * Rendered as a real View rather than swallowed, because the gradient's
 * `colors` and `testID` are exactly what several of these tests assert on.
 */
jest.mock("expo-linear-gradient", () => {
  const ReactModule = require("react");
  const { View } = require("react-native");
  return {
    LinearGradient: (props: Record<string, unknown>) => ReactModule.createElement(View, props, props.children)
  };
});

import { PulseBackground } from "../PulseBackground";
import { ThemeProvider, buildTheme, __testing } from "../../theme/ThemeContext";
import type { GalacticBackgroundProfile } from "../../theme/ThemeContext";
import {
  PULSE_BACKGROUND_CEILINGS,
  PULSE_BACKGROUND_LINES,
  PULSE_BACKGROUND_NODES,
  PULSE_BACKGROUND_SURFACES,
  PULSE_BACKGROUND_VARIANTS,
  PULSE_BACKGROUND_VARIANT_CYCLES,
  bottomGlowOpacity
} from "../../theme/pulseBackground";
import type { ThemeMode } from "../../settings/schema";

function withTheme(theme: ThemeMode, children: ReactNode, reduceMotion = false) {
  return (
    <ThemeProvider
      appearance={{ theme, fontScale: 1, reduceTransparency: false, compactDensity: false }}
      accessibility={{
        reduceMotion,
        boldText: false,
        highContrast: false,
        captionsEnabled: true,
        hapticFeedback: true,
        screenReaderHints: true
      }}
    >
      {children}
    </ThemeProvider>
  );
}

/**
 * Render against a chosen atmosphere profile rather than a chosen theme.
 *
 * `buildTheme` pins the active appearance to dark for this release (see the
 * comment at its top), so `withTheme("white", …)` no longer produces a white
 * theme — it produces the dark one, and any assertion about the white or light
 * surface written through `ThemeProvider` is asserting against dark. The
 * profiles themselves are still implemented and `galacticProfileFor` still maps
 * every mode, so what is testable is the pair: the mapping is a pure function,
 * and `PulseBackground`'s branch on the resulting profile is a component
 * concern. Splitting them keeps both covered while the pin stands.
 */
function withProfile(profile: GalacticBackgroundProfile, children: ReactNode, reduceMotion = false) {
  const theme = {
    ...buildTheme(
      { theme: "dark", fontScale: 1, reduceTransparency: false, compactDensity: false },
      {
        reduceMotion,
        boldText: false,
        highContrast: false,
        captionsEnabled: true,
        hapticFeedback: true,
        screenReaderHints: true
      },
      "dark"
    ),
    galacticBackground: profile
  };
  return <__testing.ThemeContext.Provider value={theme}>{children}</__testing.ThemeContext.Provider>;
}

function flatten(style: unknown): Record<string, unknown> {
  if (Array.isArray(style)) return Object.assign({}, ...style.filter(Boolean).map(flatten));
  return (style as Record<string, unknown>) ?? {};
}

/**
 * The whole backdrop is deliberately hidden from accessibility, and the query
 * helpers skip hidden subtrees by default — so asking for them has to be
 * explicit. That the flag is needed at all is itself the evidence the layers
 * are hidden the way they should be.
 */
type Screen = ReturnType<typeof render>;
const HIDDEN = { includeHiddenElements: true } as const;
const one = (screen: Screen, id: string) => screen.getByTestId(id, HIDDEN);
const all = (screen: Screen, id: RegExp) => screen.getAllByTestId(id, HIDDEN);

describe("PulseBackground", () => {
  beforeEach(() => {
    // Motion is gated on the app being foregrounded, which jest does not
    // simulate; without this every animated case would test the still one.
    (AppState as unknown as { currentState: string }).currentState = "active";
  });
  afterEach(() => jest.restoreAllMocks());

  it("maps each theme to the atmosphere that theme promises", () => {
    // White's promise is a plain page, so its profile is disabled. This is the
    // mapping only; whether the app can currently reach it is the next test.
    expect(__testing.galacticProfileFor("white", "light").enabled).toBe(false);
    expect(__testing.galacticProfileFor("light_futuristic", "light").variant).toBe("light");
    expect(__testing.galacticProfileFor("dark", "dark")).toEqual({ enabled: true, intensity: 1, variant: "dark" });
  });

  it("ships the dark atmosphere regardless of the requested theme", () => {
    // `buildTheme` pins the active appearance to dark for this release. Asking
    // for white must therefore still yield the dark backdrop — and when that pin
    // is lifted, this test is what fails and points at the two below, which are
    // the real per-profile coverage.
    const screen = render(withTheme("white", <PulseBackground />));
    expect(screen.queryByTestId("pulse-background-field", HIDDEN)).not.toBeNull();
    expect(__testing.galacticProfileFor("white", "light").enabled).toBe(false);
  });

  it("renders nothing when the profile turns the backdrop off", () => {
    const screen = render(withProfile({ enabled: false, intensity: 0, variant: "light" }, <PulseBackground />));
    expect(screen.toJSON()).toBeNull();
    expect(screen.queryByTestId("pulse-background")).toBeNull();
  });

  it("draws a still composition and starts no loop under reduce motion", () => {
    const loop = jest.spyOn(Animated, "loop");
    const screen = render(withTheme("dark", <PulseBackground />, true));
    expect(loop).not.toHaveBeenCalled();
    // Still drawn — every node and line of the default composition is present.
    expect(all(screen, /^pulse-background-node-/)).toHaveLength(PULSE_BACKGROUND_NODES.length);
    for (const node of all(screen, /^pulse-background-node-/)) {
      expect(typeof flatten(node.props.style).opacity).toBe("number");
    }
  });

  it("runs one loop per driver when motion is allowed", () => {
    const loop = jest.spyOn(Animated, "loop");
    render(withTheme("dark", <PulseBackground />));
    expect(loop).toHaveBeenCalledTimes(3);
  });

  it("keeps every decorative layer non-interactive and out of the reading order", () => {
    const screen = render(withTheme("dark", <PulseBackground />));
    const field = one(screen, "pulse-background-field");
    expect(field.props.pointerEvents).toBe("none");
    expect(field.props.accessibilityElementsHidden).toBe(true);
    expect(field.props.importantForAccessibility).toBe("no-hide-descendants");
    for (const id of ["pulse-background-gradient", "pulse-background-lines", "pulse-background-nodes", "pulse-background-glow"]) {
      expect(one(screen, id).props.pointerEvents).toBe("none");
    }
    for (const decoration of [
      ...all(screen, /^pulse-background-node-/),
      ...all(screen, /^pulse-background-halo-/),
      ...all(screen, /^pulse-background-line-/)
    ]) {
      expect(decoration.props.pointerEvents).toBe("none");
    }
  });

  it("never blocks touches on the content it wraps", () => {
    const screen = render(
      withTheme(
        "dark",
        <PulseBackground testID="wrapped">
          <View testID="wrapped-child" />
        </PulseBackground>
      )
    );
    // `none` on the root would block descendants too, so a wrapper takes
    // `box-none` and must not hide its children from a screen reader.
    const root = one(screen, "wrapped");
    expect(root.props.pointerEvents).toBe("box-none");
    expect(root.props.accessibilityElementsHidden).toBe(false);
    expect(one(screen, "wrapped-content").props.pointerEvents).toBe("box-none");
  });

  it("holds every node, halo and line under the opacity ceilings", () => {
    const screen = render(withTheme("dark", <PulseBackground variant="elevated" />, true));
    for (const node of all(screen, /^pulse-background-node-/)) {
      expect(flatten(node.props.style).opacity).toBeLessThanOrEqual(PULSE_BACKGROUND_CEILINGS.node);
    }
    for (const halo of all(screen, /^pulse-background-halo-/)) {
      expect(flatten(halo.props.style).opacity).toBeLessThanOrEqual(
        PULSE_BACKGROUND_CEILINGS.node * PULSE_BACKGROUND_CEILINGS.halo
      );
    }
    for (const line of all(screen, /^pulse-background-line-/)) {
      expect(flatten(line.props.style).opacity).toBeLessThanOrEqual(PULSE_BACKGROUND_CEILINGS.line);
    }
    // The tables themselves are within budget, so no future variant can be the
    // one thing standing between an authored value and the ceiling.
    for (const node of PULSE_BACKGROUND_NODES) expect(node.opacity).toBeLessThanOrEqual(PULSE_BACKGROUND_CEILINGS.node);
    for (const line of PULSE_BACKGROUND_LINES) expect(line.opacity).toBeLessThanOrEqual(PULSE_BACKGROUND_CEILINGS.line);
  });

  it("keeps the bottom lift a lift rather than a fog", () => {
    for (const variant of ["default", "quiet", "elevated", "static"] as const) {
      const alphas = PULSE_BACKGROUND_SURFACES.dark.bottomGlow.colors
        .map((color) => Number(color.match(/rgba\([^)]*,\s*([\d.]+)\)/)?.[1] ?? 1))
        .filter((alpha) => alpha < 1);
      // The near-black closing stop is allowed to be denser; only the purple
      // lift is capped, and it is the one stop that can read as fog.
      const purple = PULSE_BACKGROUND_SURFACES.dark.bottomGlow.colors
        .map((color, index) => ({ color, alpha: alphas[index] ?? 0 }))
        .filter((stop) => stop.color.startsWith("rgba(124,77,255"));
      for (const stop of purple) {
        expect(stop.alpha * bottomGlowOpacity(variant)).toBeLessThanOrEqual(PULSE_BACKGROUND_CEILINGS.bottomGlow);
      }
    }
  });

  it("differs between variants exactly as the tokens say", () => {
    const counts = (variant: "default" | "quiet" | "elevated" | "static") => {
      const screen = render(withTheme("dark", <PulseBackground variant={variant} />, true));
      return {
        nodes: all(screen, /^pulse-background-node-/).length,
        lines: all(screen, /^pulse-background-line-/).length,
        glow: flatten(one(screen, "pulse-background-glow").props.style).opacity
      };
    };
    const core = PULSE_BACKGROUND_NODES.filter((node) => node.tier === "core").length;
    const coreLines = PULSE_BACKGROUND_LINES.filter((line) => line.tier === "core").length;

    expect(counts("default").nodes).toBe(PULSE_BACKGROUND_NODES.length);
    expect(counts("quiet").nodes).toBe(core);
    expect(counts("quiet").lines).toBe(coreLines);
    expect(counts("elevated").nodes).toBe(PULSE_BACKGROUND_NODES.length);
    expect(counts("elevated").glow).toBeGreaterThan(counts("quiet").glow as number);

    // `static` is the default composition with the motion taken out.
    const loop = jest.spyOn(Animated, "loop");
    render(withTheme("dark", <PulseBackground variant="static" />));
    expect(loop).not.toHaveBeenCalled();
    expect(PULSE_BACKGROUND_VARIANTS.static.animated).toBe(false);
  });

  it("keeps every cycle inside the approved motion budget", () => {
    for (const cycles of Object.values(PULSE_BACKGROUND_VARIANT_CYCLES)) {
      expect(cycles.drift).toBeGreaterThanOrEqual(18000);
      expect(cycles.drift).toBeLessThanOrEqual(35000);
      expect(cycles.pulse).toBeGreaterThanOrEqual(5000);
      expect(cycles.pulse).toBeLessThanOrEqual(10000);
      expect(cycles.travel).toBeGreaterThanOrEqual(25000);
      expect(cycles.travel).toBeLessThanOrEqual(45000);
    }
  });

  it("uses the light surface under a light profile instead of a dimmed dark one", () => {
    const light = __testing.galacticProfileFor("light_futuristic", "light");
    const screen = render(withProfile(light, <PulseBackground />, true));
    expect(one(screen, "pulse-background-gradient").props.colors).toEqual(
      PULSE_BACKGROUND_SURFACES.light.gradient.colors
    );
    // Dimmer than the dark surface, so it can never fight body text.
    const nodes = all(screen, /^pulse-background-node-/);
    const dark = all(render(withTheme("dark", <PulseBackground />, true)), /^pulse-background-node-/);
    expect(flatten(nodes[0].props.style).opacity).toBeLessThan(flatten(dark[0].props.style).opacity as number);
  });

  it("removes every listener it registered on unmount", () => {
    const removeMotion = jest.fn();
    const removeApp = jest.fn();
    jest
      .spyOn(AccessibilityInfo, "addEventListener")
      .mockReturnValue({ remove: removeMotion } as unknown as ReturnType<typeof AccessibilityInfo.addEventListener>);
    jest
      .spyOn(AppState, "addEventListener")
      .mockReturnValue({ remove: removeApp } as unknown as ReturnType<typeof AppState.addEventListener>);
    const screen = render(withTheme("dark", <PulseBackground />));
    screen.unmount();
    expect(removeMotion).toHaveBeenCalled();
    expect(removeApp).toHaveBeenCalled();
    expect(mockRemoveBattery).toHaveBeenCalled();
  });
});

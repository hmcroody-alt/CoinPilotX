import fs from "fs";
import path from "path";
import React, { ReactNode } from "react";
import { render } from "@testing-library/react-native";
import { AppState } from "react-native";

jest.mock("expo-battery", () => ({
  isLowPowerModeEnabledAsync: jest.fn(async () => false),
  addLowPowerModeListener: jest.fn(() => ({ remove: jest.fn() }))
}));

/**
 * A real `View` rather than the children-only stub the sibling
 * `GalacticAtmosphere.test.tsx` uses: this file exists to assert on the
 * gradient's `colors`, `locations`, `start` and `end`, and a stub that swallows
 * its props can only prove the component rendered, never *what* it painted.
 */
jest.mock("expo-linear-gradient", () => {
  const ReactModule = require("react");
  const { View } = require("react-native");
  return {
    LinearGradient: (props: Record<string, unknown>) => ReactModule.createElement(View, props, props.children)
  };
});

import { GalacticAtmosphere } from "../GalacticAtmosphere";
import { BLUE_GRAPHITE_CARD } from "../../theme/blueGraphite";
import { buildTheme, __testing } from "../../theme/ThemeContext";
import type { GalacticBackgroundProfile, Theme } from "../../theme/ThemeContext";
import type { ThemeMode } from "../../settings/schema";

/** The near-blacks this material replaces, pinned so the default path cannot drift. */
const SPACE_COLORS = ["#02050A", "#040A14", "#06101C"];
const SPACE_EDGE_COLORS = ["rgba(4,10,18,0.02)", "rgba(4,10,18,0.13)"];
const LIGHT_COLORS = ["#eef4fb", "#e9f1fa", "#e3edf9"];

/**
 * Build a theme directly rather than through `ThemeProvider`.
 *
 * `buildTheme` pins the active appearance to dark for this release
 * (`ThemeContext.tsx`, `const activeTheme: ThemeMode = "dark"`), so asking the
 * provider for black or white yields the dark theme and every "other themes are
 * unchanged" assertion written that way would silently be asserting against
 * dark. Overriding `mode`, `highContrast` and `galacticBackground` on the built
 * object is the only way to exercise the branches the component actually has —
 * and it is what those branches will receive when the pin is lifted.
 */
function themeWith(overrides: { mode?: ThemeMode; highContrast?: boolean; galactic?: GalacticBackgroundProfile }): Theme {
  const base = buildTheme(
    { theme: "dark", fontScale: 1, reduceTransparency: false, compactDensity: false },
    {
      reduceMotion: true,
      boldText: false,
      highContrast: overrides.highContrast ?? false,
      captionsEnabled: true,
      hapticFeedback: true,
      screenReaderHints: true
    },
    "dark"
  );
  return {
    ...base,
    mode: overrides.mode ?? base.mode,
    highContrast: overrides.highContrast ?? base.highContrast,
    galacticBackground: overrides.galactic ?? base.galacticBackground
  };
}

function withTheme(theme: Theme, children: ReactNode) {
  return <__testing.ThemeContext.Provider value={theme}>{children}</__testing.ThemeContext.Provider>;
}

type Screen = ReturnType<typeof render>;

/**
 * The two gradients in document order: the opaque material first, the perimeter
 * deepening last. Ordering is the component's own contract — the base has to
 * paint under the stars and the edge over them — so reading them positionally
 * also asserts that the stack was not inverted.
 */
function gradients(screen: Screen) {
  // Host nodes only. The mocked `LinearGradient` is a function component that
  // renders RN's `View`, which is itself a forwardRef wrapper over the host
  // view — so every gradient appears three times in the tree with identical
  // props, and an unfiltered walk reports three layers where there are two.
  const found = screen.UNSAFE_root.findAll(
    (node: { type: unknown; props: { colors?: unknown } }) =>
      typeof node.type === "string" && Array.isArray(node.props.colors)
  );
  return found.map((node) => node.props as { colors: string[]; locations: number[]; start?: unknown; end?: unknown });
}

const render1 = (theme: Theme, surface?: "space" | "blueGraphite") =>
  gradients(render(withTheme(theme, <GalacticAtmosphere variant="feed" surface={surface} />)));

describe("GalacticAtmosphere — the blue-graphite card surface", () => {
  beforeEach(() => {
    (AppState as unknown as { currentState: string }).currentState = "active";
  });

  describe("the card gets the approved material", () => {
    it("paints the blue-graphite ramp, axis and all, when the surface asks for it", () => {
      const [base, edge] = render1(themeWith({}), "blueGraphite");
      expect(base.colors).toEqual([...BLUE_GRAPHITE_CARD.base.colors]);
      expect(base.locations).toEqual([...BLUE_GRAPHITE_CARD.base.locations]);
      // The axis is half the material: the same four colours on a vertical axis
      // put navy across the whole bottom instead of in the lower-right corner.
      expect(base.start).toEqual(BLUE_GRAPHITE_CARD.base.start);
      expect(base.end).toEqual(BLUE_GRAPHITE_CARD.base.end);
      expect(edge.colors).toEqual([...BLUE_GRAPHITE_CARD.edge.colors]);
      expect(edge.locations).toEqual([...BLUE_GRAPHITE_CARD.edge.locations]);
    });

    it("paints an opaque material, not a wash over something darker", () => {
      const [base] = render1(themeWith({}), "blueGraphite");
      // Every stop a bare hex: no `rgba(...)`, no eight-digit alpha. A single
      // translucent stop would let the near-black page show through and put
      // back exactly the appearance this material exists to remove.
      for (const stop of base.colors) expect(stop).toMatch(/^#[0-9a-fA-F]{6}$/);
      expect(base.colors).not.toContain("#000000");
    });

    it("replaces the near-black rather than sitting on top of it", () => {
      const [base] = render1(themeWith({}), "blueGraphite");
      for (const near of SPACE_COLORS) expect(base.colors).not.toContain(near);
    });
  });

  describe("every other surface and theme is left exactly as it was", () => {
    it("defaults to the space field, so existing callers are unchanged by construction", () => {
      const [base, edge] = render1(themeWith({}));
      expect(base.colors).toEqual(SPACE_COLORS);
      expect(edge.colors).toEqual(SPACE_EDGE_COLORS);
      // No axis on the space field — it never had one, and adding one would
      // re-aim a gradient Reels renders full-screen behind video.
      expect(base.start).toBeUndefined();
      expect(base.end).toBeUndefined();
    });

    it("is byte-identical with the prop omitted and with it set to space", () => {
      expect(render1(themeWith({}), "space")).toEqual(render1(themeWith({})));
    });

    it("stands down under high contrast, which substitutes the palette wholesale", () => {
      // A surface tuned against the normal ramp has no standing to override a
      // mode whose entire purpose is to replace that ramp.
      const [base] = render1(themeWith({ highContrast: true }), "blueGraphite");
      expect(base.colors).toEqual(SPACE_COLORS);
    });

    it("stands down outside the released dark appearance", () => {
      for (const mode of ["black", "white", "light_futuristic"] as ThemeMode[]) {
        const [base] = render1(themeWith({ mode }), "blueGraphite");
        expect(base.colors).toEqual(SPACE_COLORS);
      }
    });

    it("keeps the light themes on their haze instead of darkening them", () => {
      const light = __testing.galacticProfileFor("light_futuristic", "light");
      const [base] = render1(themeWith({ mode: "light_futuristic", galactic: light }), "blueGraphite");
      expect(base.colors).toEqual(LIGHT_COLORS);
    });

    it("renders nothing at all when the profile turns the atmosphere off", () => {
      const white = __testing.galacticProfileFor("white", "light");
      expect(white.enabled).toBe(false);
      const screen = render(
        withTheme(themeWith({ mode: "white", galactic: white }), <GalacticAtmosphere variant="feed" surface="blueGraphite" />)
      );
      expect(screen.toJSON()).toBeNull();
    });

    it("dims the black theme by profile intensity rather than by material", () => {
      const black = __testing.galacticProfileFor("black", "dark");
      const screen = render(
        withTheme(themeWith({ mode: "black", galactic: black }), <GalacticAtmosphere variant="feed" surface="blueGraphite" />)
      );
      const root = screen.toJSON();
      const style = root && !Array.isArray(root) ? (root.props.style as Record<string, unknown>[]) : [];
      expect(Object.assign({}, ...style.filter(Boolean)).opacity).toBe(black.intensity);
      expect(gradients(screen)[0].colors).toEqual(SPACE_COLORS);
    });
  });

  describe("nothing about the layer itself changes", () => {
    it("adds no decorative element and removes none", () => {
      const count = (surface?: "space" | "blueGraphite") => {
        const screen = render(withTheme(themeWith({}), <GalacticAtmosphere variant="feed" surface={surface} />));
        return {
          stars: screen.UNSAFE_root.findAll((node: { props: { style?: unknown } }) => {
            const style = Array.isArray(node.props.style)
              ? Object.assign({}, ...node.props.style.filter(Boolean))
              : node.props.style;
            return (style as { backgroundColor?: string })?.backgroundColor === "#CDEBFA";
          }).length,
          gradients: gradients(screen).length
        };
      };
      expect(count("blueGraphite")).toEqual(count());
      expect(count("blueGraphite").gradients).toBe(2);
    });

    it("stays non-interactive and out of the reading order on the new surface", () => {
      const screen = render(
        withTheme(themeWith({}), <GalacticAtmosphere variant="feed" surface="blueGraphite" testID="card-material" />)
      );
      const root = screen.toJSON();
      const props = root && !Array.isArray(root) ? root.props : {};
      expect(props.pointerEvents).toBe("none");
      expect(props.accessibilityElementsHidden).toBe(true);
      expect(props.importantForAccessibility).toBe("no-hide-descendants");
    });
  });

  describe("the opt-in stays opted-in", () => {
    const read = (file: string) =>
      fs.readFileSync(path.join(__dirname, "..", "..", "screens", file), "utf8");

    it("is asked for by the Pulse Network card", () => {
      expect(read("HomeScreen.tsx")).toContain('surface="blueGraphite"');
    });

    it("is not asked for by Reels, which is a media surface and stays dark", () => {
      // Reels renders this same component full-screen behind video. A
      // component-wide change would have taken it along; an opt-in does not,
      // and this is the assertion that notices if someone flips the default.
      const reels = read("ReelsScreen.tsx");
      expect(reels).toContain("<GalacticAtmosphere variant=\"feed\" testID=\"reels-galactic-atmosphere\" />");
      expect(reels).not.toContain("blueGraphite");
    });
  });
});

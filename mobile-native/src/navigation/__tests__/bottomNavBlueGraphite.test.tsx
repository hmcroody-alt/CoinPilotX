/**
 * The dock's blue-graphite material, and everything about the dock that must
 * not have moved to get it there.
 *
 * The material is a *sibling layer*, not a fill: a gradient cannot be a
 * `backgroundColor`, and the obvious way to make one respect the panel's 38pt
 * radius — `overflow: "hidden"` on the panel — would clip the Create circle,
 * which deliberately overhangs. So the risk this file is written against is not
 * "is the colour right" (the token suite answers that) but "did adding a layer
 * to this container change the container". Half the assertions below are
 * therefore about geometry, hit targets and routing staying exactly as they
 * were, with the layer present.
 */
import React, { ReactNode } from "react";
import { StyleSheet } from "react-native";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 34, left: 0, right: 0 })
}));
jest.mock("expo-haptics", () => ({
  selectionAsync: jest.fn(async () => undefined),
  impactAsync: jest.fn(async () => undefined),
  ImpactFeedbackStyle: { Light: "light" }
}));

/**
 * The dock owns the Pulse Radio mini player, so importing it reaches
 * `core/pulseRadio` and through it `expo-av` — a native module absent under
 * Jest, which fails the suite at require time before a single assertion runs.
 *
 * Stubbed to the module's shape and nothing more, exactly as
 * `globalHeaderTitleAndSubtitle.test.tsx` does: this file asserts on a
 * background layer, and a stub that grew behaviour would start standing in for
 * audio that belongs to the audio suite. Pulse Radio playback is a protected
 * system and nothing here touches it.
 */
jest.mock("../../core/pulseRadio", () => ({
  getPulseRadioState: () => ({
    status: "offline",
    track: null,
    message: "",
    userWantsPlayback: false,
    interruptedBy: null,
    queue: [],
    queueIndex: -1,
    shuffle: false,
    repeatMode: "off",
    positionMillis: 0,
    durationMillis: 0
  }),
  subscribePulseRadio: () => () => undefined,
  togglePulseRadio: () => Promise.resolve(),
  playNextTrack: () => Promise.resolve(),
  playPreviousTrack: () => Promise.resolve()
}));

/** A real `View`, so `colors`/`locations`/`start`/`end` survive to be asserted on. */
jest.mock("expo-linear-gradient", () => {
  const ReactModule = require("react");
  const { View } = require("react-native");
  return {
    LinearGradient: (props: Record<string, unknown>) => ReactModule.createElement(View, props, props.children)
  };
});

import type { BottomTabBarProps } from "@react-navigation/bottom-tabs";
import { LogiNexusBottomNavigation } from "../GlobalNavigation";
import { BLUE_GRAPHITE_NAV } from "../../theme/blueGraphite";
import {
  BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT,
  BOTTOM_NAV_DOCK_PANEL_PADDING
} from "../bottomNavMetrics";
import { buildTheme, __testing } from "../../theme/ThemeContext";
import type { Theme } from "../../theme/ThemeContext";
import type { ThemeMode } from "../../settings/schema";

/** The near-black the dock used to be. Pinned so the off-gate path cannot drift. */
const LEGACY_PANEL_FILL = "rgba(7, 14, 32, 0.95)";

const TABS = ["Home", "Reels", "Messenger", "Profile"] as const;

function tabBarProps(activeIndex = 0) {
  const routes = TABS.map((name) => ({ key: `${name}-key`, name, params: undefined }));
  const navigate = jest.fn();
  const emit = jest.fn(() => ({ defaultPrevented: false }));
  return {
    navigate,
    emit,
    props: {
      state: { index: activeIndex, routes, routeNames: [...TABS], key: "tabs", type: "tab", stale: false, history: [] },
      descriptors: Object.fromEntries(routes.map((route) => [route.key, { options: {} }])),
      navigation: { navigate, emit, addListener: jest.fn(() => jest.fn()) },
      insets: { top: 0, bottom: 34, left: 0, right: 0 }
    } as unknown as BottomTabBarProps
  };
}

function themeWith(overrides: { mode?: ThemeMode; highContrast?: boolean }): Theme {
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
  return { ...base, mode: overrides.mode ?? base.mode, highContrast: overrides.highContrast ?? base.highContrast };
}

const withTheme = (theme: Theme, children: ReactNode) => (
  <__testing.ThemeContext.Provider value={theme}>{children}</__testing.ThemeContext.Provider>
);

function mount(theme: Theme, activeIndex = 0) {
  const { props, navigate, emit } = tabBarProps(activeIndex);
  const screen = render(withTheme(theme, <LogiNexusBottomNavigation {...props} />));
  return { screen, navigate, emit };
}

const flatten = (style: unknown): Record<string, unknown> =>
  Array.isArray(style) ? Object.assign({}, ...style.filter(Boolean).map(flatten)) : ((style as Record<string, unknown>) ?? {});

type JsonNode = { props: Record<string, unknown>; children: (JsonNode | string)[] | null };

/**
 * The panel's direct children, in paint order, read off the rendered host tree.
 *
 * Not via `element.parent`: RNTL's `parent` is the nearest *composite* ancestor,
 * which for the material is the `View` forwardRef wrapping the material itself
 * — so walking up from it lands one node short of the panel every time. The
 * host JSON has no composites in it at all, so ordering there is paint order.
 */
function panelChildTestIds(screen: ReturnType<typeof render>): string[] {
  const root = screen.toJSON() as JsonNode | null;
  const find = (node: JsonNode | string | null): JsonNode | null => {
    if (!node || typeof node === "string") return null;
    if (flatten(node.props.style).borderRadius === 38) return node;
    for (const child of node.children ?? []) {
      const found = find(child);
      if (found) return found;
    }
    return null;
  };
  const panel = find(root);
  return (panel?.children ?? [])
    .map((child) => (typeof child === "string" ? undefined : (child.props.testID as string | undefined)))
    .filter((id): id is string => Boolean(id));
}

function panelStyle(screen: ReturnType<typeof render>) {
  const material = screen.queryByTestId("global-bottom-navigation-material", { includeHiddenElements: true });
  const home = screen.getByTestId("global-bottom-home", { includeHiddenElements: true });
  // The panel is whichever ancestor owns the 38pt radius — found by walking up
  // from a tab rather than by index, so re-ordering children cannot silently
  // re-point this helper at a different node.
  let node = (material ?? home).parent;
  while (node && flatten(node.props.style).borderRadius !== 38) node = node.parent;
  return flatten(node?.props.style);
}

describe("bottom navigation — the blue-graphite dock", () => {
  describe("the dock wears the darker member of the family", () => {
    it("paints the nav ramp, axis and all", () => {
      const { screen } = mount(themeWith({}));
      const material = screen.getByTestId("global-bottom-navigation-material", { includeHiddenElements: true });
      expect(material.props.colors).toEqual([...BLUE_GRAPHITE_NAV.base.colors]);
      expect(material.props.locations).toEqual([...BLUE_GRAPHITE_NAV.base.locations]);
      expect(material.props.start).toEqual(BLUE_GRAPHITE_NAV.base.start);
      expect(material.props.end).toEqual(BLUE_GRAPHITE_NAV.base.end);
    });

    it("backs the gradient with an opaque fill from the same family, never black", () => {
      // The gradient is the material; the fill is what guarantees the panel is
      // never translucent — not even for the frame before the gradient paints.
      const style = panelStyle(mount(themeWith({})).screen);
      expect(style.backgroundColor).toBe(BLUE_GRAPHITE_NAV.fallback);
      expect(style.backgroundColor).not.toBe(LEGACY_PANEL_FILL);
      expect(style.backgroundColor).toMatch(/^#[0-9a-fA-F]{6}$/);
    });

    it("clips itself instead of asking the panel to clip", () => {
      const { screen } = mount(themeWith({}));
      const style = flatten(
        screen.getByTestId("global-bottom-navigation-material", { includeHiddenElements: true }).props.style
      );
      // One point inside the panel's 38, which is the border width — RN lays
      // absolute children out against the padding box, so this lands flush.
      expect(style.borderRadius).toBe(37);
      expect(style).toMatchObject(StyleSheet.absoluteFillObject);
      // `overflow: "hidden"` on the panel would have clipped the Create circle,
      // which overhangs by `BOTTOM_NAV_CREATE_MARGIN_TOP`. It must stay off.
      expect(panelStyle(screen).overflow).toBeUndefined();
    });

    it("stays out of the hit path entirely", () => {
      const { screen } = mount(themeWith({}));
      expect(
        screen.getByTestId("global-bottom-navigation-material", { includeHiddenElements: true }).props.pointerEvents
      ).toBe("none");
    });

    it("paints under the tabs, by document order rather than by zIndex", () => {
      const { screen } = mount(themeWith({}));
      const material = screen.getByTestId("global-bottom-navigation-material", { includeHiddenElements: true });
      const order = panelChildTestIds(screen);
      // First child of the panel, so it paints beneath every tab with no
      // stacking context of its own. A `zIndex` here would be the tell that the
      // order is wrong and is being compensated for.
      expect(order[0]).toBe("global-bottom-navigation-material");
      expect(flatten(material.props.style).zIndex).toBeUndefined();
      // ...and the tabs really are its siblings, painted after it, not its
      // children — which would put the material above them instead.
      expect(order).toContain("global-bottom-home");
      expect(order.indexOf("global-bottom-home")).toBeGreaterThan(0);
    });
  });

  /**
   * The perimeter deepening.
   *
   * This layer shipped late: the token existed and was unit-tested from the
   * start, but nothing rendered it, so the dock deepened only along the base
   * ramp's axis and its top and bottom sat flat at core graphite while the
   * card's did not. The tests below are the ones that would have caught that,
   * so they assert the layer is *rendered* and where it sits — not just that the
   * token is well-formed, which `blueGraphite.test.ts` already covers.
   */
  describe("the dock deepens at its top and bottom, not only along the base axis", () => {
    const edgeOf = (screen: ReturnType<typeof render>) =>
      screen.getByTestId("global-bottom-navigation-material-edge", { includeHiddenElements: true });

    it("renders the nav edge ramp", () => {
      const edge = edgeOf(mount(themeWith({})).screen);
      expect(edge.props.colors).toEqual([...BLUE_GRAPHITE_NAV.edge.colors]);
      expect(edge.props.locations).toEqual([...BLUE_GRAPHITE_NAV.edge.locations]);
    });

    it("takes no axis, so it runs top-to-bottom across the base's axis", () => {
      // The base ramp is aimed corner to corner on purpose. This layer must not
      // be: giving it an axis would align it with the base and leave the dock's
      // top and bottom exactly as flat as they were before it existed.
      const edge = edgeOf(mount(themeWith({})).screen);
      expect(edge.props.start).toBeUndefined();
      expect(edge.props.end).toBeUndefined();
    });

    it("only darkens — every stop translucent, so the opaque base still shows", () => {
      const edge = edgeOf(mount(themeWith({})).screen);
      for (const stop of edge.props.colors as string[]) {
        expect(stop).toMatch(/^rgba\(/);
        const alpha = Number(stop.replace(/^.*,\s*([\d.]+)\)$/, "$1"));
        // A fully opaque stop would stop being a deepening and start being a
        // second material, hiding the base ramp it is supposed to modulate.
        expect(alpha).toBeLessThan(1);
      }
    });

    it("paints over the base but still under every tab", () => {
      const order = panelChildTestIds(mount(themeWith({})).screen);
      expect(order[0]).toBe("global-bottom-navigation-material");
      expect(order[1]).toBe("global-bottom-navigation-material-edge");
      expect(order.indexOf("global-bottom-home")).toBeGreaterThan(1);
    });

    it("clips itself to the same radius as the base", () => {
      const style = flatten(edgeOf(mount(themeWith({})).screen).props.style);
      expect(style.borderRadius).toBe(37);
      expect(style).toMatchObject(StyleSheet.absoluteFillObject);
    });

    it("stays out of the hit path", () => {
      expect(edgeOf(mount(themeWith({})).screen).props.pointerEvents).toBe("none");
    });

    it("stands down wherever the base does", () => {
      for (const theme of [themeWith({ highContrast: true }), ...(["black", "white", "light_futuristic"] as ThemeMode[]).map((mode) => themeWith({ mode }))]) {
        const { screen } = mount(theme);
        expect(
          screen.queryByTestId("global-bottom-navigation-material-edge", { includeHiddenElements: true })
        ).toBeNull();
      }
    });
  });

  describe("every other theme keeps the dock it had", () => {
    it("keeps the legacy fill and paints no material under high contrast", () => {
      const { screen } = mount(themeWith({ highContrast: true }));
      expect(screen.queryByTestId("global-bottom-navigation-material", { includeHiddenElements: true })).toBeNull();
      expect(panelStyle(screen).backgroundColor).toBe(LEGACY_PANEL_FILL);
    });

    it("keeps the legacy fill outside the released dark appearance", () => {
      for (const mode of ["black", "white", "light_futuristic"] as ThemeMode[]) {
        const { screen } = mount(themeWith({ mode }));
        expect(screen.queryByTestId("global-bottom-navigation-material", { includeHiddenElements: true })).toBeNull();
        expect(panelStyle(screen).backgroundColor).toBe(LEGACY_PANEL_FILL);
      }
    });
  });

  describe("the dock is geometrically unchanged", () => {
    it("holds every metric the scrollable surfaces reserve clearance against", () => {
      // These three are shared with `bottomNavMetrics.ts`: every scrollable
      // surface pads its content by a number derived from them. A colour change
      // that moved any of them would leave a band of dead space app-wide.
      const style = panelStyle(mount(themeWith({})).screen);
      expect(style.minHeight).toBe(BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT);
      expect(style.padding).toBe(BOTTOM_NAV_DOCK_PANEL_PADDING);
      expect(style.borderRadius).toBe(38);
      expect(style.borderWidth).toBe(1);
    });

    it("measures identically with the material on and off", () => {
      const geometry = (theme: Theme) => {
        const { minHeight, padding, borderRadius, borderWidth, borderColor, flexDirection, gap, alignItems } = panelStyle(
          mount(theme).screen
        );
        return { minHeight, padding, borderRadius, borderWidth, borderColor, flexDirection, gap, alignItems };
      };
      // Colour is the only difference. Anything else here is a regression.
      expect(geometry(themeWith({}))).toEqual(geometry(themeWith({ highContrast: true })));
    });

    it("keeps the safe-area inset it draws itself", () => {
      const { screen } = mount(themeWith({}));
      const shell = screen.getByTestId("global-bottom-navigation", { includeHiddenElements: true });
      expect(flatten(shell.props.style).paddingBottom).toBe(34);
    });
  });

  describe("the dock still behaves like a dock", () => {
    it("keeps the active tab marked as selected, and only the active one", () => {
      const { screen } = mount(themeWith({}));
      expect(screen.getByTestId("global-bottom-home").props.accessibilityState.selected).toBe(true);
      for (const other of ["reels", "messenger", "profile"]) {
        expect(screen.getByTestId(`global-bottom-${other}`).props.accessibilityState.selected).toBe(false);
      }
    });

    it("moves the active treatment with the route rather than pinning it to Home", () => {
      const { screen } = mount(themeWith({}), TABS.indexOf("Profile"));
      expect(screen.getByTestId("global-bottom-profile").props.accessibilityState.selected).toBe(true);
      expect(screen.getByTestId("global-bottom-home").props.accessibilityState.selected).toBe(false);
    });

    it("still routes on a tap on an inactive tab", () => {
      const { screen, navigate, emit } = mount(themeWith({}));
      fireEvent.press(screen.getByTestId("global-bottom-reels"));
      expect(emit).toHaveBeenCalledWith(expect.objectContaining({ type: "tabPress", target: "Reels-key" }));
      expect(navigate).toHaveBeenCalledWith("Reels");
    });

    it("still opens the composer from the Create button", () => {
      const { screen, navigate } = mount(themeWith({}));
      fireEvent.press(screen.getByTestId("global-bottom-create"));
      expect(navigate).toHaveBeenCalledWith("Home", { openComposer: true });
    });

    it("keeps every tab reachable and labelled, with the material present", () => {
      const { screen } = mount(themeWith({}));
      for (const id of ["home", "reels", "create", "messenger", "profile"]) {
        const tab = screen.getByTestId(`global-bottom-${id}`);
        expect(tab.props.accessibilityRole).toBe("tab");
        expect(typeof tab.props.accessibilityLabel).toBe("string");
        expect(tab.props.accessibilityState.disabled).toBe(false);
      }
    });
  });
});

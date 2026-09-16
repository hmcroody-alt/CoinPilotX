import React from "react";
import { render } from "@testing-library/react-native";

/**
 * `LinearGradient` is mocked as a plain host view that keeps its props, because
 * the colours it is handed *are* the thing worth asserting on here — the
 * wallpaper has no behaviour, so its props are its entire observable surface.
 */
jest.mock("expo-linear-gradient", () => {
  const react = require("react");
  const { View } = require("react-native");
  return { LinearGradient: (props: Record<string, unknown>) => react.createElement(View, props) };
});

const theme = {
  colors: { background: "#050910" },
  galacticBackground: { enabled: true, intensity: 1, variant: "dark" as "dark" | "light" },
  reduceTransparency: false
};

jest.mock("../../theme/ThemeContext", () => ({ useTheme: () => theme }));

import { ChatWallpaper } from "../ChatWallpaper";
import { DEFAULT_CHAT_WALLPAPER, resolveChatWallpaper } from "../../theme/chatWallpaper";

type Node = { props: Record<string, any>; children?: unknown };

function flatten(style: unknown): Record<string, any> {
  if (Array.isArray(style)) return Object.assign({}, ...style.filter(Boolean).map(flatten));
  return (style as Record<string, any>) || {};
}

function root(screen: ReturnType<typeof render>): Node {
  const tree = screen.toJSON();
  if (!tree || Array.isArray(tree)) throw new Error("expected a single root node");
  return tree as unknown as Node;
}

/**
 * Every gradient in the tree, outermost first.
 *
 * Deduped by array identity: a mocked `LinearGradient` matches at both the
 * composite and the host level, so a single logical gradient otherwise shows up
 * several times. The same `colors` array reference is threaded through each of
 * those layers, which makes identity the exact right key.
 */
function gradients(screen: ReturnType<typeof render>) {
  const seen = new Set<string[]>();
  for (const node of screen.UNSAFE_root.findAll((n: { props: { colors?: unknown } }) => Array.isArray(n.props.colors))) {
    seen.add(node.props.colors as string[]);
  }
  return [...seen];
}

beforeEach(() => {
  theme.galacticBackground = { enabled: true, intensity: 1, variant: "dark" };
  theme.reduceTransparency = false;
});

describe("ChatWallpaper", () => {
  it("is a background and nothing else — no touches, no accessibility presence", () => {
    const node = root(render(<ChatWallpaper />));
    expect(node.props.pointerEvents).toBe("none");
    expect(node.props.accessibilityElementsHidden).toBe(true);
    expect(node.props.importantForAccessibility).toBe("no-hide-descendants");
  });

  describe("precedence", () => {
    const cosmic = resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER);

    /**
     * The mission's rule is user custom → user selected built-in → default, and
     * the half that can actually go wrong on the client is the second arrow: a
     * stored choice must render, and only a genuinely absent choice may fall
     * through to the default. So absence and unrecognised values are asserted
     * to land on Cosmic, and a real id is asserted *not* to.
     */
    it.each([
      ["undefined", undefined],
      ["null", null],
      ["empty string", ""],
      ["the sentinel 'default'", "default"],
      ["an id this build does not know", "wallpaper_from_a_later_release"]
    ])("falls through to Cosmic for %s", (_label, value) => {
      const drawn = gradients(render(<ChatWallpaper wallpaper={value as string} />));
      expect(drawn[0]).toEqual(cosmic.gradient);
    });

    it("renders a stored choice instead of the default", () => {
      const drawn = gradients(render(<ChatWallpaper wallpaper="minimal_black" />));
      expect(drawn[0]).toEqual(resolveChatWallpaper("minimal_black").gradient);
      expect(drawn[0]).not.toEqual(cosmic.gradient);
    });
  });

  it("paints an opaque base on the outermost view, before any child", () => {
    // This is the no-flash guarantee: the base is a plain backgroundColor on
    // the root, so it is on screen in the first commit rather than one frame
    // after the gradient child lays out.
    const node = root(render(<ChatWallpaper />));
    expect(flatten(node.props.style).backgroundColor).toBe(resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER).base);
  });

  describe("accessibility and theme fallbacks", () => {
    it("drops to a flat opaque fill under Reduce Transparency", () => {
      theme.reduceTransparency = true;
      const screen = render(<ChatWallpaper />);
      expect(gradients(screen)).toHaveLength(0);
      expect(flatten(root(screen).props.style).backgroundColor).toBe(theme.colors.background);
    });

    it("drops to a flat opaque fill when the galactic background is off", () => {
      theme.galacticBackground = { enabled: false, intensity: 1, variant: "dark" };
      const screen = render(<ChatWallpaper />);
      expect(gradients(screen)).toHaveLength(0);
      expect(flatten(root(screen).props.style).backgroundColor).toBe(theme.colors.background);
    });

    it("keeps a bright page on light appearances rather than a navy field", () => {
      theme.galacticBackground = { enabled: true, intensity: 1, variant: "light" };
      const screen = render(<ChatWallpaper />);
      const drawn = gradients(screen);
      expect(drawn).toHaveLength(1);
      expect(drawn[0]).not.toEqual(resolveChatWallpaper(DEFAULT_CHAT_WALLPAPER).gradient);
      // Dark text on a dark cosmic field is the failure this branch prevents,
      // so assert the direction: every stop is a light colour.
      for (const stop of drawn[0]) expect(parseInt(stop.slice(1, 3), 16)).toBeGreaterThan(0xd0);
    });

    it("dims only the depth layer, never the gradient under it", () => {
      // Black theme wants a quieter field over true black. If `intensity` were
      // applied to the root or the gradient the background would go
      // translucent and the window would show through.
      theme.galacticBackground = { enabled: true, intensity: 0.4, variant: "dark" };
      const screen = render(<ChatWallpaper />);
      expect(flatten(root(screen).props.style).opacity).toBeUndefined();
      const dimmed = screen.UNSAFE_root.findAll(
        (node: { props: { style?: unknown } }) => flatten(node.props.style).opacity === 0.4
      );
      expect(dimmed.length).toBeGreaterThan(0);
      // The gradient itself keeps full opacity.
      const gradientNodes = screen.UNSAFE_root.findAll((node: { props: { colors?: unknown } }) =>
        Array.isArray(node.props.colors)
      );
      for (const gradient of gradientNodes) expect(flatten(gradient.props.style).opacity).toBeUndefined();
    });
  });

  it("re-renders nothing when the wallpaper prop is unchanged", () => {
    // `memo` over a string prop is what keeps composer keystrokes and arriving
    // messages from touching the background at all. Same element identity on
    // re-render is the observable consequence.
    const screen = render(<ChatWallpaper wallpaper="pulsesoc_cosmic" />);
    const before = screen.toJSON();
    screen.update(<ChatWallpaper wallpaper="pulsesoc_cosmic" />);
    expect(screen.toJSON()).toEqual(before);
  });
});

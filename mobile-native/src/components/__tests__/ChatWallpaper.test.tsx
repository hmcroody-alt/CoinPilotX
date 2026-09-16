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

/**
 * Doubles as a render counter. `useTheme` is the first thing the render body
 * does, ahead of every early return, so one call is one execution of the body —
 * which is the only way to tell a `memo` bail-out from a re-render that happens
 * to produce an identical tree.
 */
const mockRenders = jest.fn(() => theme);

jest.mock("../../theme/ThemeContext", () => ({ useTheme: () => mockRenders() }));

import { ChatWallpaper } from "../ChatWallpaper";
import { CHAT_WALLPAPER_IDS, DEFAULT_CHAT_WALLPAPER, resolveChatWallpaper } from "../../theme/chatWallpaper";

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
  mockRenders.mockClear();
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

  describe("cost", () => {
    /**
     * These are the mission's performance requirements, and they are asserted
     * here rather than measured on a device because the property in question is
     * discrete: either the background re-renders while you scroll and type, or
     * it does not. A frame-rate sample can only ever show that it was cheap
     * enough on one machine on one run; a render count settles it.
     */
    it("does not re-render when the parent re-renders with the same wallpaper", () => {
      // The chat screen re-renders on every composer keystroke, every arriving
      // message, and every scroll-driven state change. If any of those reached
      // the background, the wallpaper would be re-composited hundreds of times
      // in a normal conversation. `memo` over two primitive props is what stops
      // it — and only a render count can tell that bail-out apart from a
      // re-render that happens to produce an identical tree.
      const screen = render(<ChatWallpaper wallpaper="pulsesoc_cosmic" />);
      expect(mockRenders).toHaveBeenCalledTimes(1);

      for (let keystroke = 0; keystroke < 25; keystroke += 1) {
        screen.update(<ChatWallpaper wallpaper="pulsesoc_cosmic" />);
      }

      // Still one: the twenty-five re-renders never reached the background.
      expect(mockRenders).toHaveBeenCalledTimes(1);
    });

    it("does re-render when the viewer actually changes wallpaper", () => {
      // The positive control for the test above. Without it, "the render count
      // stayed at one" would also hold for a component that never updates at
      // all, which would be a broken wallpaper rather than a cheap one.
      const screen = render(<ChatWallpaper wallpaper="pulsesoc_cosmic" />);
      expect(mockRenders).toHaveBeenCalledTimes(1);
      screen.update(<ChatWallpaper wallpaper="minimal_black" />);
      expect(mockRenders).toHaveBeenCalledTimes(2);
    });

    it("schedules no timers, so there is no continuous animation", () => {
      jest.useFakeTimers();
      try {
        render(<ChatWallpaper wallpaper="pulsesoc_cosmic" />);
        // "One optimized static background layer, no continuous animation."
        // A single pending timer here would mean the background wakes the JS
        // thread for the whole time a conversation is open.
        expect(jest.getTimerCount()).toBe(0);
      } finally {
        jest.useRealTimers();
      }
    });

    it("costs a static view count in the same range as the wallpapers it joins", () => {
      // Counting host views is the honest way to size the default: the number is
      // identical on every machine and every run, unlike a frame rate.
      //
      // The default is in fact the *dearest* of the built-ins — 32 views against
      // the 24 of `deep_space`, which it replaced — so the useful question is
      // not "is it cheap in the abstract" but "is it in the same class as what
      // people already had". A few views more than the next-dearest is; an order
      // of magnitude would not be.
      //
      // None of this is per-message or per-frame work. The test above is what
      // establishes that the subtree renders once and is only composited after.
      const count = (id: string) =>
        render(<ChatWallpaper wallpaper={id} />).UNSAFE_root.findAll(
          (node: { type: unknown }) => typeof node.type === "string"
        ).length;

      const cosmic = count(DEFAULT_CHAT_WALLPAPER);
      // Deliberately excludes the default itself: a max over *every* id would
      // include Cosmic, and "Cosmic is at most the dearest of all of them"
      // is true no matter how expensive Cosmic gets.
      const dearestOther = Math.max(
        ...CHAT_WALLPAPER_IDS.filter((id) => id !== DEFAULT_CHAT_WALLPAPER).map(count)
      );

      expect(cosmic).toBeLessThanOrEqual(dearestOther + 8);
      // A loose ceiling too, to catch a redesign that starts costing hundreds of
      // views — without pinning the exact figure, which would turn every visual
      // tweak into a failing test.
      expect(cosmic).toBeLessThan(64);
    });
  });
});

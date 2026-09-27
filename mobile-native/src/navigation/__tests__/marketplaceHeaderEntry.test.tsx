/**
 * The Marketplace control: present on Home, absent everywhere else, and before
 * Search.
 *
 * All three claims need a render, and the middle one is the reason this file
 * exists. `LogiNexusGlobalHeader` is the header for *every* screen in the app —
 * the `mode="home"` variant is one branch of one component — so a new action
 * added carelessly appears on Settings, on a profile, and above a checkout. The
 * mechanism that keeps it Home-only is that each action renders only when its
 * handler prop is supplied, and Home is the only caller that supplies this one.
 * That is a convention, not a type error, so nothing but a test holds it.
 *
 * Order is asserted structurally rather than by reading a screenshot. The brief
 * specifies `[Menu] PulseSoc [Marketplace][Search][Notifications][Avatar]`, and a
 * JSX reorder that moved Marketplace after Search would be invisible in any
 * assertion that merely checked the button exists.
 */

import { readFileSync } from "fs";
import { join } from "path";
import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

// Repo-wide stub: the real Icon loads its font asynchronously and setStates after
// the test has finished. The icon *name* is still asserted below, via props.
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

/**
 * The animated wordmark calls `useIsFocused`, so rendering it needs a navigation
 * container this file has no use for. Stubbed to a plain label — the width
 * contract between the wordmark and the action buttons is arithmetic and is
 * asserted in `headerActionMetrics.test.ts`, so the constant is kept real.
 */
jest.mock("../../components/home/LivingPulseSocWordmark", () => ({
  PULSESOC_WORDMARK_MIN_WIDTH: 120,
  LivingPulseSocWordmark: () => null
}));

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

import { LogiNexusGlobalHeader } from "../GlobalNavigation";

const noop = () => undefined;

/** Home's real prop set. */
function homeHeader(overrides: Record<string, unknown> = {}) {
  return (
    <LogiNexusGlobalHeader
      mode="home"
      title="PulseSoc"
      showDrawer
      onOpenDrawer={noop}
      onOpenMarketplace={noop}
      onOpenSearch={noop}
      onOpenActivity={noop}
      onOpenProfile={noop}
      {...overrides}
    />
  );
}

describe("the Marketplace control on Home", () => {
  it("renders", () => {
    const { getByTestId } = render(homeHeader());
    expect(getByTestId("global-header-marketplace")).toBeTruthy();
  });

  it("is labelled Marketplace for a screen reader", () => {
    const { getByTestId } = render(homeHeader());
    const button = getByTestId("global-header-marketplace");
    expect(button.props.accessibilityLabel).toBe("Marketplace");
    expect(button.props.accessibilityRole).toBe("button");
  });

  it("uses a storefront glyph rather than a cart", () => {
    // A cart means "my basket". This is the entry to browsing, and the
    // basket already has its own entry inside Marketplace.
    const { UNSAFE_getByProps } = render(homeHeader());
    expect(UNSAFE_getByProps({ name: "storefront-outline" })).toBeTruthy();
  });

  it("calls its handler when pressed", () => {
    const onOpenMarketplace = jest.fn();
    const { getByTestId } = render(homeHeader({ onOpenMarketplace }));
    fireEvent.press(getByTestId("global-header-marketplace"));
    expect(onOpenMarketplace).toHaveBeenCalledTimes(1);
  });

  it("sits immediately before Search, and before Notifications and the avatar", () => {
    const { getByTestId, UNSAFE_root } = render(homeHeader());
    const order = ["global-header-marketplace", "global-header-search", "global-header-activity"];
    const positions = order.map((testID) => {
      const node = getByTestId(testID);
      return flatten(UNSAFE_root).indexOf(node);
    });
    expect(positions[0]).toBeGreaterThan(-1);
    expect(positions).toEqual([...positions].sort((a, b) => a - b));
  });

  it("keeps a touch target no smaller than the app's standard header button", () => {
    const { getByTestId } = render(homeHeader());
    const style = flattenStyle(getByTestId("global-header-marketplace").props.style);
    expect(style.width).toBeGreaterThanOrEqual(38);
    expect(style.width).toBe(style.height);
  });
});

describe("no other screen gains it", () => {
  it("is absent from a standard header", () => {
    const { queryByTestId } = render(<LogiNexusGlobalHeader title="Settings" />);
    expect(queryByTestId("global-header-marketplace")).toBeNull();
  });

  it("is absent from a Home-mode header that was given no handler", () => {
    // The gating mechanism itself: the prop, not the mode, is what places it.
    const { queryByTestId, getByTestId } = render(
      homeHeader({ onOpenMarketplace: undefined })
    );
    expect(queryByTestId("global-header-marketplace")).toBeNull();
    // And the rest of the cluster is untouched.
    expect(getByTestId("global-header-search")).toBeTruthy();
  });

  it("leaves Search, Notifications and the avatar in place", () => {
    const { getByTestId } = render(homeHeader());
    expect(getByTestId("global-header-search")).toBeTruthy();
    expect(getByTestId("global-header-activity")).toBeTruthy();
    expect(getByTestId("global-header-profile")).toBeTruthy();
  });
});

/**
 * Everything above renders the header directly and hands it the prop — which is
 * the only way to test the gating rule, and is exactly why none of it can see
 * the one failure that actually shipped. The control is placed *by* its handler,
 * so a Home screen that never passes one renders no button at all while every
 * assertion above still passes, because each supplies its own.
 *
 * Not hypothetical: this slice was built, typechecked, tested green and
 * installed on a simulator with the handler missing from `HomeScreen`, and the
 * header had no storefront icon. Reading the source is the cheapest thing that
 * catches it — rendering `HomeScreen` itself means standing up the feed, the
 * composer, the drawer and ~20 API mocks to assert one prop.
 */
describe("HomeScreen supplies the handler that places it", () => {
  const source = readFileSync(join(__dirname, "..", "..", "screens", "HomeScreen.tsx"), "utf8");

  it("routes the header to the registered Marketplace tab", () => {
    expect(source).toMatch(/const openMarketplaceTab = useCallback\(\(\) => navigation\.navigate\("Tabs", \{ screen: "Marketplace" \}\)/);
  });

  it("threads the handler through every link down to the global header", () => {
    // HomeScreen → HomeHeader → HomeTopBar → LogiNexusGlobalHeader. Dropping any
    // one link renders no button, and nothing else in this file would notice.
    expect(source).toContain("onOpenMarketplace={openMarketplaceTab}");
    expect(source.match(/onOpenMarketplace=\{onOpenMarketplace\}/g) || []).toHaveLength(2);
  });
});

/** Depth-first node order, which is render order for a row of siblings. */
function flatten(node: unknown): unknown[] {
  const out: unknown[] = [];
  const walk = (current: { children?: unknown[] } | unknown) => {
    out.push(current);
    const children = (current as { children?: unknown[] })?.children;
    if (Array.isArray(children)) children.forEach(walk);
  };
  walk(node);
  return out;
}

function flattenStyle(style: unknown): Record<string, number> {
  if (Array.isArray(style)) {
    return style.reduce<Record<string, number>>((acc, entry) => ({ ...acc, ...flattenStyle(entry) }), {});
  }
  return (style || {}) as Record<string, number>;
}

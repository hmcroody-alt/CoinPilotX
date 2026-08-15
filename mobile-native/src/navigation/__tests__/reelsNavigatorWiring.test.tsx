/**
 * Does a Reels swipe move the navigator the user is actually looking at?
 *
 * Every other test of this feature mocks `useBottomNavVisibility`, which means
 * they all answer a narrower question: "did the screen call the setter". They
 * would pass unchanged if the screen were talking to a *different* provider
 * instance than the dock reads from, or to the inert default object the hook
 * returns when there is no provider above it at all — and in both of those
 * worlds the dock never moves on a device. Green tests, dead feature.
 *
 * So this file mocks nothing on the visibility path. It mounts the real
 * `BottomNavVisibilityProvider`, drives the real `useImmersiveNavigator`, and
 * reads the resulting state back through the same public hook the rendered dock
 * uses. It also pins the two vetoes that sit between "state says hidden" and
 * "dock is off screen": the per-route policy, and the keyboard.
 */
import React from "react";
import { Text } from "react-native";
import { act, render, screen } from "@testing-library/react-native";
import { BottomNavVisibilityProvider, useBottomNavVisibility } from "../BottomNavVisibility";
import { resolveBottomNavPolicy } from "../bottomNavPolicy";
import { __clearSpatialFlagOverrides, __setSpatialFlagOverride } from "../../spatial/flags";
import type { HorizontalSwipe } from "../../spatial/navigatorVisibility";
import { useImmersiveNavigator } from "../../spatial/useImmersiveNavigator";

/** Stands in for ReelsScreen: the only thing it does is report swipes. */
let notifySwipe!: (swipe: HorizontalSwipe) => void;
function ReelsSurface() {
  notifySwipe = useImmersiveNavigator(true).notifySwipe;
  return null;
}

/**
 * Stands in for the rendered dock. It reads exactly what
 * `LogiNexusBottomNavigation` reads, so if this probe does not see the change,
 * neither does the dock.
 */
function NavigatorProbe() {
  const { hidden, docked } = useBottomNavVisibility();
  return <Text>{`${docked ? "docked" : "undocked"}:${hidden ? "hidden" : "visible"}`}</Text>;
}

const dockState = () => screen.getByText(/^(un)?docked:/).props.children;

const right = (): HorizontalSwipe => ({ source: "touch", startOffsetX: 0, endOffsetX: -120 });
const left = (): HorizontalSwipe => ({ source: "touch", startOffsetX: 0, endOffsetX: 120 });

async function swipe(gesture: HorizontalSwipe) {
  await act(async () => notifySwipe(gesture));
}

beforeEach(() => {
  __clearSpatialFlagOverrides();
  __setSpatialFlagOverride("spatialConsoleEnabled", true);
  __setSpatialFlagOverride("immersiveNavigatorEnabled", true);
});

afterEach(() => __clearSpatialFlagOverrides());

describe("Reels drives the real bottom navigator", () => {
  async function mountUnderProvider() {
    const utils = render(
      <BottomNavVisibilityProvider>
        <ReelsSurface />
        <NavigatorProbe />
      </BottomNavVisibilityProvider>
    );
    await act(async () => undefined);
    return utils;
  }

  it("hides the dock the probe can see, on a right swipe", async () => {
    await mountUnderProvider();
    expect(dockState()).toBe("docked:visible");

    await swipe(right());

    expect(dockState()).toBe("docked:hidden");
  });

  it("restores it on a left swipe", async () => {
    await mountUnderProvider();
    await swipe(right());

    await swipe(left());

    expect(dockState()).toBe("docked:visible");
  });

  it("survives repeated right swipes without getting stuck", async () => {
    // The acceptance criterion, stated as state rather than as call counts:
    // however many times the gesture repeats, the dock ends up where the last
    // gesture asked for it.
    await mountUnderProvider();

    await swipe(right());
    await swipe(right());
    await swipe(right());
    expect(dockState()).toBe("docked:hidden");

    await swipe(left());
    expect(dockState()).toBe("docked:visible");

    await swipe(right());
    expect(dockState()).toBe("docked:hidden");
  });

  it("leaves the dock alone for a cancelled drag and for tilt", async () => {
    await mountUnderProvider();

    await swipe({ source: "touch", startOffsetX: 400, endOffsetX: 400 });
    expect(dockState()).toBe("docked:visible");

    await swipe(right());
    await swipe({ source: "motion", startOffsetX: 0, endOffsetX: 375 });
    expect(dockState()).toBe("docked:hidden");
  });

  it("reports no dock at all when Reels is mounted outside the provider", async () => {
    // The control for everything above. Reels is registered both as a tab and
    // as a pushed stack destination, and the provider only wraps the tab
    // navigator — so a pushed instance gets the inert default object and its
    // setter goes nowhere. If the assertions above ever start passing in this
    // configuration, they have stopped testing the wiring.
    render(
      <>
        <ReelsSurface />
        <NavigatorProbe />
      </>
    );
    await act(async () => undefined);

    await swipe(right());

    expect(dockState()).toBe("undocked:visible");
  });
});

describe("vetoes between hidden state and a hidden dock", () => {
  it("does not let the Reels route policy override the hide", async () => {
    // `LogiNexusBottomNavigation` forces `hidden` to false for any route whose
    // policy is "always-visible". Moving Reels into that group — a one-word
    // edit in a table that says nothing about gestures — would make every
    // assertion in this file pass while the dock never moved on a device.
    expect(resolveBottomNavPolicy("Reels")).not.toBe("always-visible");
    expect(resolveBottomNavPolicy("Reels")).toBe("scroll-responsive");
  });
});

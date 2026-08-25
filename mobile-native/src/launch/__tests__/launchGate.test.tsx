/**
 * The launch gate.
 *
 * The gate makes one promise in two directions, and both halves have to be
 * tested because breaking either one is silent:
 *
 *   - Nothing that works gets locked. The table is an explicit deny-list and
 *     the default is READY, so the risk is not "a module stayed locked" — it is
 *     a stale row outliving the backend that landed, or a typo'd key that
 *     gates a module nobody can find. Those are config invariants, asserted
 *     here against the real registry rather than against a fixture.
 *   - Nothing that is locked can be entered. A card that shows Coming Soon is
 *     only half a gate; the other half is the route refusing to render however
 *     it was reached.
 *
 * And one presentational promise the brief is explicit about: a locked module
 * reads as *early*, never as *broken*. That is testable — the state has to
 * survive greyscale (it is in the label and in a word on the card, not only in
 * the teal), the control has to stay pressable rather than disabled, and the
 * vocabulary has to stay inside "Coming Soon" / "Building" / "Preparing for
 * Launch".
 */

import React from "react";
import { fireEvent, render, renderHook } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

// The router under test picks between the manager and the Coming Soon screen.
// Stubbing the manager keeps this suite off its whole import graph and makes
// "the manager did not render" an assertion rather than an inference.
jest.mock("../../screens/EventsManagerScreen", () => ({
  EventsManagerScreen: () => null
}));

import { BUSINESS_OS_SECTIONS, businessOsHubSections, businessOsLaunchSections } from "../../api/businessOs";
import { preloadNamespaces, translate } from "../../i18n/engine";
import { EventsRoute } from "../../screens/EventsRoute";
import { ComingSoonSheet } from "../ComingSoonSheet";
import { LaunchTile } from "../LaunchTile";
import { LOCKED_GLOW_MAX, LOCKED_GLOW_MIN, useLockedMotion } from "../lockedMotion";
import {
  GATED_ROUTES,
  LAUNCH_READINESS,
  businessModuleId,
  isLaunchGated,
  isLaunchReady,
  presenceModuleId,
  readinessOf,
  routeReadiness
} from "../readiness";

// Catalogs are lazy; without this the launch copy degrades to humanized keys
// and every assertion below would be about "Locked Label" rather than about
// what a user reads.
beforeAll(async () => {
  await preloadNamespaces("en", ["commerce", "common"]);
});

const GATED_IDS = Object.keys(LAUNCH_READINESS);

describe("launch readiness table", () => {
  it("treats an unregistered module as ready", () => {
    // The default is the safety property: the mission forbids blindly locking
    // a production-ready feature, so a module nobody registered opens.
    expect(readinessOf("business:orders")).toBe("READY");
    expect(readinessOf(businessModuleId("marketplace"))).toBe("READY");
    expect(isLaunchReady("presence:somethingNobodyHasWrittenYet")).toBe(true);
  });

  it("carries no rows that resolve READY", () => {
    // A `READY` row is a gate that does nothing while reading, at a glance, as
    // a gate — the exact shape that survives long after the backend landed.
    GATED_IDS.forEach((id) => {
      expect(readinessOf(id)).not.toBe("READY");
      expect(isLaunchGated(id)).toBe(true);
    });
  });

  it("names only modules that exist", () => {
    // A typo'd key does not fail loudly: it silently gates nothing, because
    // unknown ids are READY. This is what makes that failure visible.
    // Widened to `string` on purpose: the ids in the table are plain strings,
    // and the whole point of this test is to catch one that does not match a
    // section key. A `Set<BusinessOsSectionKey>` would refuse the lookup at
    // compile time and take the runtime check with it.
    const sectionKeys = new Set<string>(BUSINESS_OS_SECTIONS.map((section) => section.key));
    GATED_IDS.filter((id) => id.startsWith("business:")).forEach((id) => {
      expect(sectionKeys.has(id.slice("business:".length))).toBe(true);
    });
    // Presence has one gated action and it is the one the audit found: the
    // per-presence Business OS entry that navigates without a page id.
    expect(readinessOf(presenceModuleId("businessOs"))).toBe("BUILDING");
  });

  it("registers only routes whose module is actually gated", () => {
    Object.entries(GATED_ROUTES).forEach(([routeName, moduleId]) => {
      expect(isLaunchGated(moduleId)).toBe(true);
      expect(routeReadiness(routeName)).toBe(readinessOf(moduleId));
    });
    // `BusinessOs` is deliberately absent: reached from the profile tile it is
    // correct, and only the presence-scoped entry into it is gated. Registering
    // the route here would lock the working path too.
    expect(routeReadiness("BusinessOs")).toBe("READY");
    expect(routeReadiness("SomeRouteNobodyRegistered")).toBe("READY");
  });
});

describe("the Business landing presents locked modules rather than hiding them", () => {
  it("keeps every routable section and adds the gated ones", () => {
    const launch = businessOsLaunchSections().map((section) => section.key);
    // Gating is additive. A section that used to open must still be presented.
    businessOsHubSections().forEach((section) => {
      expect(launch).toContain(section.key);
    });
    // Customers and Team were hidden by `backed: false`. The brief's premise is
    // that a user should be able to see the shape of the product, so they are
    // on the landing now — locked, not absent.
    expect(launch).toContain("customers");
    expect(launch).toContain("team");
  });

  it("never presents a section that has neither a route nor a gate", () => {
    // Such a tile would throw from `businessOsNavigationArgs` on tap. The
    // resolver is what makes that unrepresentable.
    businessOsLaunchSections().forEach((section) => {
      expect(Boolean(section.route) || isLaunchGated(businessModuleId(section.key))).toBe(true);
    });
  });
});

describe("a locked tile", () => {
  const lockedProps = {
    id: businessModuleId("customers"),
    label: "Customers",
    blurb: "Everyone who has bought from you.",
    icon: "people-outline",
    index: 0,
    motionEnabled: true,
    screenActive: true
  };

  it("says it is coming soon in its accessibility label, not only in its colour", () => {
    const onPress = jest.fn();
    const view = render(<LaunchTile {...lockedProps} onPress={onPress} />);
    const tile = view.getByTestId("launch-tile-business:customers");

    // iOS hints are off by default and colour is not a channel a screen reader
    // has, so the state has to be in the label itself.
    expect(tile.props.accessibilityLabel).toBe("Customers. Coming soon.");
    // And on the card, in words, for a sighted user in greyscale.
    expect(view.getByText("Coming Soon")).toBeTruthy();
  });

  it("stays a live control instead of being disabled", () => {
    const onPress = jest.fn();
    const view = render(<LaunchTile {...lockedProps} onPress={onPress} />);
    const tile = view.getByTestId("launch-tile-business:customers");

    // `disabled` tells a user they did something wrong. This module is not
    // disabled, it is early — and the tap is how they find that out.
    expect(tile.props.accessibilityState?.disabled).toBeFalsy();
    fireEvent.press(tile);
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it("keeps a ready tile's own blurb and gives it no badge", () => {
    const view = render(
      <LaunchTile
        id={businessModuleId("marketplace")}
        label="Marketplace"
        blurb="Your storefront and listings."
        icon="storefront-outline"
        index={1}
        motionEnabled
        screenActive
        onPress={jest.fn()}
      />
    );
    const tile = view.getByTestId("launch-tile-business:marketplace");
    expect(tile.props.accessibilityLabel).toBe("Marketplace. Your storefront and listings.");
    expect(view.queryByText("Coming Soon")).toBeNull();
    expect(view.queryByText("Building")).toBeNull();
  });
});

describe("reduce motion", () => {
  it("stops the movement but keeps the locked card's halo", () => {
    const moving = renderHook(() => useLockedMotion({ index: 0, active: true, enabled: true }));
    const still = renderHook(() => useLockedMotion({ index: 0, active: true, enabled: false }));

    // With motion on, the halo's opacity is driven by an interpolation.
    expect(typeof moving.result.current.glowStyle.opacity).not.toBe("number");
    // With motion off it is a fixed mid-strength number — the premium locked
    // look survives; only the movement is gone. Reduce Motion is not a reason
    // to make a card look unfinished in a different way.
    const restingOpacity = still.result.current.glowStyle.opacity;
    expect(typeof restingOpacity).toBe("number");
    expect(restingOpacity).toBe((LOCKED_GLOW_MIN + LOCKED_GLOW_MAX) / 2);
    expect(restingOpacity as number).toBeGreaterThan(0);
  });

  it("leaves the press response inert rather than half-animated", () => {
    const still = renderHook(() => useLockedMotion({ index: 0, active: true, enabled: false }));
    // No assertion on a value here — the point is that calling these under
    // Reduce Motion is safe and starts nothing. A throw or a started spring
    // would be the regression.
    expect(() => {
      still.result.current.onPressIn();
      still.result.current.onPressOut();
    }).not.toThrow();
  });
});

describe("the Coming Soon message", () => {
  it("says what the brief says, for whichever module was tapped", () => {
    const onDismiss = jest.fn();
    const view = render(<ComingSoonSheet target={{ id: "business:team", label: "Team" }} onDismiss={onDismiss} />);

    expect(view.getByText("COMING SOON")).toBeTruthy();
    expect(view.getByText("Team")).toBeTruthy();
    expect(view.getByText("We're building this part of the PulseSoc universe. This feature is preparing for launch.")).toBeTruthy();

    fireEvent.press(view.getByTestId("coming-soon-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("never uses developer language", () => {
    // The brief names the allowed vocabulary and forbids the rest. This is the
    // assertion that keeps a well-meaning "temporarily unavailable" out of the
    // catalog months from now, when the reason is no longer in anyone's head.
    const forbidden = /broken|not implemented|unimplemented|unavailable|disabled|error|todo|wip|failed/i;
    const keys = [
      "comingSoonTitle",
      "comingSoonBody",
      "comingSoonAction",
      "statusComingSoon",
      "statusBuilding",
      "statusPreparing",
      "lockedLabel",
      "lockedHint"
    ];
    keys.forEach((key) => {
      expect(translate(`commerce:launch.${key}`, { module: "Team" })).not.toMatch(forbidden);
    });
  });
});

describe("the deep-link boundary", () => {
  it("refuses to render a gated route however it was reached", () => {
    const goBack = jest.fn();
    const view = render(<EventsRoute navigation={{ navigate: jest.fn(), goBack }} />);

    // Arriving here without passing the card — a deep link, restored state, a
    // stray navigate — still lands on the message, not the half-built manager.
    expect(view.getByTestId("coming-soon-screen-business:events")).toBeTruthy();
    expect(view.getByText("COMING SOON")).toBeTruthy();

    // And there is a way out. A gate the user cannot back out of is a trap.
    fireEvent.press(view.getByTestId("coming-soon-screen-dismiss"));
    expect(goBack).toHaveBeenCalledTimes(1);
  });
});

/**
 * The revert, pinned.
 *
 * Reverting a design by flipping a router is cheap to do and cheap to undo by
 * accident — a stray `mode: "hub"` in a push payload, or someone flipping
 * `HUB_LIVE_CARDS` while chasing something else, would quietly put the defective
 * light hub back in front of every seller. So the guarantee under test is not
 * "the dark screen can render", it is "nothing a caller passes can reach the
 * light one while the flag is off".
 *
 * The light screen is mocked rather than imported for real because the route
 * defers its require: if the flag is honoured, that module is never pulled in at
 * all, and a test that imported it eagerly would not notice a regression back to
 * a module-scope import.
 */
import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("../BusinessOsScreen", () => {
  const { Text } = require("react-native");
  return { BusinessOsScreen: () => <Text>DARK</Text> };
});
jest.mock("../BusinessHubScreen", () => {
  const { Text } = require("react-native");
  return { BusinessHubScreen: () => <Text>LIGHT</Text> };
});

const navigation = { navigate: jest.fn() };

/** Re-import the route with `HUB_LIVE_CARDS` forced to a given value. */
function routeWithFlag(enabled: boolean) {
  let Route: (props: any) => any;
  jest.isolateModules(() => {
    jest.doMock("../../api/businessOs", () => ({
      ...jest.requireActual("../../api/businessOs"),
      HUB_LIVE_CARDS: enabled
    }));
    Route = require("../BusinessHubRoute").BusinessHubRoute;
  });
  return Route!;
}

describe("BusinessOs route after the revert", () => {
  it("renders the dark sections screen for a plain visit", () => {
    const { BusinessHubRoute } = require("../BusinessHubRoute");
    expect(render(<BusinessHubRoute navigation={navigation} />).getByText("DARK")).toBeTruthy();
  });

  it("renders the dark screen for every param a real caller passes", () => {
    const { BusinessHubRoute } = require("../BusinessHubRoute");
    // A titled deep link, the old escape hatch, and the redesign's own opt-in.
    // All three land in the same place while the flag is off — including
    // `mode: "hub"`, which is the one that would hurt.
    for (const params of [{ title: "Business OS" }, { mode: "classic" as const }, { mode: "hub" as const }]) {
      const view = render(<BusinessHubRoute route={{ params }} navigation={navigation} />);
      expect(view.getByText("DARK")).toBeTruthy();
      expect(view.queryByText("LIGHT")).toBeNull();
    }
  });

  it("keeps the light hub unreachable even with the opt-in param, because the flag is what decides", () => {
    const Route = routeWithFlag(false);
    const view = render(<Route route={{ params: { mode: "hub" } }} navigation={navigation} />);
    expect(view.getByText("DARK")).toBeTruthy();
  });

  it("still reaches the light hub when the flag is turned back on, so the revert is undoable", () => {
    // This is the half that proves the code was flagged off rather than broken.
    // If a future owner re-enables the redesign, this is the path they take.
    const Route = routeWithFlag(true);
    expect(render(<Route route={{ params: { mode: "hub" } }} navigation={navigation} />).getByText("LIGHT")).toBeTruthy();
  });

  it("does not hand the light hub a plain visit even when the flag is on", () => {
    // Flag on is "the redesign exists", not "the redesign is the default". A
    // re-enable should be an explicit opt-in, not a silent flip for every link.
    const Route = routeWithFlag(true);
    expect(render(<Route navigation={navigation} />).getByText("DARK")).toBeTruthy();
  });
});

describe("the flag itself", () => {
  it("ships off", () => {
    // The revert is only real if the shipped value is false. A test that only
    // exercised the router would pass just as happily with the redesign live.
    const { HUB_LIVE_CARDS } = jest.requireActual("../../api/businessOs");
    expect(HUB_LIVE_CARDS).toBe(false);
  });
});

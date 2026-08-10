/**
 * The Advertising card, its deep links and its notifications all point at one
 * route name. The rebuild put a new screen behind that name and kept the old
 * one for the creation forms, so the only thing that can break is the predicate
 * that chooses between them — and breaking it would send someone tapping
 * "Create campaign" to a screen with no form on it.
 */
import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("../AdsManagerScreen", () => {
  const { Text } = require("react-native");
  return { AdsManagerScreen: () => <Text>MANAGER</Text> };
});
jest.mock("../BusinessOsAdvertisingScreen", () => {
  const { Text } = require("react-native");
  return { BusinessOsAdvertisingScreen: () => <Text>CLASSIC</Text> };
});
jest.mock("../AdsSubPageScreen", () => {
  const { Text } = require("react-native");
  return { AdsSubPageScreen: ({ surface }: { surface: string }) => <Text>{`SUB:${surface}`}</Text> };
});
jest.mock("../AdsAudiencesScreen", () => {
  const { Text } = require("react-native");
  return { AdsAudiencesScreen: () => <Text>AUDIENCES</Text> };
});
jest.mock("../AdsLibraryScreen", () => {
  const { Text } = require("react-native");
  return { AdsLibraryScreen: () => <Text>LIBRARY</Text> };
});
jest.mock("../AdsPolicyCenterScreen", () => {
  const { Text } = require("react-native");
  return { AdsPolicyCenterScreen: () => <Text>POLICY</Text> };
});
jest.mock("../AdsReportsScreen", () => {
  const { Text } = require("react-native");
  return { AdsReportsScreen: () => <Text>REPORTS</Text> };
});
jest.mock("../AdsWalletScreen", () => {
  const { Text } = require("react-native");
  return { AdsWalletScreen: () => <Text>WALLET</Text> };
});
jest.mock("../AdsInsightsScreen", () => {
  const { Text } = require("react-native");
  return { AdsInsightsScreen: () => <Text>INSIGHTS</Text> };
});

import { AdvertisingRoute } from "../AdvertisingRoute";

describe("BusinessOsAdvertising route", () => {
  it("lands on the manager by default, so existing links keep working", () => {
    expect(render(<AdvertisingRoute />).getByText("MANAGER")).toBeTruthy();
    expect(
      render(<AdvertisingRoute route={{ params: { title: "Advertising" } }} />).getByText("MANAGER")
    ).toBeTruthy();
  });

  it("routes to the classic screen for the creation flows", () => {
    const view = render(<AdvertisingRoute route={{ params: { mode: "classic" } }} />);
    expect(view.getByText("CLASSIC")).toBeTruthy();
  });

  /**
   * The sub-pages share this route name on purpose. A tile pointed at a name the
   * navigator doesn't know does not degrade to a blank screen — it throws — so
   * every wave-2 surface, and the read-only account-standing page, are reached
   * by `mode` rather than by new names. `audiences` and `creatives` used to
   * land on the static `AdsSubPageScreen`; wave 2 gave each its own manager,
   * so those modes now dispatch to the full screens.
   */
  it("dispatches each sub-page mode to its own surface", () => {
    const expectations = [
      ["audiences", "AUDIENCES"],
      ["creatives", "LIBRARY"],
      ["policy", "POLICY"],
      ["reports", "REPORTS"],
      ["wallet", "WALLET"],
      ["insights", "INSIGHTS"],
      ["account", "SUB:account"]
    ] as const;
    for (const [mode, marker] of expectations) {
      const view = render(<AdvertisingRoute route={{ params: { mode } }} />);
      expect(view.getByText(marker)).toBeTruthy();
    }
  });

  /**
   * An unrecognised mode is a typo, a stale deep link or an older build's
   * notification. None of those should land nowhere: the manager is the safe
   * destination because it is where every one of these surfaces is reachable
   * from anyway.
   */
  it("falls back to the manager for a mode it doesn't recognise", () => {
    const view = render(<AdvertisingRoute route={{ params: { mode: "audience" as never } }} />);
    expect(view.getByText("MANAGER")).toBeTruthy();
  });
});

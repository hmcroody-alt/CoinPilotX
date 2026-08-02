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
});

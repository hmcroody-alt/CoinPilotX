/**
 * "Crypto intelligence", asserted as a set of destinations.
 *
 * The section used to list alerts and portfolio and stop there, which made it
 * describe a workflow it could not start: alerts are pointed at watchlists, but
 * the only surface that advertises alerts offered no way to reach the lists they
 * watch. These tests pin the roster and, more importantly, pin *where each row
 * goes* — a row that looks tappable and lands nowhere is the failure this
 * section is most prone to.
 */

import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: ({ children }: { children?: React.ReactNode }) => children ?? null }));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => `date(${value})`, number: (value: number) => String(value) })
}));

import { openDashboardRoute } from "../../navigation/dashboardRouting";
import { CryptoIntelligenceSection } from "../PremiumCenterScreen";

const nav = () => ({ navigate: jest.fn() });

describe("Crypto intelligence — the roster", () => {
  it("offers every crypto surface that actually ships, watchlists included", () => {
    const navigation = nav();
    const { getByText } = render(
      <CryptoIntelligenceSection navigation={navigation as never} />
    );
    for (const key of ["alerts", "portfolio", "watchlists", "undx"]) {
      expect(getByText(`discovery:crypto.intelligence.${key}.label`)).toBeTruthy();
    }
  });
});

describe("Crypto intelligence — where the rows go", () => {
  it.each([
    ["alerts", "AlertManagement", { title: "Alerts" }],
    ["portfolio", "Portfolio", undefined],
    ["watchlists", "Watchlists", { title: "Watchlists" }],
    // `UndxCapabilities` is a registered route rendering the server-authoritative
    // capability registry, and the Command Center on this same screen already
    // opens it. Leaving this row inert made the section advertise UNDX crypto
    // intelligence while refusing to open the one screen that reports it.
    ["undx", "UndxCapabilities", undefined]
  ])("sends %s to its own screen", (key, route, params) => {
    const navigation = nav();
    const { getByLabelText } = render(
      <CryptoIntelligenceSection navigation={navigation as never} />
    );
    fireEvent.press(getByLabelText(`discovery:crypto.intelligence.${key}.label`));
    expect(navigation.navigate.mock.calls).toEqual([params ? [route, params] : [route]]);
  });
});

// Compare actual Premium presses with dashboard dispatch, including params.
describe("Premium reuses the dashboard system", () => {
  it.each([
    ["alerts", "/dashboard/crypto/alerts"],
    ["portfolio", "/pulse/portfolio"],
    ["watchlists", "/dashboard/crypto/watchlists"]
  ])("%s has exactly the dashboard destination and state inputs", (key, path) => {
    const dashboard = nav();
    const premium = nav();
    openDashboardRoute(dashboard, path);
    const { getByLabelText } = render(<CryptoIntelligenceSection navigation={premium as never} />);
    fireEvent.press(getByLabelText(`discovery:crypto.intelligence.${key}.label`));
    expect(premium.navigate.mock.calls).toEqual(dashboard.navigate.mock.calls);
    expect(premium.navigate).toHaveBeenCalledTimes(1);
  });
});

// Saved navigation state and older callers must not resurrect separate clients.
describe("Legacy crypto entry points cannot fork the dashboard implementation", () => {
  it("exports the exact dashboard alert component", () => {
    const { CryptoAlertCenterScreen } = require("../CryptoAlertCenterScreen");
    const { AlertManagementScreen } = require("../AlertManagementScreen");
    expect(CryptoAlertCenterScreen).toBe(AlertManagementScreen);
  });

  it("exports the exact dashboard portfolio component", () => {
    const { CryptoPortfolioScreen } = require("../CryptoPortfolioScreen");
    const { PortfolioScreen } = require("../PortfolioScreen");
    expect(CryptoPortfolioScreen).toBe(PortfolioScreen);
  });
});

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
    ["alerts", "CryptoAlertCenter"],
    ["portfolio", "CryptoPortfolio"],
    ["watchlists", "Watchlists"]
  ])("sends %s to its own screen", (key, route) => {
    const navigation = nav();
    const { getByLabelText } = render(
      <CryptoIntelligenceSection navigation={navigation as never} />
    );
    fireEvent.press(getByLabelText(`discovery:crypto.intelligence.${key}.label`));
    expect(navigation.navigate).toHaveBeenCalledWith(route);
  });

  /**
   * UNDX surfaces inside the other flows and has no standalone route. A chevron
   * and a press handler here would promise a screen that does not exist, so the
   * row stays inert on purpose — and stays that way only if something asserts it.
   *
   * Asserted through the accessibility tree rather than by pressing: only the
   * navigating rows are exposed as buttons, so the absence of a button label is
   * exactly the promise being kept. Assistive tech is told what is tappable, and
   * UNDX is not offered as such.
   */
  it("leaves UNDX inert rather than pointing it at a screen that does not exist", () => {
    const navigation = nav();
    const { getByText, queryByLabelText } = render(
      <CryptoIntelligenceSection navigation={navigation as never} />
    );
    expect(getByText("discovery:crypto.intelligence.undx.label")).toBeTruthy();
    expect(queryByLabelText("discovery:crypto.intelligence.undx.label")).toBeNull();
  });
});

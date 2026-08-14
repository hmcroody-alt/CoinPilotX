import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: ({ children }: { children?: React.ReactNode }) => children ?? null }));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => value, number: (value: number) => String(value) })
}));

import { PlansSection } from "../PremiumCenterScreen";

const baseProps = {
  plan: "annual" as const,
  onPlan: jest.fn(),
  busy: false,
  disabled: false,
  onPurchase: jest.fn(),
  onRetry: jest.fn(),
  expired: false
};

describe("Premium plan terminal states", () => {
  beforeEach(() => jest.clearAllMocks());

  it("renders loading only while the request is active", () => {
    const { getByText } = render(<PlansSection {...baseProps} offers={null} loading />);
    expect(getByText("premium:plans.loading")).toBeTruthy();
  });

  it("renders unavailable with a working retry after an empty response", () => {
    const onRetry = jest.fn();
    const { getByText } = render(
      <PlansSection {...baseProps} onRetry={onRetry} loading={false} offers={{ plans: [], annualSavingsPercent: null, status: "empty", missingPlans: ["monthly", "annual"] }} />
    );
    expect(getByText("premium:plans.unavailable")).toBeTruthy();
    fireEvent.press(getByText("premium:retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("offers only the real localized product and reports the missing plan", () => {
    const onPurchase = jest.fn();
    const { getByText, queryByText, getByLabelText } = render(
      <PlansSection
        {...baseProps}
        plan="monthly"
        onPurchase={onPurchase}
        loading={false}
        offers={{
          plans: [{ plan: "monthly", productId: "com.pulsesoc.premium.monthly", displayPrice: "€9,99", price: 9.99, currency: "EUR" }],
          annualSavingsPercent: null,
          status: "success",
          missingPlans: ["annual"]
        }}
      />
    );
    expect(getByText("€9,99")).toBeTruthy();
    expect(getByText("The annual plan is temporarily unavailable.")).toBeTruthy();
    expect(queryByText("$99.99")).toBeNull();
    fireEvent.press(getByLabelText("premium:purchase.start"));
    expect(onPurchase).toHaveBeenCalledTimes(1);
  });
});

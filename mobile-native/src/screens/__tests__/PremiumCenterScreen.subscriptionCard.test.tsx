/**
 * "Your subscription", asserted row by row.
 *
 * The bug this card was built to end was a paying member being told
 * "We don't have billing details for this membership." The tests below therefore
 * spend most of their effort on what the card must *never* say: the wrong verb
 * next to the right date, an invented price, a stale "Active" on a lapsed row,
 * or the no-billing sentence on a subscription the server verified.
 */

import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: ({ children }: { children?: React.ReactNode }) => children ?? null }));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => `date(${value})`, number: (value: number) => String(value) })
}));

import { BillingSection } from "../PremiumCenterScreen";
import type { PremiumSubscription } from "../../api/premiumCenter";

const sub = (over: Partial<PremiumSubscription> = {}): PremiumSubscription => ({
  provider: "apple_app_store",
  plan_key: "premium_monthly",
  billing_period: "monthly",
  status: "active",
  current_period_end: "2026-09-24T00:00:00Z",
  cancel_at_period_end: false,
  state: "active",
  auto_renew: true,
  renews_at: "2026-09-24T00:00:00Z",
  expires_at: null,
  product_id: "com.pulsesoc.premium.monthly",
  original_purchase_at: "2025-03-01T00:00:00Z",
  ...over
});

const base = { experience: "active" as const, price: "$9.99", priceLoading: false };

describe("Your subscription — the facts", () => {
  it("never shows the no-billing sentence for a verified subscription", () => {
    const { queryByText } = render(<BillingSection {...base} subscription={sub()} />);
    expect(queryByText("premium:billing.none")).toBeNull();
    expect(queryByText("premium:billing.founderNone")).toBeNull();
  });

  it("names the plan and the period rather than inferring them from a price", () => {
    const { getByLabelText } = render(<BillingSection {...base} subscription={sub()} />);
    expect(getByLabelText("premium:billing.plan: premium:billing.planValue")).toBeTruthy();
  });

  /**
   * A plan key the server could not map to a period still names the product. The
   * alternative — an empty row, or "PulseSoc Premium — " with nothing after the
   * dash — reads as data loss on a membership that is perfectly healthy.
   */
  it("falls back to the bare product name when the period is unknown", () => {
    const { getByLabelText } = render(
      <BillingSection {...base} subscription={sub({ billing_period: "" })} />
    );
    expect(getByLabelText("premium:billing.plan: premium:billing.planValueUnknown")).toBeTruthy();
  });

  it("shows Apple's own localized price string, not a currency we assumed", () => {
    const { getByLabelText } = render(
      <BillingSection {...base} price="€9,99" subscription={sub()} />
    );
    expect(getByLabelText("premium:billing.price: €9,99")).toBeTruthy();
  });

  it("shows a skeleton, never a dash, while StoreKit is still answering", () => {
    const { getByLabelText, queryByLabelText } = render(
      <BillingSection {...base} price={null} priceLoading subscription={sub()} />
    );
    expect(getByLabelText("premium:billing.price: premium:billing.loadingValue")).toBeTruthy();
    expect(queryByLabelText("premium:billing.price: —")).toBeNull();
  });

  it("drops the price row entirely when StoreKit never answers", () => {
    const { queryByLabelText } = render(
      <BillingSection {...base} price={null} priceLoading={false} subscription={sub()} />
    );
    expect(queryByLabelText(/premium:billing\.price/)).toBeNull();
  });

  it("names Apple as the biller so nobody hunts for a card statement", () => {
    const { getByLabelText } = render(<BillingSection {...base} subscription={sub()} />);
    // The mock `t` echoes defaultValue, so this asserts the row is keyed on the
    // provider the server sent. The catalog resolves it to "Apple App Store".
    expect(getByLabelText("premium:billing.provider: apple_app_store")).toBeTruthy();
  });

  it("shows the verified original purchase date as Subscription since", () => {
    const { getByLabelText } = render(<BillingSection {...base} subscription={sub()} />);
    expect(getByLabelText("premium:billing.since: date(2025-03-01T00:00:00Z)")).toBeTruthy();
  });

  /** Omitted, not guessed. An unverifiable date is worse than a missing row. */
  it("omits Subscription since when Apple's payload never carried one", () => {
    const { queryByLabelText } = render(
      <BillingSection {...base} subscription={sub({ original_purchase_at: null })} />
    );
    expect(queryByLabelText(/premium:billing\.since/)).toBeNull();
  });
});

describe("Your subscription — renews versus expires", () => {
  it("says Renews on for an auto-renewing subscription and nothing about expiry", () => {
    const { getByLabelText, queryByLabelText } = render(<BillingSection {...base} subscription={sub()} />);
    expect(getByLabelText("premium:billing.renewsOn: date(2026-09-24T00:00:00Z)")).toBeTruthy();
    expect(queryByLabelText(/premium:billing\.expiresOn/)).toBeNull();
  });

  /**
   * The single most damaging sentence this card could produce: telling a member
   * who has already cancelled that their subscription renews.
   */
  it("says Expires on — never Renews on — once auto-renew is off", () => {
    const { getByLabelText, queryByLabelText } = render(
      <BillingSection
        {...base}
        subscription={sub({
          state: "canceled", auto_renew: false, cancel_at_period_end: true,
          renews_at: null, expires_at: "2026-09-24T00:00:00Z"
        })}
      />
    );
    expect(getByLabelText("premium:billing.expiresOn: date(2026-09-24T00:00:00Z)")).toBeTruthy();
    expect(queryByLabelText(/premium:billing\.renewsOn/)).toBeNull();
  });

  it("never renders both dates, whatever the server sent", () => {
    // A hostile payload with both fields populated: only one row may appear.
    const { queryByLabelText } = render(
      <BillingSection
        {...base}
        subscription={sub({ expires_at: "2026-09-24T00:00:00Z" })}
      />
    );
    const renews = queryByLabelText(/premium:billing\.renewsOn/);
    const expires = queryByLabelText(/premium:billing\.expiresOn/);
    expect(Boolean(renews) && Boolean(expires)).toBe(false);
  });
});

describe("Your subscription — status words", () => {
  it.each([
    ["active" as const],
    ["trialing" as const],
    ["grace" as const],
    ["billing_retry" as const],
    ["canceled" as const],
    ["expired" as const],
    ["revoked" as const],
    ["paused" as const],
    ["unknown" as const]
  ])("renders a word, not only a colour, for %s", (state) => {
    const { getByLabelText } = render(
      <BillingSection {...base} subscription={sub({ state })} />
    );
    // The state's own catalog key is present in the row's spoken label, so the
    // status dot is never the sole carrier of the meaning.
    expect(getByLabelText(`premium:billing.status: premium:subState.${state}`)).toBeTruthy();
  });

  it("never leaks the provider's raw status word into the card", () => {
    const { queryByText } = render(
      <BillingSection {...base} subscription={sub({ status: "DID_FAIL_TO_RENEW_weirdness" })} />
    );
    expect(queryByText(/DID_FAIL_TO_RENEW/)).toBeNull();
  });
});

describe("Your subscription — the explanatory notes", () => {
  it("explains a pending cancellation only when one is pending", () => {
    const cancelled = render(
      <BillingSection {...base} subscription={sub({ state: "canceled", auto_renew: false, renews_at: null, expires_at: "2026-09-24T00:00:00Z" })} />
    );
    expect(cancelled.getByText("premium:billing.cancelPending")).toBeTruthy();

    const active = render(<BillingSection {...base} subscription={sub()} />);
    expect(active.queryByText("premium:billing.cancelPending")).toBeNull();
  });

  it("tells a member in grace that access is still live", () => {
    const { getByText, queryByText } = render(
      <BillingSection {...base} subscription={sub({ state: "grace" })} />
    );
    expect(getByText("premium:billing.graceNote")).toBeTruthy();
    expect(queryByText("premium:billing.retryNote")).toBeNull();
  });

  it("distinguishes a billing retry from grace", () => {
    const { getByText, queryByText } = render(
      <BillingSection {...base} subscription={sub({ state: "billing_retry" })} />
    );
    expect(getByText("premium:billing.retryNote")).toBeTruthy();
    expect(queryByText("premium:billing.graceNote")).toBeNull();
  });
});

describe("Your subscription — no provider row at all", () => {
  it("tells a Founder they hold Premium without a billing record", () => {
    const { getByText, queryByText } = render(
      <BillingSection {...base} experience="founder" subscription={null} />
    );
    expect(getByText("premium:billing.founderNone")).toBeTruthy();
    expect(queryByText("premium:billing.none")).toBeNull();
  });

  it("shows only the verified entitlement status while provider details are delayed", () => {
    const { getByLabelText, queryByText, queryByLabelText } = render(
      <BillingSection {...base} experience="active" subscription={null} />
    );
    expect(getByLabelText("premium:billing.status: premium:subState.active")).toBeTruthy();
    expect(queryByText("premium:billing.none")).toBeNull();
    expect(queryByLabelText(/premium:billing\.(plan|price|provider|renewsOn|expiresOn|since)/)).toBeNull();
  });

  it("renders no subscription card when neither entitlement nor provider facts exist", () => {
    const { toJSON } = render(<BillingSection {...base} experience="none" subscription={null} />);
    expect(toJSON()).toBeNull();
  });
});

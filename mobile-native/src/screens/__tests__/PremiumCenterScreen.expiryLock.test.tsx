/**
 * The Premium Center crypto rows, under every entitlement state.
 *
 * The companion file pins *where the rows go*. This one pins *whether they go
 * at all*, and it deliberately does not stop at the padlock: a lock icon is a
 * drawing, and a row that draws a lock while still calling `navigate` is the
 * exact bug this suite exists to catch. So every locked case asserts the
 * negative — `navigation.navigate` was not called — and every open case asserts
 * the positive. The icon is incidental; the navigation call is the behaviour.
 *
 * The entitlement axis is the same fourteen-case matrix the backend suite in
 * `tests/crypto_premium/test_premium_expiry_crypto_lock.py` walks, expressed as
 * the `TierAnswer` the server would actually return for each. The two halves
 * meet at the same contract: the server refuses the capability, and the row
 * refuses to pretend otherwise.
 *
 * Two cases here are not about locking at all and matter just as much. The
 * portfolio row is free — Premium only lifts a holdings ceiling the server
 * enforces at the add — so locking it would take away something a lapsed member
 * still has. And an *unavailable* answer is not a denial: a member whose
 * resolve failed is not shown a padlock, because a padlock earned by a network
 * error is a lie about their account.
 */

import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

import type { Tier, TierAnswer, TierStatus } from "../../entitlements/canonicalTier";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: ({ children }: { children?: React.ReactNode }) => children ?? null }));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => `date(${value})`, number: (value: number) => String(value) })
}));

// The one seam. `tierSatisfies` stays real — the decision under test is the
// screen's reading of a server answer, not a re-implementation of the ladder.
//
// The `mock` prefix is load-bearing, not a style choice: `jest.mock` is hoisted
// above this declaration, and the factory may only close over variables whose
// names begin with `mock`. Renaming this to `answer` makes the whole suite fail
// to run with "module factory ... not allowed to reference any out-of-scope
// variables" — which reports as a failed suite with zero failed tests.
let mockAnswer: TierAnswer;
jest.mock("../../entitlements/useCanonicalTier", () => ({
  useCanonicalTier: () => mockAnswer,
  loadCanonicalTier: jest.fn(async () => mockAnswer),
  resetCanonicalTier: jest.fn()
}));

import { CryptoIntelligenceSection } from "../PremiumCenterScreen";

const resolved = (
  effectiveTier: Tier,
  status: TierStatus,
  source: string,
  extra: Partial<TierAnswer> = {}
): TierAnswer => ({
  state: "resolved",
  effectiveTier,
  status,
  source,
  expiresAt: null,
  features: {},
  verifiedAt: "2026-09-03T00:00:00Z",
  ...extra
});

/** The fourteen states, as the resolver would describe them. */
const FREE = resolved("FREE", "none", "none");
const TRIAL_ACTIVE = resolved("PREMIUM", "active", "trial");
const TRIAL_EXPIRED = resolved("FREE", "none", "trial_expired");
const PREMIUM_ACTIVE = resolved("PREMIUM", "active", "subscription");
const PREMIUM_CANCELLED_IN_PERIOD = resolved("PREMIUM", "grace", "subscription_cancelled");
const PREMIUM_EXPIRED = resolved("FREE", "none", "subscription_expired");
const PREMIUM_REVOKED = resolved("FREE", "account_hold", "subscription_revoked");
const PRIVATE_ACTIVE = resolved("PRIVATE", "active", "subscription");
const PRIVATE_OFFICE_ACTIVE = resolved("PRIVATE_OFFICE", "active", "subscription");
/**
 * A backend that says FREE while carrying the debris of a past grant. The
 * client has no business reading `source` or a leftover feature flag as a
 * licence: `effectiveTier` is the answer, and it says FREE.
 */
const STALE_CLIENT_CLAIM = resolved("FREE", "none", "legacy_client_premium_flag", {
  features: { "premium.crypto.intelligence": "NOT_ENTITLED" }
});
/** Not a denial. The resolver did not answer, so nothing is claimed either way. */
const UNAVAILABLE: TierAnswer = {
  state: "unavailable",
  effectiveTier: "FREE",
  status: "unavailable",
  source: "",
  expiresAt: null,
  features: {},
  verifiedAt: ""
};

const LOCKING_STATES: [string, TierAnswer][] = [
  ["free account", FREE],
  ["expired trial", TRIAL_EXPIRED],
  ["expired premium", PREMIUM_EXPIRED],
  ["revoked premium", PREMIUM_REVOKED],
  ["stale client premium claim over a FREE backend", STALE_CLIENT_CLAIM]
];

const OPENING_STATES: [string, TierAnswer][] = [
  ["active trial", TRIAL_ACTIVE],
  ["active premium", PREMIUM_ACTIVE],
  ["cancelled premium still inside the paid period", PREMIUM_CANCELLED_IN_PERIOD],
  ["expired premium under an active PRIVATE tier", PRIVATE_ACTIVE],
  ["expired premium under an active PRIVATE_OFFICE tier", PRIVATE_OFFICE_ACTIVE]
];

/** Every row whose capability the server refuses without Premium. */
const PREMIUM_ROWS = ["alerts", "watchlists", "undx", "marketPulse"];

const label = (key: string) => `discovery:crypto.intelligence.${key}.label`;

const mount = () => {
  const navigation = { navigate: jest.fn() };
  const onUpgrade = jest.fn();
  const view = render(
    <CryptoIntelligenceSection navigation={navigation as never} onUpgrade={onUpgrade} />
  );
  return { ...view, navigation, onUpgrade };
};

describe("A lapsed membership closes the premium crypto rows", () => {
  it.each(LOCKING_STATES)("%s: pressing a premium row navigates nowhere", (_name, state) => {
    mockAnswer = state;
    for (const key of PREMIUM_ROWS) {
      const { getByLabelText, navigation, onUpgrade, unmount } = mount();
      fireEvent.press(getByLabelText(label(key)));
      // The assertion that matters: not "a lock rendered", but "the capability
      // was not opened".
      expect(navigation.navigate).not.toHaveBeenCalled();
      // Stage 11: a locked row is not inert. It leads somewhere — the plans and
      // restore actions already on this screen.
      expect(onUpgrade).toHaveBeenCalledTimes(1);
      unmount();
    }
  });

  it.each(LOCKING_STATES)("%s: the section says why, instead of promising access", (_name, state) => {
    mockAnswer = state;
    const { getByText, queryByText, unmount } = mount();
    // "Included with every plan" is true of a plan and false of a lapsed one.
    expect(queryByText("discovery:crypto.intelligence.subhead")).toBeNull();
    expect(getByText("premium:gate.lockedBody")).toBeTruthy();
    unmount();
  });
});

describe("An entitled membership leaves every row open", () => {
  it.each(OPENING_STATES)("%s: premium rows still reach their screens", (_name, state) => {
    mockAnswer = state;
    for (const key of PREMIUM_ROWS) {
      const { getByLabelText, navigation, onUpgrade, unmount } = mount();
      fireEvent.press(getByLabelText(label(key)));
      expect(navigation.navigate).toHaveBeenCalledTimes(1);
      expect(onUpgrade).not.toHaveBeenCalled();
      unmount();
    }
  });

  it.each(OPENING_STATES)("%s: the section keeps its normal subhead", (_name, state) => {
    mockAnswer = state;
    const { getByText, unmount } = mount();
    expect(getByText("discovery:crypto.intelligence.subhead")).toBeTruthy();
    unmount();
  });
});

/**
 * The over-lock guard.
 *
 * `PortfolioScreen` shows free and Premium members the same valuation, the same
 * prices and the same rows; Premium lifts a three-holding ceiling that the
 * server enforces on the add. A padlock here would hide holdings the member
 * entered themselves, so this row must survive every locked state above.
 */
describe("The free row is not collateral damage", () => {
  it.each(LOCKING_STATES)("%s: portfolio still opens", (_name, state) => {
    mockAnswer = state;
    const { getByLabelText, navigation, onUpgrade, unmount } = mount();
    fireEvent.press(getByLabelText(label("portfolio")));
    expect(navigation.navigate).toHaveBeenCalledTimes(1);
    expect(onUpgrade).not.toHaveBeenCalled();
    unmount();
  });
});

/**
 * An unreachable resolver is not a verdict.
 *
 * Locking on `unavailable` would show a paying member a padlock because a
 * request timed out. The rows stay open and the destination's own
 * `PremiumFeatureGate` renders the honest "we couldn't confirm your
 * membership" panel — which is a retry, not an accusation.
 */
describe("An unavailable answer locks nothing", () => {
  it("leaves all five rows navigable", () => {
    mockAnswer = UNAVAILABLE;
    for (const key of [...PREMIUM_ROWS, "portfolio"]) {
      const { getByLabelText, navigation, onUpgrade, unmount } = mount();
      fireEvent.press(getByLabelText(label(key)));
      expect(navigation.navigate).toHaveBeenCalledTimes(1);
      expect(onUpgrade).not.toHaveBeenCalled();
      unmount();
    }
  });
});

/**
 * Reactivation, without a restart.
 *
 * The screen holds no copy of the verdict — it renders whatever the shared
 * resolver last published — so a refresh that turns FREE back into PREMIUM
 * reopens the rows on the next render. Re-rendering the same tree is exactly
 * what the resolver's listener set does when a new answer lands.
 *
 * `rerender` must be handed a *freshly constructed* element. Passing back the
 * identical element object is React's own bail-out signal — it skips the
 * component entirely, the mocked hook is never re-read, and the test fails
 * against a screen that is in fact correct. Same component, same position, new
 * element: React updates in place, so this still proves "no remount".
 */
describe("Reactivation reopens the rows in place", () => {
  it("goes locked → open without remounting the screen", () => {
    mockAnswer = PREMIUM_EXPIRED;
    const navigation = { navigate: jest.fn() };
    const onUpgrade = jest.fn();
    const section = () => (
      <CryptoIntelligenceSection navigation={navigation as never} onUpgrade={onUpgrade} />
    );
    const { getByLabelText, rerender } = render(section());

    fireEvent.press(getByLabelText(label("marketPulse")));
    expect(navigation.navigate).not.toHaveBeenCalled();
    expect(onUpgrade).toHaveBeenCalledTimes(1);

    mockAnswer = PREMIUM_ACTIVE;
    rerender(section());

    fireEvent.press(getByLabelText(label("marketPulse")));
    expect(navigation.navigate).toHaveBeenCalledWith("MarketPulse");
    expect(onUpgrade).toHaveBeenCalledTimes(1);
  });
});

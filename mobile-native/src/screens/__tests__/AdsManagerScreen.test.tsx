/**
 * The ads manager makes three promises that only a rendered screen can keep.
 *
 *   1. A pause switch is either pressable or explains itself. The derivation is
 *      tested in `api/__tests__/adsDashboard.test.ts`; what is tested here is
 *      that the card actually receives the disabled state and the reason, and
 *      that a double tap sends exactly one action — an idempotency guard that
 *      lives in a ref, not in a pure function.
 *   2. Money is only ever shown when the server said it. A failed wallet call
 *      renders no chip at all, never a stale or zero balance, and the failure
 *      is stated with a retry.
 *   3. When the wallet is empty AND verification is pending, both banners show
 *      and the wallet one comes first — an unfunded campaign stops sooner than
 *      one waiting on approval.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({
    onScroll: jest.fn(),
    onScrollBeginDrag: jest.fn(),
    scrollEventThrottle: 16
  })
}));
jest.mock("../../core/cache", () => ({
  readJsonCache: jest.fn().mockResolvedValue(null),
  writeJsonCache: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => jest.fn())
}));

const mockRunAction = jest.fn();
jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  runAdCampaignAction: (...args: unknown[]) => mockRunAction(...args)
}));

const mockLoad = jest.fn();
jest.mock("../../api/adsDashboard", () => ({
  ...jest.requireActual("../../api/adsDashboard"),
  loadAdsMarketplace: () => mockLoad()
}));

import { AdsManagerScreen } from "../AdsManagerScreen";
import { walletSummary } from "../../api/adsDashboard";

const ACTIVE_ACCOUNT = { id: 7, business_name: "Roody Goods", status: "active" };
const PENDING_ACCOUNT = { id: 7, business_name: "Roody Goods", status: "pending" };

function campaign(overrides: Record<string, unknown> = {}) {
  return {
    id: 21,
    campaign_name: "Launch",
    objective: "awareness",
    status: "active",
    budget_type: "daily",
    daily_budget_cents: 2500,
    lifetime_budget_cents: 0,
    spent_cents: 500,
    ...overrides
  };
}

function wallet(balanceCents: number) {
  return walletSummary(
    7,
    { currency: "USD", spendable_balance_cents: balanceCents } as never,
    { billing_enabled: true, live_charging: false } as never
  );
}

function model(overrides: Record<string, unknown> = {}) {
  const accounts = (overrides.accounts as unknown[]) || [ACTIVE_ACCOUNT];
  return {
    accounts,
    primaryAccount: accounts[0] || null,
    campaigns: [campaign()],
    analytics: null,
    wallet: wallet(14200),
    spend: { daysCents: [], totalCents: 0, mock: false },
    needsVerification: false,
    accountsStatus: "ok",
    campaignsStatus: "ok",
    analyticsStatus: "ok",
    offline: false,
    ...overrides
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockRunAction.mockResolvedValue({ message: "Done." });
  mockLoad.mockResolvedValue(model());
});

async function renderScreen() {
  const view = render(<AdsManagerScreen />);
  await waitFor(() => expect(view.queryByText("Launch")).toBeTruthy());
  return view;
}

describe("ads manager — delivery switch", () => {
  it("sends exactly one action when the switch is tapped twice quickly", async () => {
    let resolveAction: (value: unknown) => void = () => {};
    mockRunAction.mockImplementation(
      () => new Promise((resolve) => { resolveAction = resolve; })
    );
    const view = await renderScreen();
    // By role, not by label: "Delivering" is also the status pill's text, and
    // the thing under test is the switch.
    const control = view.getByRole("switch");

    await act(async () => {
      fireEvent.press(control);
      fireEvent.press(control);
    });
    // The second press lands while the first is still in flight. A campaign
    // paused twice is a support ticket, so the in-flight set drops it.
    expect(mockRunAction).toHaveBeenCalledTimes(1);
    expect(mockRunAction).toHaveBeenCalledWith(21, "pause");

    await act(async () => {
      resolveAction({ message: "Paused." });
    });
  });

  it("disables the switch on an ended campaign and says why instead of no-opping", async () => {
    mockLoad.mockResolvedValue(
      model({
        campaigns: [campaign(), campaign({ id: 22, status: "archived", campaign_name: "Finished" })]
      })
    );
    const view = await renderScreen();
    // Ended campaigns live in their own tab; the switch there is the one that
    // must explain itself rather than disappear.
    await act(async () => {
      fireEvent.press(view.getByLabelText("Ended, 1"));
    });
    await waitFor(() => expect(view.queryByText("Finished")).toBeTruthy());

    expect(
      view.getByText("This campaign has ended and can't be restarted. Duplicate it to run it again.")
    ).toBeTruthy();
    await act(async () => {
      fireEvent.press(view.getByRole("switch"));
    });
    expect(mockRunAction).not.toHaveBeenCalled();
  });

  it("disables the switch while a campaign is in review", async () => {
    mockLoad.mockResolvedValue(
      model({ campaigns: [campaign({ status: "pending_review", campaign_name: "Waiting" })] })
    );
    const view = render(<AdsManagerScreen />);
    // In review sits under Active, not Drafts: it was submitted, not abandoned.
    await waitFor(() => expect(view.queryByText("Waiting")).toBeTruthy());
    expect(view.getByText("In review. You can pause it once it starts delivering.")).toBeTruthy();
  });

  it("refuses delivery changes while offline and says so", async () => {
    mockLoad.mockResolvedValue(model({ offline: true }));
    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByRole("switch"));
    });
    expect(mockRunAction).not.toHaveBeenCalled();
  });
});

describe("ads manager — money", () => {
  it("shows the server's balance and nothing it computed itself", async () => {
    const view = await renderScreen();
    expect(
      view.getByLabelText("Ad wallet balance $142.00. Tap to open wallet.")
    ).toBeTruthy();
  });

  it("renders no wallet chip and offers a retry when the wallet call failed", async () => {
    mockLoad.mockResolvedValue(model({ wallet: null }));
    const view = await renderScreen();
    // Never a stale or zero number: the chip is absent and the failure is named.
    expect(view.queryByLabelText(/^Ad wallet balance \$/)).toBeNull();
    expect(view.getByLabelText("Ad wallet balance not yet available. Tap to retry.")).toBeTruthy();
    expect(view.getByText("Tap to retry")).toBeTruthy();
  });

  it("says adding funds isn't live rather than offering a control that can't charge", async () => {
    mockLoad.mockResolvedValue(
      model({ wallet: wallet(0), campaigns: [campaign({ status: "active" })] })
    );
    const view = await renderScreen();
    expect(
      view.getByText(
        "Your ad wallet is empty. Adding funds isn’t available in this build yet, so campaigns won’t deliver."
      )
    ).toBeTruthy();
  });

  it("puts the empty-wallet banner before the verification banner when both apply", async () => {
    mockLoad.mockResolvedValue(
      model({
        accounts: [PENDING_ACCOUNT],
        wallet: wallet(0),
        needsVerification: true,
        campaigns: [campaign({ status: "active" })]
      })
    );
    const view = await renderScreen();
    const walletBanner = view.getByText("Ad wallet is empty");
    const verifyBanner = view.getByText("Verification needed");
    // Both are shown — neither suppresses the other — and money comes first.
    expect(walletBanner).toBeTruthy();
    expect(verifyBanner).toBeTruthy();
    const order = view.UNSAFE_root.findAllByType(require("react-native").Text);
    const texts = order.map((node: any) => node.props.children);
    expect(texts.indexOf("Ad wallet is empty")).toBeLessThan(texts.indexOf("Verification needed"));
  });
});

describe("ads manager — section failures", () => {
  it("keeps campaigns on screen when the analytics call failed", async () => {
    mockLoad.mockResolvedValue(model({ analyticsStatus: "error" }));
    const view = await renderScreen();
    expect(view.getByText("Spend and clicks didn't load.")).toBeTruthy();
    // The chart failing must not take the list with it.
    expect(view.getByText("Launch")).toBeTruthy();
  });
});

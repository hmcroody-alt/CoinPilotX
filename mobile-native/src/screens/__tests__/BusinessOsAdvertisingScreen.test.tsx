/**
 * Advertising is the one Business OS surface that spends money, so the screen
 * carries two claims that a passing API test cannot make on its own:
 *
 *   1. The budget the user types is the budget that reaches the wire. The field
 *      is dollars and the contract is cents, so an off-by-100 here is a hundred-
 *      fold overspend, not a formatting nit.
 *   2. Every control offered is one the backend will accept. `pulse_advertiser_
 *      portal` rejects illegal transitions, so a Resume button on an active
 *      campaign would be a control that can only ever produce an error — which
 *      is exactly what the mission forbids shipping.
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

const mockListAccounts = jest.fn();
const mockListCampaigns = jest.fn();
const mockCreateAccount = jest.fn();
const mockCreateCampaign = jest.fn();
const mockRunAction = jest.fn();
const mockCachedAccounts = jest.fn();
const mockCachedCampaigns = jest.fn();
const mockGetAnalytics = jest.fn();
const mockGetWallet = jest.fn();
const mockGetBilling = jest.fn();
const mockCachedAnalytics = jest.fn();

jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  listAdAccounts: (...args: unknown[]) => mockListAccounts(...args),
  listAdCampaigns: (...args: unknown[]) => mockListCampaigns(...args),
  createAdAccount: (...args: unknown[]) => mockCreateAccount(...args),
  createAdCampaign: (...args: unknown[]) => mockCreateCampaign(...args),
  runAdCampaignAction: (...args: unknown[]) => mockRunAction(...args),
  loadCachedAdAccounts: (...args: unknown[]) => mockCachedAccounts(...args),
  loadCachedAdCampaigns: (...args: unknown[]) => mockCachedCampaigns(...args),
  getAdAnalytics: (...args: unknown[]) => mockGetAnalytics(...args),
  getAdWallet: (...args: unknown[]) => mockGetWallet(...args),
  getAdBillingSummary: (...args: unknown[]) => mockGetBilling(...args),
  loadCachedAdAnalytics: (...args: unknown[]) => mockCachedAnalytics(...args)
}));

/**
 * The screen loads through `loadAdsMarketplace`, which tries the one-request
 * portal first and only falls back to the five-call fan-out that every mock
 * above describes. `getAdsPortal` was not mocked, so each render reached
 * `pulseApi` and made a real HTTPS request to pulsesoc.com — the suite's result
 * depended on how quickly the live site answered, which is why it passed alone
 * and failed under load, and why it fails outright whenever the host is slow.
 *
 * Rejecting here is not a workaround for that flake; it is the state the rest
 * of the file already assumes. Every `beforeEach` line configures a fan-out
 * mock, so the portal path was never the one under test.
 */
const mockPortal = jest.fn();
jest.mock("../../api/adsPortal", () => ({
  ...jest.requireActual("../../api/adsPortal"),
  getAdsPortal: () => mockPortal()
}));

import { BusinessOsAdvertisingScreen } from "../BusinessOsAdvertisingScreen";

const ACCOUNT = { id: 7, business_name: "Roody Goods", status: "active", verified: true };

function campaign(overrides: Record<string, unknown> = {}) {
  return {
    id: 21,
    campaign_name: "Launch",
    objective: "awareness",
    status: "draft",
    budget_type: "daily",
    daily_budget_cents: 2500,
    lifetime_budget_cents: 0,
    spent_cents: 0,
    ...overrides
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPortal.mockRejectedValue(new Error("portal unavailable"));
  mockListAccounts.mockResolvedValue({ accounts: [ACCOUNT] });
  mockListCampaigns.mockResolvedValue({ campaigns: [] });
  mockCreateAccount.mockResolvedValue({ account: ACCOUNT });
  mockCreateCampaign.mockResolvedValue({ campaign: campaign() });
  mockRunAction.mockResolvedValue({ message: "Done." });
  mockCachedAccounts.mockResolvedValue([]);
  mockCachedCampaigns.mockResolvedValue([]);
  // Analytics / wallet / billing degrade to null: money truth only shows when the
  // wallet call itself succeeds, so a null wallet renders no chip (never a zero).
  mockGetAnalytics.mockResolvedValue({ analytics: null });
  mockGetWallet.mockResolvedValue({ wallet: null });
  mockGetBilling.mockResolvedValue({ billing: null });
  mockCachedAnalytics.mockResolvedValue(null);
});

async function renderScreen() {
  const view = render(<BusinessOsAdvertisingScreen />);
  // The first paint waits on a fan-out (accounts, campaigns, analytics, wallet,
  // billing), all of them mocked, so this resolves in a tick or two; the margin
  // is for CPU contention under a full parallel run. It has to stay below Jest's
  // 5s per-test budget — a longer wait here cannot make a test pass, it only
  // converts a legible "still loading" failure into a bare timeout.
  await waitFor(() => expect(view.queryAllByText("Loading advertising…").length).toBe(0), { timeout: 4000 });
  return view;
}

describe("Business OS advertising", () => {
  it("sends the typed dollar budget to the wire in cents", async () => {
    const view = await renderScreen();
    fireEvent.changeText(view.getByLabelText("Campaign name"), "Spring launch");
    fireEvent.changeText(view.getByLabelText("Budget amount in dollars"), "25.50");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Create campaign"));
    });

    expect(mockCreateCampaign).toHaveBeenCalledWith(
      expect.objectContaining({
        ad_account_id: 7,
        campaign_name: "Spring launch",
        budget_type: "daily",
        daily_budget_cents: 2550
      })
    );
  });

  it("sends a lifetime budget under the lifetime key, never the daily one", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("Lifetime budget"));
    fireEvent.changeText(view.getByLabelText("Campaign name"), "Always on");
    fireEvent.changeText(view.getByLabelText("Budget amount in dollars"), "500");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Create campaign"));
    });

    const payload = mockCreateCampaign.mock.calls[0][0];
    expect(payload.lifetime_budget_cents).toBe(50000);
    expect(payload.daily_budget_cents).toBeUndefined();
  });

  it("refuses to create a nameless campaign instead of posting an empty one", async () => {
    const view = await renderScreen();
    fireEvent.changeText(view.getByLabelText("Budget amount in dollars"), "10");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Create campaign"));
    });
    expect(mockCreateCampaign).not.toHaveBeenCalled();
    expect(view.getByText("Campaign name is required.")).toBeTruthy();
  });

  it("offers only the transitions the backend accepts for a campaign's status", async () => {
    mockListCampaigns.mockResolvedValue({ campaigns: [campaign({ status: "active", campaign_name: "Running" })] });
    const view = await renderScreen();

    // Pause/resume is the delivery switch, not a button; an active campaign
    // exposes exactly one switch. Archive stays a comma-labelled secondary action.
    expect(view.getByRole("switch")).toBeTruthy();
    expect(view.getByLabelText("Archive, Running")).toBeTruthy();
    // An active campaign cannot be resumed or submitted; the portal rejects both.
    expect(view.queryByLabelText("Resume, Running")).toBeNull();
    expect(view.queryByLabelText("Submit for review, Running")).toBeNull();
  });

  it("leaves an archived campaign with only the action that still makes sense", async () => {
    mockListCampaigns.mockResolvedValue({ campaigns: [campaign({ status: "archived", campaign_name: "Old" })] });
    const view = await renderScreen();
    expect(view.getByLabelText("Duplicate, Old")).toBeTruthy();
    // Archived is terminal: no delivery switch, and nothing to pause or resume.
    expect(view.queryByLabelText("Delivering")).toBeNull();
    expect(view.queryByLabelText("Paused")).toBeNull();
    expect(view.queryByLabelText("Archive, Old")).toBeNull();
  });

  it("runs the action the switch names against the campaign it belongs to", async () => {
    mockListCampaigns.mockResolvedValue({ campaigns: [campaign({ id: 33, status: "active", campaign_name: "Running" })] });
    const view = await renderScreen();
    await act(async () => {
      // Toggling the delivery switch off is a pause on the owning campaign.
      fireEvent.press(view.getByRole("switch"));
    });
    expect(mockRunAction).toHaveBeenCalledWith(33, "pause");
  });

  it("disables every write control while offline rather than failing on tap", async () => {
    mockListAccounts.mockRejectedValue(new Error("Network request failed"));
    mockListCampaigns.mockRejectedValue(new Error("Network request failed"));
    mockCachedAccounts.mockResolvedValue([ACCOUNT]);
    mockCachedCampaigns.mockResolvedValue([campaign({ status: "active", campaign_name: "Running" })]);

    const view = await renderScreen();
    expect(view.getByText("Showing saved data")).toBeTruthy();

    await act(async () => {
      fireEvent.press(view.getByLabelText("Create campaign"));
    });
    await act(async () => {
      fireEvent.press(view.getByRole("switch"));
    });
    expect(mockCreateCampaign).not.toHaveBeenCalled();
    expect(mockRunAction).not.toHaveBeenCalled();
  });

  it("says plainly that a new ad account cannot deliver until it is approved", async () => {
    mockListAccounts.mockResolvedValue({ accounts: [] });
    const view = await renderScreen();
    expect(view.getByText(/cannot\s+deliver campaigns until they are approved/i)).toBeTruthy();

    fireEvent.changeText(view.getByLabelText("Business name"), "Roody Goods");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Create ad account"));
    });
    expect(mockCreateAccount).toHaveBeenCalledWith(expect.objectContaining({ business_name: "Roody Goods" }));
  });

  it("surfaces the backend's own rejection rather than a generic failure", async () => {
    mockCreateCampaign.mockRejectedValue(new Error("Daily budget exceeds the account limit."));
    const view = await renderScreen();
    fireEvent.changeText(view.getByLabelText("Campaign name"), "Too big");
    fireEvent.changeText(view.getByLabelText("Budget amount in dollars"), "999999");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Create campaign"));
    });
    expect(view.getByText("Daily budget exceeds the account limit.")).toBeTruthy();
  });
});

/**
 * The Business Profile screen makes three promises that are easy to break and
 * invisible in a screenshot, so they are pinned here.
 *
 * 1. It never invents a number. Rating, on-time rate, opening hours, reply time,
 *    profile views and follower deltas have no endpoint behind them, and the
 *    registry that routes this screen is explicit that coverage "reflects
 *    verified live coverage, not aspiration". A future refactor that quietly
 *    fills these in with a plausible default would ship a lie about a seller.
 * 2. Reduce-motion is honoured by stopping the animation, not by hiding the
 *    content. Under the OS setting the ticker must still be readable and the
 *    completeness ring must still report its real value.
 * 3. Save is gated on real dirt and goes through the existing draft endpoint,
 *    which is the one that enforces the writable-field whitelist server-side.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

const mockReducedMotion = jest.fn(() => false);

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: "LinearGradient" }));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../theme/logiNexusMotion", () => ({
  ...jest.requireActual("../../theme/logiNexusMotion"),
  // The real hook reads AccessibilityInfo, which the test environment cannot
  // toggle. Swapping the hook lets both branches be exercised for real.
  useLogiNexusReducedMotion: () => mockReducedMotion()
}));

const mockLoadApplication = jest.fn();
const mockCachedApplication = jest.fn();
const mockSaveDraft = jest.fn();
jest.mock("../../api/sellerApplication", () => ({
  ...jest.requireActual("../../api/sellerApplication"),
  loadSellerApplication: (...args: unknown[]) => mockLoadApplication(...args),
  loadCachedSellerApplication: (...args: unknown[]) => mockCachedApplication(...args),
  saveSellerApplicationDraft: (...args: unknown[]) => mockSaveDraft(...args)
}));

const mockGetProfile = jest.fn();
const mockCachedProfile = jest.fn();
jest.mock("../../api/profile", () => ({
  getMyProfile: (...args: unknown[]) => mockGetProfile(...args),
  loadCachedProfile: (...args: unknown[]) => mockCachedProfile(...args)
}));

const mockVerification = jest.fn();
const mockCachedVerification = jest.fn();
jest.mock("../../api/verification", () => ({
  ...jest.requireActual("../../api/verification"),
  loadVerificationState: (...args: unknown[]) => mockVerification(...args),
  loadCachedVerificationState: (...args: unknown[]) => mockCachedVerification(...args)
}));

const mockStore = jest.fn();
const mockCachedStore = jest.fn();
jest.mock("../../api/marketplace", () => ({
  loadSellerStoreSnapshot: (...args: unknown[]) => mockStore(...args),
  loadCachedSellerStore: (...args: unknown[]) => mockCachedStore(...args)
}));

import { emptySellerApplication } from "../../api/sellerApplication";
import { BusinessProfileScreen } from "../BusinessProfileScreen";

function application(overrides: Record<string, unknown> = {}) {
  return {
    ...emptySellerApplication(),
    completeness: 62,
    editable: true,
    submitted_at: "2024-03-04T10:00:00Z",
    fields: { business_name: "Harbour Goods", email: "hi@harbour.co", country: "Ireland", state_region: "Cork" },
    seller_types: [{ key: "retail", label: "Retail" }],
    steps: [{ key: "brand", title: "Brand", summary: "Add a logo so buyers recognise you.", fields: [], complete: false, errors: {} }],
    ...overrides
  };
}

function navigationSpy() {
  return { navigate: jest.fn(), goBack: jest.fn() };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockReducedMotion.mockReturnValue(false);
  mockLoadApplication.mockResolvedValue(application());
  mockCachedApplication.mockResolvedValue(null);
  mockSaveDraft.mockImplementation(async (fields) => application({ fields }));
  mockGetProfile.mockResolvedValue({ user_id: 7, username: "harbour", display_name: "Harbour", follower_count: 2400 });
  mockCachedProfile.mockResolvedValue(null);
  mockVerification.mockResolvedValue({ status: "not_started" });
  mockCachedVerification.mockResolvedValue(null);
  mockStore.mockResolvedValue({ listings: [], orders: [] });
  mockCachedStore.mockResolvedValue(null);
});

async function renderScreen(navigation = navigationSpy()) {
  const view = render(<BusinessProfileScreen navigation={navigation} />);
  await waitFor(() => expect(mockLoadApplication).toHaveBeenCalled());
  await act(async () => undefined);
  return { ...view, navigation };
}

describe("Business Profile", () => {
  it("shows the server's completeness rather than a decorative default", async () => {
    const view = await renderScreen();
    expect(view.getByLabelText("Profile completeness")).toBeTruthy();
    expect(view.getByText("62")).toBeTruthy();
  });

  it("names one specific next step taken from the application's own steps", async () => {
    const view = await renderScreen();
    expect(view.getByText("Add a logo so buyers recognise you.")).toBeTruthy();
  });

  it("marks every unbacked metric as untracked instead of inventing a value", async () => {
    const view = await renderScreen();
    // The ticker exposes its content once, as a spoken summary, so the moving
    // copies are not read twice. That summary is where the placeholders live.
    const ticker = view.getByLabelText(/Profile complete: 62%/);
    const spoken = String(ticker.props.accessibilityLabel);
    ["Profile views today", "New followers", "Avg reply time", "Store clicks", "Next ship day"].forEach((label) => {
      expect(spoken).toContain(`${label}: Not tracked yet`);
    });
    // Rating and on-time rate are equally unbacked in the buyer preview.
    expect(view.getByLabelText("Rating: —")).toBeTruthy();
    expect(view.getByLabelText("On time: —")).toBeTruthy();
  });

  it("explains what an empty field costs the seller rather than just saying it is blank", async () => {
    const view = await renderScreen();
    expect(view.getByLabelText(/Opening hours\. Not set\. buyers can't see when you're open/)).toBeTruthy();
    expect(view.getByLabelText(/Links\. Not set\. buyers can't check you out elsewhere/)).toBeTruthy();
  });

  it("keeps the ticker readable and the ring truthful under reduce-motion", async () => {
    mockReducedMotion.mockReturnValue(true);
    const view = await renderScreen();
    // Content is still present and still announces the real figures — the
    // setting suppresses movement, not information.
    expect(view.getByLabelText(/Profile complete: 62%/)).toBeTruthy();
    expect(view.getByText("62")).toBeTruthy();
  });

  it("leaves Save inert until something is actually edited, then saves through the draft endpoint", async () => {
    const view = await renderScreen();
    const save = view.getByLabelText("Save changes");
    expect(save.props.accessibilityState.disabled).toBe(true);

    fireEvent.press(view.getByLabelText("Business name. Harbour Goods"));
    fireEvent.changeText(view.getByLabelText("Business name"), "Harbour Goods Ltd");
    await act(async () => undefined);

    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(false);
    await act(async () => {
      fireEvent.press(view.getByLabelText("Save changes"));
    });
    expect(mockSaveDraft).toHaveBeenCalledWith(expect.objectContaining({ business_name: "Harbour Goods Ltd" }));
    // A completed save is no longer dirty, so the control goes quiet again.
    await waitFor(() => expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(true));
  });

  it("treats an edit back to the stored value as no edit at all", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("Business name. Harbour Goods"));
    const input = view.getByLabelText("Business name");
    fireEvent.changeText(input, "Something else");
    await act(async () => undefined);
    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(false);

    fireEvent.changeText(input, "Harbour Goods");
    await act(async () => undefined);
    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(true);
  });

  it("does not offer inline editing while the application is locked for review", async () => {
    mockLoadApplication.mockResolvedValue(application({ editable: false, status: "under_review" }));
    const view = await renderScreen();
    // The row is still there and still readable; it just sends the operator to
    // the application screen rather than pretending a draft write would land.
    fireEvent.press(view.getByLabelText("Business name. Harbour Goods"));
    expect(view.navigation.navigate).toHaveBeenCalledWith("MerchantApply");
    expect(view.queryByLabelText("Business name")).toBeNull();
  });

  it("sends the trust callout to the business verification track", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("Check status"));
    expect(view.navigation.navigate).toHaveBeenCalledWith("VerificationCenter", { track: "business" });
  });

  it("opens the public profile for 'View as buyer' rather than another seller tool", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("View as buyer"));
    expect(view.navigation.navigate).toHaveBeenCalledWith(
      "ProfileDetail",
      expect.objectContaining({ username: "harbour", source: "business_profile_preview" })
    );
  });

  it("falls back to cached data and says so when PulseSoc cannot be reached", async () => {
    mockLoadApplication.mockRejectedValue(new Error("Network request failed"));
    mockGetProfile.mockRejectedValue(new Error("Network request failed"));
    mockCachedApplication.mockResolvedValue(application({ completeness: 41 }));

    const view = await renderScreen();
    expect(view.getByText("Showing your last saved profile. Reconnect to sync.")).toBeTruthy();
    // The cached completeness is what is shown — not a zero that would tell a
    // seller their profile is empty when it is not.
    expect(view.getByText("41")).toBeTruthy();
  });

  it("counts only publicly purchasable listings as active", async () => {
    mockStore.mockResolvedValue({
      listings: [
        { id: 1, status: "active", approval_status: "approved" },
        { id: 2, status: "paused", approval_status: "approved" },
        { id: 3, status: "active", approval_status: "pending" }
      ],
      orders: []
    });
    const view = await renderScreen();
    expect(view.getByLabelText("Marketplace. 1 active")).toBeTruthy();
    expect(view.getByLabelText("Store. 3 listings")).toBeTruthy();
  });
});

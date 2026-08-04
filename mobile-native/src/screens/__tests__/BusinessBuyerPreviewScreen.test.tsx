/**
 * The buyer preview makes two promises that a screenshot cannot verify, so they are
 * pinned here.
 *
 * 1. **It cannot show the owner anything a buyer would not see.** The screen is fed
 *    only by `GET /api/pulse/business/profile/preview`, which the server assembles
 *    from a public allowlist. The tests below hand the client a payload that also
 *    carries `legal_name`, a payout account, the completion breakdown and the
 *    verification internals — the fields the old `ProfileDetail` route did render —
 *    and assert that none of them reach the screen. The type system already forbids
 *    this; the test is what catches the day someone reaches for `any`.
 *
 * 2. **Owner-unsafe actions are refused out loud, not silently ignored.** Message,
 *    Follow, Share and Report are rendered because hiding them would misrepresent the
 *    buyer's layout, and refused because performing them would have the owner
 *    following themselves. A button that looks live and quietly does nothing teaches
 *    the owner the wrong thing about their own shop, so the refusal has to be visible.
 *
 * The same screen serves a real buyer visiting a real shop (`sellerUserId` set). That
 * path must *not* refuse — a buyer whose "Message business" button did nothing would
 * be the mirror-image defect — so it is exercised too.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));

const mockPulseApi = jest.fn();
jest.mock("../../api/pulseApi", () => ({
  ...jest.requireActual("../../api/pulseApi"),
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { BusinessBuyerPreviewScreen } from "../BusinessBuyerPreviewScreen";

/**
 * A response shaped like the server's, plus five fields the server would never send.
 *
 * They are here on purpose. If the screen or the client ever starts passing unknown
 * keys through — the usual way a leak is introduced, by spreading the payload into
 * state instead of normalising it — these are what will surface, and the assertions
 * below are what will notice.
 */
function previewPayload(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    profile: {
      handle: "@harbour",
      business_name: "Harbour Goods",
      business_category: "retail",
      business_category_label: "General retail",
      verified: false,
      tagline: "Coastal homeware",
      about: "We make things for kitchens.",
      what_you_sell: "Homeware",
      location: "Cork, Ireland",
      member_since: "2023",
      contact: { preferred: "message" },
      languages: [],
      accessibility: [],
      hours_mode: "unset",
      hours: [],
      hours_overrides: [],
      links: [],
      policies: {},
      // --- none of the following may reach the screen ---
      legal_name: "Harbour Goods Trading Limited",
      payout_account: "IE29AIBK93115212345678",
      completion: { percent: 62, missing: [{ key: "logo", label: "Business logo" }] },
      verification: { state: "under_review", source: "seller_application", note: "Awaiting utility bill" },
      addresses: [{ kind: "pickup", line1: "12 Quay Street" }],
      ...((overrides.profile as Record<string, unknown>) || {})
    },
    preview: {
      active: true,
      title: "Buyer preview",
      subtitle: "This is how your public business profile appears.",
      exit_label: "Exit preview",
      simulated_actions: ["message", "follow", "buy", "share", "report"]
    },
    ...overrides
  };
}

function navigationSpy() {
  return { navigate: jest.fn(), goBack: jest.fn() };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPulseApi.mockResolvedValue(previewPayload());
});

async function renderScreen(params?: { sellerUserId?: number }) {
  const navigation = navigationSpy();
  const view = render(
    <BusinessBuyerPreviewScreen navigation={navigation} route={{ params: params || {} }} />
  );
  await waitFor(() => expect(mockPulseApi).toHaveBeenCalled());
  await act(async () => undefined);
  return { ...view, navigation };
}

describe("Buyer preview", () => {
  it("reads the preview endpoint, not the owner profile endpoint", async () => {
    await renderScreen();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/business/profile/preview");
    // The owner route returns locks, completion and legal name. Calling it here would
    // put all three one `JSON.stringify` away from the screen.
    expect(mockPulseApi).not.toHaveBeenCalledWith("/api/pulse/business/profile");
  });

  it("renders the public fields", async () => {
    const view = await renderScreen();
    expect(view.getByText("Harbour Goods")).toBeTruthy();
    expect(view.getByText("Coastal homeware")).toBeTruthy();
  });

  it("shows none of the owner-only values, even when the payload carries them", async () => {
    const view = await renderScreen();
    const rendered = JSON.stringify(view.toJSON());
    [
      "Harbour Goods Trading Limited", // legal name
      "IE29AIBK93115212345678", // payout account
      "Awaiting utility bill", // reviewer's private note
      "12 Quay Street" // operational address
    ].forEach((secret) => {
      expect(rendered).not.toContain(secret);
    });
    // The completeness figure is owner-only too, and "62" is short enough to appear
    // by accident, so it is asserted through the label a buyer would never see.
    expect(view.queryByLabelText(/completeness/i)).toBeNull();
  });

  it("refuses the owner-unsafe actions out loud instead of performing them", async () => {
    const view = await renderScreen();
    // The disabled state is in the label, not only in the styling — a screen reader
    // announcing a plain "Message business" would send a blind owner into a control
    // that is going to refuse them.
    const message = view.getByLabelText("Message business (preview mode — disabled)");
    expect(message.props.accessibilityState.disabled).toBe(true);

    fireEvent.press(message);
    await act(async () => undefined);
    expect(view.getByText(/Preview mode — messaging is disabled/)).toBeTruthy();
    // Refused means refused: no navigation, not a navigation that happens to fail.
    expect(view.navigation.navigate).not.toHaveBeenCalled();
  });

  it("refuses Follow without following the owner to themselves", async () => {
    const view = await renderScreen();
    fireEvent.press(view.getByLabelText("Follow (preview mode — disabled)"));
    await act(async () => undefined);
    expect(view.getByText(/Preview mode — following is disabled/)).toBeTruthy();
  });

  it("performs the same actions for real when a buyer opens a real shop", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, profile: previewPayload().profile, is_self: false });
    const view = await renderScreen({ sellerUserId: 4242 });
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/business/profile/4242");
    fireEvent.press(view.getByLabelText("Message business"));
    await act(async () => undefined);
    expect(view.navigation.navigate).toHaveBeenCalledWith("Messenger", { sellerUserId: 4242 });
    expect(view.queryByText(/Preview mode/)).toBeNull();
  });

  it("shows the banner only in preview, so a buyer is never told they are previewing", async () => {
    const owner = await renderScreen();
    expect(owner.getByText("Buyer preview")).toBeTruthy();

    mockPulseApi.mockResolvedValue({ ok: true, profile: previewPayload().profile, is_self: false });
    const buyer = await renderScreen({ sellerUserId: 4242 });
    expect(buyer.queryByText("Buyer preview")).toBeNull();
  });

  it("offers a retry rather than a dead screen when the fetch fails", async () => {
    mockPulseApi.mockRejectedValue(new Error("Network request failed"));
    const view = await renderScreen();
    const retry = view.getByLabelText(/try again/i);
    expect(retry).toBeTruthy();
    mockPulseApi.mockResolvedValue(previewPayload());
    await act(async () => {
      fireEvent.press(retry);
    });
    await waitFor(() => expect(view.getByText("Harbour Goods")).toBeTruthy());
  });
});

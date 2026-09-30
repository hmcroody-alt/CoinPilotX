/**
 * A seller may only be shown the commission the server will actually charge.
 *
 * This screen used to state, in the client, "Current Marketplace terms remain
 * 10%; the proposed 5% policy is not active" and "Seller Terms · 10% current
 * platform fee" — immediately above a Review-and-Accept button. Both numbers
 * were wrong in the same way: `services/business_os/marketplace/policy.py` is
 * the only fee authority, it charges 0 until three owner gates open and
 * `PROPOSED_PLATFORM_FEE_BPS` after, and 10% is not one of its values. It came
 * from a dead `platform_fee_rules` row no seller was ever charged.
 *
 * So the assertions here are about *where the number comes from*, not what it
 * is. A test pinning "0.00%" would have to be rewritten on the day the owner
 * activates the policy — the day it matters most — and would pass just as
 * happily against a second hardcoded literal. These render the same screen
 * against two different server answers and require the copy to move:
 *
 * * the rate shown is whatever `/api/pulse/marketplace/commercial/terms`
 *   disclosed, including when that is zero;
 * * a failed read names no rate at all, because "0%" read off a dropped request
 *   is a commission quote the platform never made.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react-native";

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
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../components/NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn(() => null)
}));

const mockSnapshot = jest.fn();
const mockCommercialTerms = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  loadSellerStoreSnapshot: (...args: unknown[]) => mockSnapshot(...args),
  getMarketplaceCommercialTerms: (...args: unknown[]) => mockCommercialTerms(...args),
  loadCachedSellerStore: jest.fn().mockResolvedValue(null)
}));

import { SellerStoreScreen } from "../SellerStoreScreen";
import { activateLocale } from "../../i18n/engine";

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  mockSnapshot.mockResolvedValue({ live: true, listings: [], orders: [] });
});

/** Renders the Orders panel, which is the only panel that quotes the rate. */
async function renderOrders() {
  const view = render(
    <SellerStoreScreen route={{ params: { mode: "orders" } as never }} navigation={{ navigate: jest.fn() }} />
  );
  await waitFor(() => expect(mockCommercialTerms).toHaveBeenCalled());
  await waitFor(() => expect(view.getByText("Orders and payouts")).toBeTruthy());
  return view;
}

/** Every percentage the rendered tree states, in source order. */
function quotedRates(view: ReturnType<typeof render>): string[] {
  return (JSON.stringify(view.toJSON() || "").match(/\d+\.\d\d%/g) || []).sort();
}

describe("seller commission disclosure", () => {
  it.each([
    ["the zero-fee lane in force today", 0, "0.00%"],
    ["the proposed rate, once the owner activates it", 500, "5.00%"],
    // Not a rate anyone plans to charge. It is here because a screen that
    // renders 0 and 500 correctly could still be switching on them; this one
    // has never appeared in this codebase, so only a value read straight from
    // the response can produce it.
    ["a rate this client has no knowledge of", 275, "2.75%"]
  ])("states %s exactly as the server disclosed it", async (_case, bps, shown) => {
    mockCommercialTerms.mockResolvedValue({ terms: { acceptance: null, current: { platform_fee_bps: bps } } });
    const view = await renderOrders();
    await waitFor(() => expect(quotedRates(view)).toContain(shown));
    // And nothing else. A second, different percentage next to this one would
    // mean the seller is reading two commission rates and choosing.
    expect(new Set(quotedRates(view))).toEqual(new Set([shown]));
    view.unmount();
  });

  it("never states the rate the dead admin row held", async () => {
    mockCommercialTerms.mockResolvedValue({ terms: { acceptance: null, current: { platform_fee_bps: 0 } } });
    const view = await renderOrders();
    const tree = JSON.stringify(view.toJSON() || "");
    // The literal this screen shipped with. Spelled both ways because the two
    // lines it appeared on were punctuated differently.
    expect(tree).not.toContain("10%");
    expect(tree).not.toContain("10.00%");
    view.unmount();
  });

  it.each([
    ["the request failed", () => Promise.reject(new Error("offline"))],
    ["the response carried no rate", () => Promise.resolve({ terms: { acceptance: null } })],
    ["the rate was not a number", () => Promise.resolve({ terms: { acceptance: null, current: { platform_fee_bps: null } } })]
  ])("quotes no commission at all when %s", async (_case, answer) => {
    mockCommercialTerms.mockImplementation(answer);
    const view = await renderOrders();
    // Specifically not "0.00%". The platform has not quoted a rate here, and a
    // seller accepting terms beneath a zero it never offered has been misled by
    // a dropped request.
    expect(quotedRates(view)).toEqual([]);
    expect(view.getByText(/commission could not be loaded/i)).toBeTruthy();
    view.unmount();
  });
});

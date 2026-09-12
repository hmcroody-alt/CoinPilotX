/**
 * The merchant's own listing rows must say whether buyers can actually buy.
 *
 * `publication_state` is `marketplace_listings.status` lowercased — one column
 * out of the five conditions `marketplace_listing_lifecycle` requires before a
 * buyer can reach a listing. This screen derived its status pill from that
 * column, so a suspended seller, a storefront that was never named, an empty
 * shelf and a perfectly healthy listing all produced the same chip.
 *
 * Worse, they produced the *neutral* chip. `published` was missing from the
 * screen's own list of live-ish values, so it fell through every branch and was
 * returned unchanged — meaning the live pill never rendered for the value
 * `drafts.publish` actually writes. A merchant looking at their store had no
 * way to tell a selling listing from a dead one.
 *
 * The server now answers the question once, from the table that filters buyer
 * discovery, and ships the result as `publication_blocker`. These tests assert
 * the screen reads it rather than re-deriving, because re-deriving is what
 * produced the wrong answer four times over.
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

/** The shape the server sends for a listing that went live cleanly. */
const LIVE = {
  id: 51,
  title: "Healthy listing",
  description: "A listing",
  category: "Home",
  price_label: "$25.00",
  quantity: 3,
  status: "published",
  approval_status: "approved",
  publication_state: "published",
  publication_label: "Live",
  publication_blocker: ""
};

function listing(overrides: Record<string, unknown>) {
  return { ...LIVE, ...overrides };
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  mockCommercialTerms.mockResolvedValue({ terms: { acceptance: null } });
});

async function renderWith(listings: Record<string, unknown>[]) {
  // `live: true` is load-bearing: the screen keeps its cached/offline copy
  // unless the snapshot says it reached the server, so without it none of these
  // listings reach the tree and every assertion below passes vacuously.
  mockSnapshot.mockResolvedValue({ live: true, listings, orders: [] });
  const view = render(
    <SellerStoreScreen route={{ params: { mode: "dashboard" } as never }} navigation={{ navigate: jest.fn() }} />
  );
  await waitFor(() => expect(mockSnapshot).toHaveBeenCalled());
  await waitFor(() => expect(mockCommercialTerms).toHaveBeenCalled());
  return view;
}

describe("seller store publication pill", () => {
  it("marks a genuinely live listing as live", async () => {
    const view = await renderWith([listing({})]);
    // The control. Every negative assertion below is satisfied by a screen that
    // simply never says "Live", so this is the one that makes them mean
    // something.
    expect(view.queryAllByText(/^Live$/).length).toBeGreaterThan(0);
    view.unmount();
  });

  it("does not call a published listing live when the seller is suspended", async () => {
    const view = await renderWith([listing({ publication_blocker: "seller_approved" })]);
    expect(view.queryAllByText(/^Live$/).length).toBe(0);
    expect(view.queryAllByText(/Store offline/i).length).toBeGreaterThan(0);
    view.unmount();
  });

  it("names the missing storefront rather than showing a bare status", async () => {
    const view = await renderWith([listing({ publication_blocker: "seller_named" })]);
    expect(view.queryAllByText(/^Live$/).length).toBe(0);
    expect(view.queryAllByText(/Store name needed/i).length).toBeGreaterThan(0);
    view.unmount();
  });

  it("shows out of stock for an approved listing with an empty shelf", async () => {
    // This pill existed and was unreachable: the screen looked for "stock" in a
    // column that only ever holds a listing status, so no payload could produce
    // it. A branch nothing can satisfy is not a feature.
    const view = await renderWith([listing({ quantity: 0, publication_blocker: "in_stock" })]);
    expect(view.queryAllByText(/^Live$/).length).toBe(0);
    expect(view.queryAllByText(/Out of stock/i).length).toBeGreaterThan(0);
    view.unmount();
  });

  it("does not re-derive publication from the status column", async () => {
    // Same `publication_state` on every row, three different answers. A screen
    // reading the column would have to render the same chip three times.
    const view = await renderWith([
      listing({ id: 61, title: "Row A", publication_blocker: "" }),
      listing({ id: 62, title: "Row B", publication_blocker: "in_stock" }),
      listing({ id: 63, title: "Row C", publication_blocker: "seller_approved" })
    ]);
    expect(view.queryAllByText(/^Live$/).length).toBeGreaterThan(0);
    expect(view.queryAllByText(/Out of stock/i).length).toBeGreaterThan(0);
    expect(view.queryAllByText(/Store offline/i).length).toBeGreaterThan(0);
    view.unmount();
  });

  it("leaves a listing the merchant never published described by its own state", async () => {
    // A draft is not "out of stock" even with quantity 0. Running every row
    // through the publication rules would replace the state the merchant needs
    // to act on with a true but useless one.
    const view = await renderWith([
      listing({ status: "draft", approval_status: "pending_review", publication_state: "draft",
                quantity: 0, publication_label: "Draft", publication_blocker: "" })
    ]);
    expect(view.queryAllByText(/Out of stock/i).length).toBe(0);
    expect(view.queryAllByText(/^Live$/).length).toBe(0);
    expect(view.queryAllByText(/^Draft$/i).length).toBeGreaterThan(0);
    view.unmount();
  });

  it("falls back to the status column when the server sends no blocker", async () => {
    // Older builds, cached payloads and any endpoint that has not been updated
    // send no `publication_blocker` at all. Absence must not read as a blocker,
    // and a published listing must still reach the live pill.
    const stale = { ...LIVE };
    delete (stale as Record<string, unknown>).publication_blocker;
    const view = await renderWith([stale]);
    expect(view.queryAllByText(/^Live$/).length).toBeGreaterThan(0);
    view.unmount();
  });
});

/**
 * Tier 0.4 at the surfaces a person actually touches.
 *
 * The data-layer separation is pinned in `api/__tests__/messengerDomainSplit`.
 * What is left, and what this file covers, is the part of the review that can
 * only be broken in a screen:
 *
 *   1. The Commerce Inbox rail is the review's rail — Marketplace / Store support
 *      / Orders / Returns / Disputes, plus the two chips that were already
 *      carrying weight — and it swaps back cleanly when the flag is off.
 *   2. Returns ships PRESENT and EMPTY. Nothing in the app can create a return,
 *      so the filter says so instead of being hidden, which would have made a
 *      missing feature look like a finished one.
 *   3. "Contact Seller" lands in the Commerce Inbox, not in the friends list.
 *      With the flag off it keeps its old destination exactly.
 */

import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

import { FilterChips } from "../../components/messages/FilterChips";
import { MessagesFilterEmpty } from "../../components/messages/MessagesStates";
import { filterCounts, InboxRow } from "../../api/commerceInbox";

const EMPTY_COUNTS = filterCounts([]);

afterEach(() => {
  delete process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT;
});

describe("the commerce triage rail", () => {
  it("shows the review's rail when the split is on", () => {
    process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT = "1";
    const { getByText, queryByText } = render(
      <FilterChips active="all" counts={EMPTY_COUNTS} onChange={() => undefined} />
    );
    ["All", "Unread", "Marketplace", "Store support", "Orders", "Returns", "Disputes"].forEach((label) => {
      expect(getByText(label)).toBeTruthy();
    });
    // The pre-0.4 chips are gone, not merely reordered.
    expect(queryByText("Offers")).toBeNull();
    expect(queryByText("Starred")).toBeNull();
    expect(queryByText("Archived")).toBeNull();
  });

  it("leaves the pre-0.4 rail untouched while the flag is off", () => {
    const { getByText, queryByText } = render(
      <FilterChips active="all" counts={EMPTY_COUNTS} onChange={() => undefined} />
    );
    ["All", "Unread", "Offers", "Orders", "Starred", "Archived"].forEach((label) => {
      expect(getByText(label)).toBeTruthy();
    });
    expect(queryByText("Marketplace")).toBeNull();
    expect(queryByText("Disputes")).toBeNull();
  });

  it("reports the filter that was tapped", () => {
    process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT = "1";
    const onChange = jest.fn();
    const { getByText } = render(
      <FilterChips active="all" counts={EMPTY_COUNTS} onChange={onChange} />
    );
    fireEvent.press(getByText("Disputes"));
    expect(onChange).toHaveBeenCalledWith("disputes");
  });
});

describe("Returns ships present and honestly empty", () => {
  it("says returns cannot be started rather than pretending there are none to show", () => {
    const { getByText } = render(<MessagesFilterEmpty filter="returns" />);
    expect(getByText("No returns yet")).toBeTruthy();
    expect(getByText(/not something you can start in the app yet/i)).toBeTruthy();
  });

  it("counts zero for returns no matter what is in the inbox", () => {
    const rows: InboxRow[] = [
      {
        id: 1,
        domain: "MARKETPLACE",
        title: "Dana",
        colorKey: "1",
        snippet: "refund?",
        ownLast: false,
        unreadCount: 1,
        typing: false,
        starred: false,
        archived: false,
        spam: false,
        blocked: false
      }
    ];
    expect(filterCounts(rows).returns).toBe(0);
  });
});

/* ------------------------------------------------------------------ *
 * Commerce entry point routing
 * ------------------------------------------------------------------ */

const mockStartSellerChat = jest.fn();
const mockSearchMarketplace = jest.fn();
const mockLoadCachedMarketplace = jest.fn();

jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  searchMarketplace: (...args: unknown[]) => mockSearchMarketplace(...args),
  loadCachedMarketplace: (...args: unknown[]) => mockLoadCachedMarketplace(...args),
  startMarketplaceSellerChat: (...args: unknown[]) => mockStartSellerChat(...args)
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({ handlers: {}, contentPadding: {} })
}));
// The media viewer pulls in native playback, which has nothing to do with routing.
jest.mock("../../components/NativeMediaViewer", () => ({
  mediaViewerItemFromPulseMedia: jest.fn(() => ({})),
  NativeMediaViewer: () => null
}));

import { MarketplaceScreen } from "../MarketplaceScreen";

const LISTING = {
  id: 77,
  title: "Aeron chair",
  price: 95,
  seller_user_id: 12,
  seller_name: "Dana",
  currency: "USD"
};

async function openListingAndContactSeller(navigate: jest.Mock) {
  mockSearchMarketplace.mockResolvedValue({ items: [LISTING] });
  mockLoadCachedMarketplace.mockResolvedValue([LISTING]);
  mockStartSellerChat.mockResolvedValue({ conversation_id: 4242 });

  const screen = render(
    <MarketplaceScreen navigation={{ navigate } as never} route={{ params: undefined } as never} />
  );
  const title = await screen.findByText("Aeron chair");
  await act(async () => {
    fireEvent.press(title);
  });
  const button = await screen.findByText("Contact Seller");
  await act(async () => {
    fireEvent.press(button);
  });
  await waitFor(() => expect(mockStartSellerChat).toHaveBeenCalledWith(12));
  return screen;
}

describe("commerce entry points land in the Commerce Inbox", () => {
  beforeEach(() => {
    mockStartSellerChat.mockReset();
    mockSearchMarketplace.mockReset();
    mockLoadCachedMarketplace.mockReset();
  });

  it("routes Contact Seller through the Commerce Inbox when the split is on", async () => {
    process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT = "1";
    const navigate = jest.fn();
    await openListingAndContactSeller(navigate);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("BusinessOsMessages", {
        title: "Messages",
        focusConversationId: 4242
      })
    );
    // The social messenger is never the destination.
    expect(navigate.mock.calls.some(([route]) => route === "Chat")).toBe(false);
  });

  it("keeps the old destination exactly while the flag is off", async () => {
    const navigate = jest.fn();
    await openListingAndContactSeller(navigate);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith("Chat", { conversationId: 4242, title: "Dana" })
    );
  });
});

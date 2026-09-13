/**
 * The product page's floating header must sit below the status bar, not under it.
 *
 * `MarketplaceProduct` is registered `headerShown: false`, so it owns the entire
 * window — including the status bar and, on the current iPhones, the Dynamic
 * Island. Its header is a plain 46pt row of `Pressable`s (back, share, save,
 * cart) as the first child of the ScrollView. With no top inset that row lays
 * out at y=0..56, which on a notched device is *entirely* inside the island's
 * band. The controls draw fine and are completely dead: the system consumes the
 * touches before they reach the app.
 *
 * That is not cosmetic. This screen is where "Preview as buyer" lands a seller
 * mid-publish, and back is the only way out of it — no tab bar, no dismiss
 * gesture, because a pushed stack screen has no sheet affordance and the edge
 * swipe is competing with the gallery's horizontal pager. A dead back button
 * strands the seller on a preview of their own draft.
 *
 * It survived every existing test because the whole native suite mocks
 * `useSafeAreaInsets` to `{ top: 0, ... }`. At top 0 a missing inset and a
 * correct one are the same number, so no amount of rendering could see it. This
 * file mocks a real notch instead, which is the only way the assertion has
 * teeth — and is why it is a separate file rather than a case added to
 * `MarketplacePriceLabelRendering`, whose zero-inset mock is module-scoped.
 *
 * What is asserted is the invariant, not the arithmetic: the header's resolved
 * `paddingTop` must clear `insets.top`. A test pinning `insets.top + 8` would
 * fail on a legitimate change to the 8, and would pass for a screen that hard-
 * coded 55 and broke on the next device.
 */

import React from "react";
import { render } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

/** A real notch. The rest of the suite mocks 0, which cannot see this bug. */
const INSETS = { top: 62, bottom: 34, left: 0, right: 0 };

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => INSETS
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../api/marketplaceCommerce", () => ({
  addToCart: jest.fn(async () => ({ lines: [], badgeCount: 0 })),
  fetchCart: jest.fn(async () => ({ lines: [], badgeCount: 0 }))
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({ handlers: {}, contentPadding: {} })
}));
jest.mock("../../components/NativeMediaViewer", () => ({
  mediaViewerItemFromPulseMedia: jest.fn(() => ({})),
  NativeMediaViewer: () => null
}));
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(async () => null),
  setItem: jest.fn(async () => undefined)
}));

import { MarketplaceProductScreen } from "../MarketplaceProductScreen";

const LISTING = {
  id: 701,
  title: "Walnut side table",
  price_label: "$27.00",
  currency: "USD",
  seller_user_id: 12,
  seller_name: "Dana R.",
  category: "Home",
  status: "active",
  approval_status: "approved",
  quantity: 4,
  media: []
};

const navigation = {
  navigate: jest.fn(),
  goBack: jest.fn(),
  setOptions: jest.fn(),
  addListener: jest.fn(() => () => undefined)
};

function renderProduct() {
  return render(
    <MarketplaceProductScreen
      navigation={navigation as never}
      route={{ params: { listingId: LISTING.id, listing: LISTING, title: LISTING.title } } as never}
    />
  );
}

/**
 * Walk up from the back button to the first ancestor that actually resolves a
 * `paddingTop`, and return it. Going through the tree rather than reading the
 * StyleSheet keeps this honest about the value the device sees: the padding
 * arrives as an inline override merged over `styles.header`, so reading the
 * stylesheet entry alone would report the static 0.
 */
function resolvedPaddingTopAbove(node: { parent: unknown; props: Record<string, unknown> } | null): number | null {
  let current = node;
  for (let hops = 0; current && hops < 8; hops += 1) {
    const flat = StyleSheet.flatten(current.props?.style as never) as { paddingTop?: number } | undefined;
    if (flat && typeof flat.paddingTop === "number") return flat.paddingTop;
    current = current.parent as typeof current;
  }
  return null;
}

describe("the product page header", () => {
  it("renders the back control the seller needs to leave the preview", () => {
    // Guards the two assertions below: they are both about a control's layout,
    // and a screen that failed to mount would satisfy either one vacuously.
    const { getByLabelText, getByText } = renderProduct();
    expect(getByText("Walnut side table")).toBeTruthy();
    expect(getByLabelText("Back to Marketplace")).toBeTruthy();
  });

  it("pads the header clear of the status bar so the controls are tappable", () => {
    const { getByLabelText } = renderProduct();
    const paddingTop = resolvedPaddingTopAbove(getByLabelText("Back to Marketplace") as never);
    expect(paddingTop).not.toBeNull();
    // The whole point: the row starts below the island, not inside it.
    expect(paddingTop).toBeGreaterThanOrEqual(INSETS.top);
  });

  it("takes the inset from the device rather than hard-coding one", () => {
    // A screen that pinned a constant would pass the test above on this device
    // and fail on the next one. Nothing here asserts the exact padding, only
    // that it moves with what the device reports.
    const first = resolvedPaddingTopAbove(renderProduct().getByLabelText("Back to Marketplace") as never);
    INSETS.top = 91;
    try {
      const second = resolvedPaddingTopAbove(renderProduct().getByLabelText("Back to Marketplace") as never);
      expect(second).toBeGreaterThan(first as number);
      expect(second).toBeGreaterThanOrEqual(91);
    } finally {
      INSETS.top = 62;
    }
  });
});

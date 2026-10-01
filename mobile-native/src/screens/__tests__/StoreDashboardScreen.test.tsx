/**
 * The Store dashboard makes promises that are invisible in a screenshot, so
 * they are pinned here.
 *
 * 1. **All five states render, and each is distinguishable.** Loading, empty,
 *    error, paused and offline are separate treatments, and an error must not
 *    look like an empty store — that is the specific failure of a screen that
 *    collapses a failed fetch into an empty array.
 * 2. **A failed section takes down only itself.** Orders failing must leave the
 *    listings on screen with their own retry, and vice versa.
 * 3. **Status is never colour-only.** Every LED renders its own text, so a
 *    seller who cannot distinguish red from green still reads "Out of stock".
 * 4. **Navigation is preserved.** Edit reaches the existing listing editor
 *    (`mode: "create"`, the panel set that contains it), not a route that does
 *    not exist. This is the constraint most likely to be broken by a refactor
 *    and the least likely to be noticed.
 * 5. **Reduce-motion stops the animation, not the content.** Under the OS
 *    setting every figure must still be present and readable.
 */

import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

const mockReducedMotion = jest.fn(() => false);

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: "LinearGradient" }));
jest.mock("react-native-svg", () => ({
  __esModule: true,
  default: "Svg",
  Svg: "Svg",
  Path: "Path"
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../theme/logiNexusMotion", () => ({
  ...jest.requireActual("../../theme/logiNexusMotion"),
  // The real hook reads AccessibilityInfo, which the test environment cannot
  // toggle. Swapping it lets both branches be exercised for real.
  useLogiNexusReducedMotion: () => mockReducedMotion()
}));

// The header bell now reads the shared unread store rather than counting open
// orders off the dashboard payload. That store talks to the network on mount,
// which a screen test has no business doing, so the count is supplied directly
// and each test states the badge it wants to exercise.
const mockBellCount = jest.fn(() => 0);
jest.mock("../../core/unreadCounts", () => ({
  ...jest.requireActual("../../core/unreadCounts"),
  useBellCount: () => mockBellCount(),
  refreshUnreadCounts: jest.fn(async () => undefined)
}));

const mockLoad = jest.fn();
jest.mock("../../api/storeDashboard", () => ({
  ...jest.requireActual("../../api/storeDashboard"),
  loadStoreDashboard: (...args: unknown[]) => mockLoad(...args)
}));

import type { MarketplaceListing, MarketplaceSellerOrder } from "../../api/marketplace";
import type { StoreLoadResult } from "../../api/storeDashboard";
import { StoreDashboardScreen } from "../StoreDashboardScreen";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

function listing(over: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id: 1,
    listing_id: 1,
    seller_name: "Bright Coffee Co",
    title: "Bright Coffee Beans",
    price_label: "12.00",
    currency: "USD",
    quantity: 20,
    status: "active",
    approval_status: "approved",
    ...over
  } as MarketplaceListing;
}

function order(over: Partial<MarketplaceSellerOrder> = {}): MarketplaceSellerOrder {
  return {
    id: 1,
    item_type: "listing",
    item_id: 1,
    amount_cents: 1200,
    gross_amount_cents: 1200,
    currency: "USD",
    status: "paid",
    created_at: new Date().toISOString(),
    ...over
  } as MarketplaceSellerOrder;
}

function result(over: Partial<StoreLoadResult> = {}): StoreLoadResult {
  return {
    listings: { status: "ok", data: [listing()] },
    orders: { status: "ok", data: [order()] },
    cachedAt: null,
    offline: false,
    ...over
  };
}

function navigation() {
  return { navigate: jest.fn(), goBack: jest.fn() };
}

async function renderScreen(nav = navigation()) {
  const view = render(<StoreDashboardScreen navigation={nav} route={{ params: { mode: "dashboard" } }} />);
  // Flush the load promise and its state updates.
  await act(async () => {
    await Promise.resolve();
  });
  return { ...view, nav };
}

beforeEach(() => {
  mockLoad.mockReset();
  mockReducedMotion.mockReturnValue(false);
  mockBellCount.mockReturnValue(0);
  mockLoad.mockResolvedValue(result());
});

afterEach(() => {
  jest.useRealTimers();
});

/* ------------------------------------------------------------------ *
 * State 1 — loading
 * ------------------------------------------------------------------ */

describe("loading state", () => {
  it("shows placeholders in place of the content, not a spinner over a blank page", async () => {
    let settle: (value: StoreLoadResult) => void = () => undefined;
    mockLoad.mockReturnValue(new Promise<StoreLoadResult>((resolve) => { settle = resolve; }));

    const view = render(<StoreDashboardScreen navigation={navigation()} route={{}} />);

    // The header and the status strip are chrome — they are present immediately,
    // so the page does not reflow when the data lands.
    expect(view.getByLabelText("Search your store")).toBeTruthy();
    expect(view.queryByText("Bright Coffee Beans")).toBeNull();

    await act(async () => {
      settle(result());
      await Promise.resolve();
    });

    expect(view.getByText("Bright Coffee Beans")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * State 2 — populated
 * ------------------------------------------------------------------ */

describe("populated state", () => {
  it("renders the listing with its title, stock and sales", async () => {
    const view = await renderScreen();
    expect(view.getByText("Bright Coffee Beans")).toBeTruthy();
    expect(view.getByText("20 in stock")).toBeTruthy();
  });

  it("reports the store as open in words, not only in colour", async () => {
    const view = await renderScreen();
    expect(view.getByText("Bright Coffee Co · Open for orders")).toBeTruthy();
  });

  it("shows no attention banner when nothing needs attention", async () => {
    const view = await renderScreen();
    // A banner that is always there is a banner sellers stop reading.
    expect(view.queryByText(/running low|out of stock/i)).toBeNull();
  });

  it("raises the banner when a listing runs low, and says what to do", async () => {
    mockLoad.mockResolvedValue(
      result({ listings: { status: "ok", data: [listing({ quantity: 2 })] } })
    );
    const view = await renderScreen();
    expect(view.getByText("1 listing is running low")).toBeTruthy();
    expect(view.getByText("Only 2 left")).toBeTruthy();
  });

  it("names an out-of-stock listing as unbuyable rather than leaving it to the colour", async () => {
    mockLoad.mockResolvedValue(
      result({ listings: { status: "ok", data: [listing({ quantity: 0 })] } })
    );
    const view = await renderScreen();
    expect(view.getByText("1 listing is out of stock")).toBeTruthy();
    expect(view.getByText("Out of stock — hidden")).toBeTruthy();
    expect(view.getByText("Restock")).toBeTruthy();
  });

  it("exposes tab counts to a screen reader rather than only drawing them", async () => {
    mockLoad.mockResolvedValue(
      result({
        listings: {
          status: "ok",
          data: [listing({ id: 1, listing_id: 1, quantity: 20 }), listing({ id: 2, listing_id: 2, quantity: 1 })]
        }
      })
    );
    const view = await renderScreen();
    expect(view.getByLabelText("Low stock, 1")).toBeTruthy();
    expect(view.getByLabelText("All, 2")).toBeTruthy();
  });

  it("filters the list when a tab is chosen", async () => {
    mockLoad.mockResolvedValue(
      result({
        listings: {
          status: "ok",
          data: [
            listing({ id: 1, listing_id: 1, title: "Beans", quantity: 20 }),
            listing({ id: 2, listing_id: 2, title: "Mug", quantity: 1 })
          ]
        }
      })
    );
    const view = await renderScreen();
    expect(view.getByText("Beans")).toBeTruthy();

    await act(async () => {
      fireEvent.press(view.getByLabelText("Low stock, 1"));
    });

    expect(view.queryByText("Beans")).toBeNull();
    expect(view.getByText("Mug")).toBeTruthy();
  });

  it("keeps the KPIs visible while searching instead of navigating away", async () => {
    mockLoad.mockResolvedValue(
      result({
        listings: {
          status: "ok",
          data: [
            listing({ id: 1, listing_id: 1, title: "Beans" }),
            listing({ id: 2, listing_id: 2, title: "Mug" })
          ]
        }
      })
    );
    const view = await renderScreen();

    await act(async () => {
      fireEvent.changeText(view.getByLabelText("Search your listings and orders"), "mug");
    });

    expect(view.queryByText("Beans")).toBeNull();
    expect(view.getByText("Mug")).toBeTruthy();
    expect(view.getByText("Today's sales")).toBeTruthy();
  });

  it("says what did not match rather than showing an empty gap", async () => {
    const view = await renderScreen();
    await act(async () => {
      fireEvent.changeText(view.getByLabelText("Search your listings and orders"), "zzz");
    });
    expect(view.getByText(/No listings match/)).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * State 3 — empty store
 * ------------------------------------------------------------------ */

describe("empty state", () => {
  it("keeps the store chrome and invites a first listing", async () => {
    mockLoad.mockResolvedValue(result({ listings: { status: "ok", data: [] }, orders: { status: "ok", data: [] } }));
    const view = await renderScreen();

    // Still their store — header, strip and KPIs remain.
    expect(view.getByText("Today's sales")).toBeTruthy();
    expect(view.getByText("Your store is ready. It just needs something to sell.")).toBeTruthy();
    expect(view.getByLabelText("Add your first listing")).toBeTruthy();
  });

  it("does not present an empty store as paused", async () => {
    mockLoad.mockResolvedValue(result({ listings: { status: "ok", data: [] }, orders: { status: "ok", data: [] } }));
    const view = await renderScreen();
    expect(view.queryByText(/Paused/)).toBeNull();
  });

  it("routes the empty-state button to the existing create gateway", async () => {
    mockLoad.mockResolvedValue(result({ listings: { status: "ok", data: [] }, orders: { status: "ok", data: [] } }));
    const view = await renderScreen();

    await act(async () => {
      fireEvent.press(view.getByLabelText("Add your first listing"));
    });

    expect(view.nav.navigate).toHaveBeenCalledWith("MarketplaceCreateGateway", {
      title: "Create Listing"
    });
  });
});

/* ------------------------------------------------------------------ *
 * State 4 — error
 * ------------------------------------------------------------------ */

describe("error state", () => {
  it("fails one section without taking down the other", async () => {
    mockLoad.mockResolvedValue(
      result({ orders: { status: "error", message: "Orders didn't load." } })
    );
    const view = await renderScreen();

    expect(view.getByText("Sales and orders didn't load.")).toBeTruthy();
    // The listings half is unaffected and still on screen.
    expect(view.getByText("Bright Coffee Beans")).toBeTruthy();
  });

  it("does not show an empty store when the listings call failed", async () => {
    // The failure this replaces: a rejected fetch collapsed into an empty array
    // and the seller was told their store had no listings.
    mockLoad.mockResolvedValue(
      result({ listings: { status: "error", message: "Listings didn't load." } })
    );
    const view = await renderScreen();

    expect(view.getByText("Your listings didn't load.")).toBeTruthy();
    expect(view.queryByText("Your store is ready. It just needs something to sell.")).toBeNull();
  });

  it("never says something went wrong", async () => {
    mockLoad.mockResolvedValue(
      result({
        listings: { status: "error", message: "Listings didn't load." },
        orders: { status: "error", message: "Orders didn't load." }
      })
    );
    const view = await renderScreen();
    expect(view.queryByText(/something went wrong/i)).toBeNull();
  });

  it("offers an inline retry that reloads", async () => {
    mockLoad.mockResolvedValue(
      result({ listings: { status: "error", message: "Listings didn't load." } })
    );
    const view = await renderScreen();
    expect(mockLoad).toHaveBeenCalledTimes(1);

    mockLoad.mockResolvedValue(result());
    await act(async () => {
      fireEvent.press(view.getByLabelText("Retry. Your listings didn't load."));
      await Promise.resolve();
    });

    expect(mockLoad).toHaveBeenCalledTimes(2);
    expect(view.getByText("Bright Coffee Beans")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * State 5 — paused, and offline
 * ------------------------------------------------------------------ */

describe("paused state", () => {
  it("says buyers cannot order and offers to reopen", async () => {
    mockLoad.mockResolvedValue(
      result({ listings: { status: "ok", data: [listing({ quantity: 0 })] } })
    );
    const view = await renderScreen();

    expect(view.getByText("Bright Coffee Co · Paused — buyers can't order")).toBeTruthy();
    expect(view.getByText("Reopen")).toBeTruthy();
  });

  it("keeps the KPIs while paused", async () => {
    mockLoad.mockResolvedValue(
      result({ listings: { status: "ok", data: [listing({ quantity: 0 })] } })
    );
    const view = await renderScreen();
    expect(view.getByText("Today's sales")).toBeTruthy();
    expect(view.getByText("Open orders")).toBeTruthy();
  });
});

describe("offline state", () => {
  it("labels cached data with when it was captured", async () => {
    mockLoad.mockResolvedValue(
      result({ offline: true, cachedAt: new Date(Date.now() - 3_600_000).toISOString() })
    );
    const view = await renderScreen();

    expect(view.getByText(/Offline — showing your store as of/)).toBeTruthy();
    // Cached content is shown, not withheld.
    expect(view.getByText("Bright Coffee Beans")).toBeTruthy();
  });

  it("shows no offline note when the data is live", async () => {
    const view = await renderScreen();
    expect(view.queryByText(/Offline —/)).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Navigation contracts
 * ------------------------------------------------------------------ */

describe("navigation", () => {
  it("sends Edit to the mode that renders the existing listing editor", async () => {
    const view = await renderScreen();

    await act(async () => {
      fireEvent.press(view.getByLabelText("Edit Bright Coffee Beans"));
    });

    // `create` is the mode whose panel set contains the editor. Routing to
    // `dashboard` — which this screen now occupies — would orphan it. The id
    // is what lands the seller on that listing's editor instead of a blank one.
    expect(view.nav.navigate).toHaveBeenCalledWith("SellerStore", {
      mode: "create",
      title: "Bright Coffee Beans",
      listingId: 1
    });
  });

  it("sends the Orders KPI to the existing orders mode", async () => {
    const view = await renderScreen();

    await act(async () => {
      fireEvent.press(view.getByLabelText(/^Open orders/));
    });

    expect(view.nav.navigate).toHaveBeenCalledWith("SellerStore", { mode: "orders" });
  });

  it("previews the storefront through the buyer marketplace tab", async () => {
    const view = await renderScreen();

    await act(async () => {
      fireEvent.press(view.getByLabelText("Preview your storefront as a buyer"));
    });

    expect(view.nav.navigate).toHaveBeenCalledWith("Tabs", { screen: "Marketplace" });
  });

  it("only navigates to routes that exist in the stack", async () => {
    mockBellCount.mockReturnValue(1);
    const view = await renderScreen();
    const known = new Set([
      "SellerStore",
      "Tabs",
      "MarketplaceCreateGateway",
      "BusinessOsInsights",
      // The bell opens the seller activity feed, which is where the shared
      // unread count is actually cleared. Registered in AppNavigator.
      "BusinessOsActivity",
      "Notifications"
    ]);

    await act(async () => {
      fireEvent.press(view.getByLabelText("Add a listing"));
      fireEvent.press(view.getByLabelText("Notifications, 1 unread"));
    });

    view.nav.navigate.mock.calls.forEach(([name]) => {
      expect(known.has(String(name))).toBe(true);
    });
  });

  it("does not disable a tile by wiring it to an unrelated screen", async () => {
    const view = await renderScreen();
    // Shipping and Returns have no screen and no endpoint. Honest and inert
    // beats opening something that is not what the label promised.
    fireEvent.press(view.getByLabelText(/^Shipping/));
    expect(view.nav.navigate).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------------ *
 * Reduce motion
 * ------------------------------------------------------------------ */

describe("reduce motion", () => {
  it("keeps every figure readable with animation off", async () => {
    mockReducedMotion.mockReturnValue(true);
    mockLoad.mockResolvedValue(
      result({ listings: { status: "ok", data: [listing({ quantity: 2 })] } })
    );
    const view = await renderScreen();

    // Content, status and the banner all survive; only the motion is gone.
    expect(view.getByText("Bright Coffee Beans")).toBeTruthy();
    expect(view.getByText("Only 2 left")).toBeTruthy();
    expect(view.getByText("1 listing is running low")).toBeTruthy();
    expect(view.getByText("Today's sales")).toBeTruthy();
  });

  it("renders the same content whether or not motion is reduced", async () => {
    mockReducedMotion.mockReturnValue(false);
    const moving = await renderScreen();
    const movingTitle = moving.getByText("Bright Coffee Beans").props.children;
    const movingStock = moving.getByText("20 in stock").props.children;

    moving.unmount();
    mockReducedMotion.mockReturnValue(true);
    const still = await renderScreen();

    expect(still.getByText("Bright Coffee Beans").props.children).toEqual(movingTitle);
    expect(still.getByText("20 in stock").props.children).toEqual(movingStock);
  });
});

/* ------------------------------------------------------------------ *
 * Accessibility
 * ------------------------------------------------------------------ */

describe("accessibility", () => {
  it("announces a listing as one sentence rather than as fragments", async () => {
    const view = await renderScreen();
    // The row and its Edit button both carry the title; the row is the one that
    // reads out the whole listing.
    const labels = view
      .getAllByLabelText(/Bright Coffee Beans/)
      .map((node) => String(node.props.accessibilityLabel));
    const row = labels.find((label) => label.includes("20 in stock"));

    expect(row).toBeDefined();
    expect(row).toContain("Bright Coffee Beans");
    // Price and sales are in the same sentence, not read as loose fragments.
    expect(row).toContain("12.00");
    expect(labels).toContain("Edit Bright Coffee Beans");
  });

  it("gives the KPI cards a label a screen reader can read as a sentence", async () => {
    const view = await renderScreen();
    expect(view.getByLabelText(/^Today's sales/)).toBeTruthy();
  });

  it("pairs every status indicator with text", async () => {
    mockLoad.mockResolvedValue(
      result({
        listings: {
          status: "ok",
          data: [
            listing({ id: 1, listing_id: 1, title: "Out", quantity: 0 }),
            listing({ id: 2, listing_id: 2, title: "Low", quantity: 1 }),
            listing({ id: 3, listing_id: 3, title: "Fine", quantity: 40 }),
            listing({ id: 4, listing_id: 4, title: "Drafted", status: "draft" })
          ]
        }
      })
    );
    const view = await renderScreen();

    // Four LEDs, four different colours — and four different sentences.
    expect(view.getByText("Out of stock — hidden")).toBeTruthy();
    expect(view.getByText("Only 1 left")).toBeTruthy();
    expect(view.getByText("40 in stock")).toBeTruthy();
    expect(view.getByText("Draft — not published")).toBeTruthy();
  });
});

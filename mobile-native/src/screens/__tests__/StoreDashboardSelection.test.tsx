/**
 * Selection mode on the real screen — §16–§20.
 *
 * `marketplace/storeSelection` already proves the state model in isolation, and
 * those 33 tests are the ones that pin what Select All takes and which rows a
 * bulk action can touch. This file exists for the half that a pure module
 * cannot reach: whether the screen actually *asks* it the right questions.
 *
 * That distinction matters, because every bug this file is built to catch is a
 * wiring bug, not a logic bug — the module can be perfectly correct while the
 * screen calls it with the wrong list:
 *
 * * Select All handed `allRows` instead of `visible`, so it takes the whole
 *   catalogue and the label's "6 shown" becomes a lie told by correct code.
 * * `reconcile` never called on reload, so a listing deleted on another device
 *   stays in the selection and the count keeps including it.
 * * The row's blocked wash computed from its own partition rather than the
 *   screen's, so the button says "4 blocked" while five rows are greyed.
 * * Tapping a row in selection mode still opening the editor, which abandons a
 *   half-built selection.
 *
 * None of those change a single line of `storeSelection.ts`.
 */

import React from "react";
import { act, fireEvent, render } from "@testing-library/react-native";

const mockReducedMotion = jest.fn(() => true);

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
/**
 * The screen refreshes through `registerSyncInvalidation`, not through a button,
 * so the mock keeps the handlers and the test fires one. This is also the real
 * path a listing deleted on another device takes to this screen, which makes it
 * the honest way to test the reload rather than a convenient one.
 */
const syncHandlers: Record<string, (() => void)[]> = {};
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn((channel: string, fn: () => void) => {
    (syncHandlers[channel] ||= []).push(fn);
    return () => undefined;
  })
}));
jest.mock("../../theme/logiNexusMotion", () => ({
  ...jest.requireActual("../../theme/logiNexusMotion"),
  useLogiNexusReducedMotion: () => mockReducedMotion()
}));
jest.mock("../../core/unreadCounts", () => ({
  ...jest.requireActual("../../core/unreadCounts"),
  useBellCount: () => 0,
  refreshUnreadCounts: jest.fn(async () => undefined)
}));

const mockLoad = jest.fn();
jest.mock("../../api/storeDashboard", () => ({
  ...jest.requireActual("../../api/storeDashboard"),
  loadStoreDashboard: (...args: unknown[]) => mockLoad(...args)
}));

import type { MarketplaceListing } from "../../api/marketplace";
import type { StoreLoadResult } from "../../api/storeDashboard";
import { StoreDashboardScreen } from "../StoreDashboardScreen";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

const READY = { publishable: true, checkout_ready: true, blockers: [], warnings: [] };

/**
 * Not publishable. `blockers` is what `partition` reads.
 *
 * Note that `warnings` stays empty, which keeps this row *in stock* for tab
 * purposes — the two lists answer different questions and the screen reads them
 * from different places. Conflating them is how the first draft of this file
 * ended up filtering to the "Out" tab and finding an in-stock row there.
 */
const NOT_READY = {
  publishable: false,
  checkout_ready: false,
  blockers: ["MISSING_PRICE"],
  warnings: []
};

/** Out of stock. `warnings` is what the tab filter reads. */
const SOLD_OUT = {
  publishable: true,
  checkout_ready: false,
  blockers: [],
  warnings: ["OUT_OF_STOCK"]
};

function listing(id: number, over: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id,
    listing_id: id,
    seller_name: "Bright Coffee Co",
    title: `Listing ${id}`,
    price_label: "12.00",
    currency: "USD",
    quantity: 20,
    status: "active",
    approval_status: "approved",
    readiness: READY,
    ...over
  } as MarketplaceListing;
}

function result(listings: MarketplaceListing[]): StoreLoadResult {
  return {
    listings: { status: "ok", data: listings },
    orders: { status: "ok", data: [] },
    cachedAt: null,
    offline: false
  };
}

async function renderScreen() {
  const nav = { navigate: jest.fn(), goBack: jest.fn() };
  const view = render(<StoreDashboardScreen navigation={nav} route={{ params: { mode: "dashboard" } }} />);
  await act(async () => {
    await Promise.resolve();
  });
  return { ...view, nav };
}

/** Enter selection mode the way a seller does. */
function longPressRow(view: ReturnType<typeof render>, title: string) {
  fireEvent(view.getByText(title), "longPress");
}

/**
 * Tabs put their count in the accessibility label rather than in a second text
 * node, so "Out" alone does not match. Anchored so "Out" cannot also hit
 * "Out of stock" copy elsewhere on the screen.
 */
function pressTab(view: ReturnType<typeof render>, label: string) {
  fireEvent.press(view.getByLabelText(new RegExp(`^${label}, `)));
}

/** A payload landing from the sync channel, the way a real refresh arrives. */
async function reloadWith(listings: MarketplaceListing[]) {
  mockLoad.mockResolvedValue(result(listings));
  await act(async () => {
    syncHandlers.seller_inventory?.forEach((fn) => fn());
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  mockLoad.mockReset();
  mockReducedMotion.mockReturnValue(true);
  mockLoad.mockResolvedValue(result([listing(1), listing(2), listing(3)]));
  for (const key of Object.keys(syncHandlers)) delete syncHandlers[key];
});

/* ------------------------------------------------------------------ *
 * Entering and leaving
 * ------------------------------------------------------------------ */

describe("entering selection mode", () => {
  it("is not on by default — the bar is absent and rows open the editor", async () => {
    const view = await renderScreen();
    expect(view.queryByText(/Select all/)).toBeNull();

    fireEvent.press(view.getByText("Listing 1"));
    expect(view.nav.navigate).toHaveBeenCalledWith(
      "SellerStore",
      expect.objectContaining({ listingId: 1, mode: "create" })
    );
  });

  it("starts with the row the seller long-pressed already picked", async () => {
    // Entering empty would make the gesture cost two taps to do the obvious
    // thing, and the row under the finger is unambiguously the one they meant.
    const view = await renderScreen();
    longPressRow(view, "Listing 2");
    expect(view.getByText("1 selected")).toBeTruthy();
  });

  it("stops a row tap from navigating away mid-selection", async () => {
    const view = await renderScreen();
    longPressRow(view, "Listing 2");
    view.nav.navigate.mockClear();

    fireEvent.press(view.getByText("Listing 1"));

    // Landing in the editor here abandons the selection the seller is building.
    expect(view.nav.navigate).not.toHaveBeenCalled();
    expect(view.getByText("2 selected")).toBeTruthy();
  });

  it("hides the per-row Edit buttons, which would also navigate away", async () => {
    const view = await renderScreen();
    expect(view.queryAllByText("Edit").length).toBe(3);
    longPressRow(view, "Listing 1");
    expect(view.queryAllByText("Edit").length).toBe(0);
  });

  it("hides the inline status action too, which is the other way out of the list", async () => {
    // Edit is the obvious escape hatch and it was the only one the first draft
    // of this file checked. "Restock" sits on the row as well, navigates to the
    // same editor, and is *more* likely to be tapped by accident while picking
    // — it is small, it is mid-row, and it is the thing the seller was reading
    // when they decided to select the row in the first place.
    mockLoad.mockResolvedValue(result([listing(1), listing(2, { readiness: SOLD_OUT })]));
    const view = await renderScreen();
    expect(view.queryAllByText("Restock").length).toBe(1);

    longPressRow(view, "Listing 1");

    expect(view.queryAllByText("Restock").length).toBe(0);
    // The status itself is not hidden — only the thing that navigates. A seller
    // picking rows for a bulk action still needs to see which ones are dead.
    expect(view.queryAllByText(/Out of stock/).length).toBeGreaterThan(0);
  });

  it("leaves on Done, and the rows open the editor again", async () => {
    const view = await renderScreen();
    longPressRow(view, "Listing 1");
    fireEvent.press(view.getByText("Done"));

    expect(view.queryByText(/Select all/)).toBeNull();
    fireEvent.press(view.getByText("Listing 1"));
    expect(view.nav.navigate).toHaveBeenCalled();
  });

  it("stays open when the seller deselects their last row", async () => {
    // "Selecting, with nothing picked" is a real state. Closing the mode here
    // would yank the bar away under a seller who is mid-correction.
    const view = await renderScreen();
    longPressRow(view, "Listing 1");
    fireEvent.press(view.getByText("Listing 1"));

    expect(view.getByText("Tap listings to select")).toBeTruthy();
    expect(view.queryByText(/Select all/)).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * Select All — the scope question
 * ------------------------------------------------------------------ */

describe("Select All is scoped to what is on screen", () => {
  it("names the count and says 'shown'", async () => {
    const view = await renderScreen();
    longPressRow(view, "Listing 1");
    expect(view.getByText("Select all 3 shown")).toBeTruthy();
  });

  it("takes only the rows the active tab is showing", async () => {
    // THE wiring bug. If the screen hands Select All `allRows` instead of the
    // filtered `visible`, this reports 4 and the label's "1 shown" is a lie
    // told by a correct module.
    mockLoad.mockResolvedValue(
      result([
        listing(1, { quantity: 0, readiness: SOLD_OUT }),
        listing(2),
        listing(3),
        listing(4)
      ])
    );
    const view = await renderScreen();

    // Enter selection mode and then clear it, so Select All is measured from an
    // empty selection rather than from the row the long-press picked.
    longPressRow(view, "Listing 2");
    fireEvent.press(view.getByText("Listing 2"));
    expect(view.getByText("Select all 4 shown")).toBeTruthy();

    // Filter to the one out-of-stock row. The catalogue still holds four.
    pressTab(view, "Out");

    expect(view.getByText("Select all 1 shown")).toBeTruthy();
    fireEvent.press(view.getByText("Select all 1 shown"));

    // One, not four. Handing `allRows` to Select All here would report
    // "4 selected" under a label that had just promised one.
    expect(view.getByText("1 selected")).toBeTruthy();
  });

  it("flips to Deselect once everything shown is picked", async () => {
    const view = await renderScreen();
    longPressRow(view, "Listing 1");
    fireEvent.press(view.getByText("Select all 3 shown"));

    expect(view.getByText("3 selected")).toBeTruthy();
    expect(view.getByText("Deselect all 3 shown")).toBeTruthy();

    fireEvent.press(view.getByText("Deselect all 3 shown"));
    expect(view.getByText("Tap listings to select")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * Rows that scrolled off — the honesty question
 * ------------------------------------------------------------------ */

describe("a selection that outlives the filter", () => {
  it("survives a tab change and names the part no longer on screen", async () => {
    mockLoad.mockResolvedValue(
      result([listing(1, { quantity: 0, readiness: SOLD_OUT }), listing(2), listing(3)])
    );
    const view = await renderScreen();

    longPressRow(view, "Listing 2");
    fireEvent.press(view.getByText("Listing 3"));
    expect(view.getByText("2 selected")).toBeTruthy();

    // Both picked rows are in-stock, so filtering to Out hides them.
    pressTab(view, "Out");

    // Kept, not dropped — and the seller is told where they went. A bare
    // "2 selected" here would be the dangerous version of the same state.
    expect(view.getByText("2 selected · 2 not shown")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * Reload — the stale-id question
 * ------------------------------------------------------------------ */

describe("a reload that changes the catalogue", () => {
  it("drops a selected listing that no longer exists", async () => {
    const view = await renderScreen();
    longPressRow(view, "Listing 1");
    fireEvent.press(view.getByText("Listing 2"));
    expect(view.getByText("2 selected")).toBeTruthy();

    // Listing 2 deleted on another device. Left in the selection, a bulk action
    // posts an id the server will reject, and the failure is reported against a
    // row this screen no longer shows.
    await reloadWith([listing(1), listing(3)]);

    expect(view.queryByText("Listing 2")).toBeNull();
    expect(view.getByText("1 selected")).toBeTruthy();
  });

  /**
   * The assertion above is weaker than it looks, and this is the one that has
   * teeth.
   *
   * `selectionSummary` counts through `selectedRows`, which already filters to
   * ids present in the list — so the count reads correctly whether or not
   * `reconcile` ever ran. Deleting the reconcile effect entirely leaves the test
   * above passing. Measured, not assumed: it survived the mutation.
   *
   * What a missing `reconcile` actually does is leave the id in the set, where
   * it is invisible until the row comes back. Then it returns already ticked —
   * a listing the seller never selected, silently inside the next bulk publish.
   */
  it("does not re-tick a row that vanished and came back", async () => {
    const view = await renderScreen();
    longPressRow(view, "Listing 1");
    fireEvent.press(view.getByText("Listing 2"));
    expect(view.getByText("2 selected")).toBeTruthy();

    await reloadWith([listing(1), listing(3)]);
    expect(view.getByText("1 selected")).toBeTruthy();

    // Listing 2 is back — unselected, because the seller has not picked it since.
    await reloadWith([listing(1), listing(2), listing(3)]);

    expect(view.getByText("Listing 2")).toBeTruthy();
    expect(view.getByText("1 selected")).toBeTruthy();
    expect(view.getByLabelText(/^Listing 2,/).props.accessibilityState.checked).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * The blocked preview — §34
 * ------------------------------------------------------------------ */

describe("the publish preview on the rows themselves", () => {
  it("names why a selected row will not publish", async () => {
    mockLoad.mockResolvedValue(result([listing(1), listing(2, { readiness: NOT_READY })]));
    const view = await renderScreen();

    longPressRow(view, "Listing 1");
    fireEvent.press(view.getByText("Listing 2"));

    // "N blocked" on a button says how many. Only the row says which, and which
    // is what the seller needs in order to go and fix it.
    expect(view.getByText("1 thing left")).toBeTruthy();
  });

  it("refuses a row whose readiness never arrived", async () => {
    // The rule, at the screen level: absence is not a clean bill of health.
    mockLoad.mockResolvedValue(result([listing(1, { readiness: undefined })]));
    const view = await renderScreen();
    longPressRow(view, "Listing 1");

    expect(view.getByText("No readiness check yet")).toBeTruthy();
  });

  it("says nothing about a row that is not in the selection", async () => {
    // An unselected row wearing "1 thing left" reads as a warning about the
    // listing rather than a preview of a batch it is not part of.
    mockLoad.mockResolvedValue(result([listing(1), listing(2, { readiness: NOT_READY })]));
    const view = await renderScreen();

    longPressRow(view, "Listing 1");
    expect(view.queryByText("1 thing left")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Accessibility
 * ------------------------------------------------------------------ */

describe("selection is never signalled by colour alone", () => {
  it("announces each row as a checkbox with its checked state", async () => {
    const view = await renderScreen();
    longPressRow(view, "Listing 1");

    const picked = view.getByLabelText(/^Listing 1,/);
    expect(picked.props.accessibilityRole).toBe("checkbox");
    expect(picked.props.accessibilityState.checked).toBe(true);

    const unpicked = view.getByLabelText(/^Listing 2,/);
    expect(unpicked.props.accessibilityState.checked).toBe(false);
  });

  it("reads the blocked reason out rather than leaving it to the wash", async () => {
    mockLoad.mockResolvedValue(result([listing(1, { readiness: NOT_READY })]));
    const view = await renderScreen();
    longPressRow(view, "Listing 1");

    expect(view.getByLabelText(/1 thing left/)).toBeTruthy();
  });
});

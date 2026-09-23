/**
 * The bulk journey on the real screen — §19, §23, §31, §33, §34.
 *
 * `marketplace/storeBulkRun` already proves the attempt model and the
 * partial-success sentences in isolation. This file exists for the part a pure
 * module cannot see: whether the screen sends what the button promised, and
 * whether what the seller ends up looking at came from the server.
 *
 * Every failure it is built to catch is a wiring failure that leaves both
 * modules correct:
 *
 * * The request built from `eligible` instead of the whole reviewed set, so a
 *   row the seller fixed on another device is silently dropped from work they
 *   asked for.
 * * A fresh idempotency key minted per send, so Try again publishes everything a
 *   second time — the exact thing the key exists to prevent.
 * * The result face fed from the request rather than the response, so "18
 *   published" appears over a list of fourteen.
 * * No reload after the write, so the seller taps Done and lands on the store as
 *   it was before they published — §31's read-back missing, with green unit
 *   tests either side of the gap.
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
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
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

const mockBatch = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  batchMarketplaceSellerListings: (...args: unknown[]) => mockBatch(...args)
}));

import type {
  MarketplaceBatchOutcome,
  MarketplaceBatchResponse,
  MarketplaceBatchResult,
  MarketplaceListing
} from "../../api/marketplace";
import { PulseApiError } from "../../api/pulseApi";
import type { StoreLoadResult } from "../../api/storeDashboard";
import { StoreDashboardScreen } from "../StoreDashboardScreen";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

const READY = {
  publishable: true,
  resubmittable: false,
  checkout_ready: true,
  blockers: [],
  warnings: [],
  summary: "Ready to publish",
  fixes: [],
  notes: []
};

const NOT_READY = {
  publishable: false,
  resubmittable: false,
  checkout_ready: false,
  blockers: ["MISSING_PRICE"],
  warnings: [],
  summary: "1 thing left",
  fixes: [{ code: "MISSING_PRICE", label: "Add price", section: "pricing" }],
  notes: []
};

/**
 * A finished draft: publishable, not yet live, and the server says a bulk
 * publish would take it. The only shape where the Publish CTA has work to do.
 */
const DRAFT = {
  status: "draft",
  approval_status: "draft",
  readiness: READY,
  bulk_eligibility: { publish: null, hide: null }
} as Partial<MarketplaceListing>;

/** Publishable, unfinished — the blocked half of §34's preview. */
const DRAFT_UNREADY = {
  status: "draft",
  approval_status: "draft",
  readiness: NOT_READY,
  bulk_eligibility: {
    publish: { code: "NOT_READY", reason: "1 thing left", blockers: ["MISSING_PRICE"] },
    hide: null
  }
} as Partial<MarketplaceListing>;

/**
 * Live and perfect — and still not publishable *again*.
 *
 * The case no client-side derivation can reach: `publishable` is true, so a
 * phone deciding for itself would send it and knock a live product back into
 * the review queue. Only the server's `bulk_eligibility` knows.
 */
const LIVE = {
  status: "active",
  approval_status: "approved",
  readiness: READY,
  bulk_eligibility: {
    publish: { code: "ALREADY_PUBLISHED", reason: "Already published" },
    hide: null
  }
} as Partial<MarketplaceListing>;

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
    bulk_eligibility: { publish: null, hide: null },
    ...over
  } as MarketplaceListing;
}

function result(listings: MarketplaceListing[]): StoreLoadResult {
  return {
    listings: { status: "ok", data: listings },
    orders: { status: "ok", data: [] },
    metrics: null,
    cachedAt: null,
    offline: false
  };
}

function entry(
  listing_id: number,
  outcome: MarketplaceBatchOutcome,
  over: Partial<MarketplaceBatchResult> = {}
): MarketplaceBatchResult {
  return { listing_id, outcome, ...over };
}

function batchResponse(results: MarketplaceBatchResult[]): MarketplaceBatchResponse {
  return {
    ok: true,
    batch_id: "batch-1",
    action: "publish",
    requested_count: results.length,
    successful_count: results.filter((item) => item.outcome === "succeeded").length,
    blocked_count: results.filter((item) => item.outcome === "blocked").length,
    failed_count: results.filter((item) => item.outcome === "failed").length,
    results
  };
}

/* ------------------------------------------------------------------ *
 * Harness
 * ------------------------------------------------------------------ */

async function renderScreen() {
  const nav = { navigate: jest.fn(), push: jest.fn(), goBack: jest.fn() };
  const view = render(
    <StoreDashboardScreen navigation={nav} route={{ params: { mode: "dashboard" } }} />
  );
  await act(async () => {
    await Promise.resolve();
  });
  return { ...view, nav };
}

type View = Awaited<ReturnType<typeof renderScreen>>;

/**
 * Enter selection mode and take everything the active tab is showing.
 *
 * Deliberately goes through Select All rather than tapping each row: "select all
 * and publish" is the gesture §33 describes and the one that puts an
 * already-live listing into a publish batch if the screen decides eligibility
 * for itself.
 */
function selectEverything(view: View, count: number) {
  fireEvent(view.getByText("Listing 1"), "longPress");
  if (count > 6) fireEvent.press(view.getByLabelText(`See all ${count} listings`));
  // The long-press already took one row, so clear it first — otherwise Select
  // All's tri-state is being measured from a partial selection.
  fireEvent.press(view.getByText("Listing 1"));
  fireEvent.press(view.getByText(`Select all ${count} shown`));
}

/** Open the confirm sheet from the docked CTA. */
function openSheet(view: View, ctaLabel: string) {
  fireEvent.press(view.getByLabelText(ctaLabel));
}

/** Tap the sheet's own confirm button and let the request settle. */
async function confirm(view: View, label: string) {
  await act(async () => {
    fireEvent.press(view.getByLabelText(label));
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function keysSent(): string[] {
  return mockBatch.mock.calls.map((call) => call[0].idempotencyKey);
}

beforeEach(() => {
  mockLoad.mockReset();
  mockBatch.mockReset();
  mockReducedMotion.mockReturnValue(true);
  mockLoad.mockResolvedValue(result([listing(1, DRAFT), listing(2, DRAFT)]));
  mockBatch.mockResolvedValue(batchResponse([entry(1, "succeeded"), entry(2, "succeeded")]));
});

/* ------------------------------------------------------------------ *
 * §34 — the shape of the outcome, before committing
 * ------------------------------------------------------------------ */

describe("the docked action bar", () => {
  it("is absent until the seller is selecting", async () => {
    const view = await renderScreen();
    expect(view.queryByLabelText(/^Publish \d/)).toBeNull();
  });

  it("puts the count on the button, and the blocked count beside it", async () => {
    // §34 in one string. A button reading "Publish 3" that publishes two is the
    // failure this replaces.
    mockLoad.mockResolvedValue(
      result([listing(1, DRAFT), listing(2, DRAFT), listing(3, DRAFT_UNREADY)])
    );
    const view = await renderScreen();
    selectEverything(view, 3);

    expect(view.getByLabelText("Publish 2 · 1 blocked")).toBeTruthy();
  });

  it("counts an already-live listing as blocked, which no client could know", async () => {
    // The row is `publishable: true`. A screen deriving eligibility from the
    // verdict would put it in the batch and unpublish a live product.
    mockLoad.mockResolvedValue(result([listing(1, DRAFT), listing(2, LIVE)]));
    const view = await renderScreen();
    selectEverything(view, 2);

    expect(view.getByLabelText("Publish 1 · 1 blocked")).toBeTruthy();
  });

  it("re-asks the question for the other action and changes with the answer", async () => {
    // Which action is armed decides which rows are greyed, so the switch is on
    // the bar rather than behind the sheet. Both drafts can be hidden; only the
    // ready one can be published.
    mockLoad.mockResolvedValue(result([listing(1, DRAFT), listing(2, DRAFT_UNREADY)]));
    const view = await renderScreen();
    selectEverything(view, 2);
    expect(view.getByLabelText("Publish 1 · 1 blocked")).toBeTruthy();

    fireEvent.press(view.getByLabelText("Hide"));

    expect(view.getByLabelText("Hide 2")).toBeTruthy();
  });

  it("disables the button when nothing is eligible, and still says why", async () => {
    // A greyed button with no label is a dead end. "Nothing to publish" over two
    // selected rows is information: both of them are blocked.
    mockLoad.mockResolvedValue(result([listing(1, DRAFT_UNREADY), listing(2, DRAFT_UNREADY)]));
    const view = await renderScreen();
    selectEverything(view, 2);

    const cta = view.getByLabelText("Nothing to publish");
    expect(cta.props.accessibilityState.disabled).toBe(true);
  });

  it("names the blocked rows in the sheet, not just how many", async () => {
    mockLoad.mockResolvedValue(result([listing(1, DRAFT), listing(2, DRAFT_UNREADY)]));
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 1 · 1 blocked");

    expect(view.getByText("1 of 2 selected will publish.")).toBeTruthy();
    expect(view.getByText("Staying as they are")).toBeTruthy();
    // The reason is the server's sentence, shown beside the row it is about.
    expect(view.getAllByText("1 thing left").length).toBeGreaterThan(0);
  });
});

/* ------------------------------------------------------------------ *
 * §22 / §23 — one request, one key
 * ------------------------------------------------------------------ */

describe("what goes on the wire", () => {
  it("sends one request for the whole batch", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 2");
    await confirm(view, "Publish listings, 2");

    expect(mockBatch).toHaveBeenCalledTimes(1);
    expect(mockBatch).toHaveBeenCalledWith(
      expect.objectContaining({ action: "publish", listingIds: [1, 2] })
    );
  });

  it("includes the rows the preview called blocked", async () => {
    // The preview is a snapshot and the server re-checks every row at write
    // time. Filtering to `eligible` here would drop a listing the seller fixed a
    // minute ago on another device from work they explicitly asked for.
    mockLoad.mockResolvedValue(
      result([listing(1, DRAFT), listing(2, DRAFT_UNREADY), listing(3, LIVE)])
    );
    mockBatch.mockResolvedValue(
      batchResponse([
        entry(1, "succeeded"),
        entry(2, "blocked", { reason: "1 thing left" }),
        entry(3, "blocked", { reason: "Already published" })
      ])
    );
    const view = await renderScreen();
    selectEverything(view, 3);
    openSheet(view, "Publish 1 · 2 blocked");
    await confirm(view, "Publish listings, 1");

    expect(mockBatch.mock.calls[0][0].listingIds).toEqual([1, 2, 3]);
  });

  it("carries an idempotency key", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 2");
    await confirm(view, "Publish listings, 2");

    expect(mockBatch.mock.calls[0][0].idempotencyKey).toMatch(/^bulk-publish-/);
  });

  it("reuses that key for the retry after a failure", async () => {
    // §23, and the version of it that actually happens: the request timed out,
    // the seller cannot tell whether it landed, and they tap Try again. A fresh
    // key here publishes everything a second time.
    mockBatch.mockRejectedValueOnce(
      new PulseApiError("PulseSoc took too long to respond. Try again.", 504, "request_timeout")
    );
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 2");
    await confirm(view, "Publish listings, 2");
    expect(view.getByText("Couldn't finish")).toBeTruthy();

    await confirm(view, "Try again");

    expect(mockBatch).toHaveBeenCalledTimes(2);
    const [first, second] = keysSent();
    expect(second).toBe(first);
  });

  it("cannot turn a second tap into a second batch", async () => {
    // Whether the press lands at all depends on how fast the disabled state
    // flushes, which is not something to rely on. What must hold either way is
    // that every send of one attempt carries one key, so the server recognises
    // the repeat instead of re-applying it.
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 2");

    await act(async () => {
      fireEvent.press(view.getByLabelText("Publish listings, 2"));
      fireEvent.press(view.getByLabelText("Publish listings, 2"));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(new Set(keysSent()).size).toBe(1);
  });

  it("mints a new key once the seller changes the selection", async () => {
    // The other half of the rule. Two different batches sharing a key means the
    // second one is answered with the first one's results.
    mockLoad.mockResolvedValue(result([listing(1, DRAFT), listing(2, DRAFT), listing(3, DRAFT)]));
    mockBatch.mockResolvedValue(batchResponse([entry(1, "succeeded")]));
    const view = await renderScreen();

    selectEverything(view, 3);
    fireEvent.press(view.getByText("Listing 3"));
    openSheet(view, "Publish 2");
    await confirm(view, "Publish listings, 2");
    fireEvent.press(view.getByLabelText("Done"));

    selectEverything(view, 3);
    openSheet(view, "Publish 3");
    await confirm(view, "Publish listings, 3");

    const [first, second] = keysSent();
    expect(second).not.toBe(first);
  });
});

/* ------------------------------------------------------------------ *
 * §19 — partial success is the normal outcome
 * ------------------------------------------------------------------ */

describe("the result the seller reads", () => {
  const eighteen = Array.from({ length: 18 }, (_, index) =>
    listing(index + 1, index < 14 ? DRAFT : DRAFT_UNREADY)
  );
  const fourteenAndFour = batchResponse([
    ...Array.from({ length: 14 }, (_, index) => entry(index + 1, "succeeded")),
    ...Array.from({ length: 4 }, (_, index) =>
      entry(15 + index, "blocked", { reason: "1 thing left" })
    )
  ]);

  async function publishEighteen() {
    mockLoad.mockResolvedValue(result(eighteen));
    mockBatch.mockResolvedValue(fourteenAndFour);
    const view = await renderScreen();
    selectEverything(view, 18);
    openSheet(view, "Publish 14 · 4 blocked");
    await confirm(view, "Publish listings, 14");
    return view;
  }

  it("says fourteen published and four need attention", async () => {
    const view = await publishEighteen();
    expect(view.getByText("14 products published")).toBeTruthy();
    expect(view.getByText("4 products need attention")).toBeTruthy();
  });

  it("does not report the request count as the success count", async () => {
    const view = await publishEighteen();
    expect(view.queryByText(/18 products published/)).toBeNull();
  });

  it("does not call it a failure either", async () => {
    // The more tempting of the two §19 mistakes: any blocked row turning the
    // whole batch red, so a seller who just published fourteen products is told
    // the operation failed and taps again.
    const view = await publishEighteen();
    expect(view.queryByText("Couldn't finish")).toBeNull();
  });

  it("lists the four, with the reason each one did not move", async () => {
    const view = await publishEighteen();
    // "Submitted for review" is written only by the result face, so this counts
    // the fourteen lines without having to exclude the rows behind the sheet.
    expect(view.getAllByText("Submitted for review").length).toBe(14);
    // The reason is not exclusive to the sheet — it is the same prose the rows
    // already wear, from the same server field — so the floor is four, not an
    // exact count of what the list happens to have rendered underneath.
    expect(view.getAllByText("1 thing left").length).toBeGreaterThanOrEqual(4);
  });
});

/* ------------------------------------------------------------------ *
 * §31 — TAP → REQUEST → BACKEND CHANGE → READ BACK → UI UPDATE
 * ------------------------------------------------------------------ */

describe("the store the seller lands back on", () => {
  it("is reloaded from the server, not patched from the request", async () => {
    mockLoad.mockResolvedValue(result([listing(1, DRAFT), listing(2, DRAFT)]));
    const view = await renderScreen();
    expect(view.getAllByText("Draft — not published").length).toBe(2);
    expect(mockLoad).toHaveBeenCalledTimes(1);

    selectEverything(view, 2);
    openSheet(view, "Publish 2");

    // What the server will say when asked again: both listings now live.
    mockLoad.mockResolvedValue(result([listing(1, LIVE), listing(2, LIVE)]));
    await confirm(view, "Publish listings, 2");

    // The read-back happened before the seller saw the result, so Done lands on
    // a store that already matches it.
    expect(mockLoad).toHaveBeenCalledTimes(2);
    fireEvent.press(view.getByLabelText("Done"));

    expect(view.queryByText("Draft — not published")).toBeNull();
    expect(view.getAllByText("20 in stock").length).toBe(2);
  });

  it("ends selection mode once the result is dismissed", async () => {
    // The selection described rows in the state they were in before the write.
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 2");
    await confirm(view, "Publish listings, 2");
    fireEvent.press(view.getByLabelText("Done"));

    expect(view.queryByText(/Select all/)).toBeNull();
    expect(view.queryByLabelText(/^Publish \d/)).toBeNull();
  });

  it("keeps the selection when the seller cancels instead", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 2");
    fireEvent.press(view.getByLabelText("Cancel"));

    expect(mockBatch).not.toHaveBeenCalled();
    expect(view.getByText("2 selected")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * A request that could not be attempted
 * ------------------------------------------------------------------ */

describe("a batch the server refused", () => {
  it("shows the server's sentence rather than one of its own", async () => {
    // How BATCH_TOO_LARGE reaches the seller as "Select up to 200 listings at a
    // time." without this screen keeping a second copy of the limit.
    mockBatch.mockRejectedValue(
      new PulseApiError("Select up to 200 listings at a time.", 400, "BATCH_TOO_LARGE")
    );
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 2");
    await confirm(view, "Publish listings, 2");

    expect(view.getByText("Select up to 200 listings at a time.")).toBeTruthy();
  });

  it("does not claim nothing changed, because it cannot know that", async () => {
    // A request that timed out may already have published fourteen products.
    // What is true either way is that the retry is safe, so that is what it says.
    mockBatch.mockRejectedValue(new PulseApiError("PulseSoc request failed.", 500, "server_error"));
    const view = await renderScreen();
    selectEverything(view, 2);
    openSheet(view, "Publish 2");
    await confirm(view, "Publish listings, 2");

    expect(view.queryByText(/Nothing was changed/)).toBeNull();
    expect(view.getByText(/won't repeat anything that already went through/)).toBeTruthy();
  });
});

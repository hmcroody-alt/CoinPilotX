/**
 * The bulk reprice on the real screen — §11, §21, §23, §31, §33, §34.
 *
 * Separate from `StoreDashboardBulk.test.tsx` because it is a different journey,
 * not a third action on the same one. Publish and hide are one tap from a
 * selection to a write. A reprice has a rule the seller types, a dry run that
 * answers it, and only then a write — and every joint in that chain is a place a
 * green pair of unit tests can sit either side of a wiring bug.
 *
 * The five it is built to catch:
 *
 * * **The dry run isn't one.** `onPreview` wired to the commit call, so "Preview
 *   changes" reprices the store. `storeBulkRun` cannot see which function the
 *   button reached; only this can.
 * * **The rule never leaves the phone.** A commit sent without `pricing_rule`,
 *   answered `MISSING_PRICING_RULE`, or worse sent with the *draft* read back at
 *   confirm time rather than the rule the seller approved.
 * * **Dollars sent as cents.** `parsePricingRule` converts and
 *   `storeBulkPricing` pins it, but nothing there proves the screen sends the
 *   parsed rule rather than `Number(draft.value)`.
 * * **Try again writes.** The error face is shared between the dry run and the
 *   commit. Pointed at the wrong handler, a seller whose *preview* timed out taps
 *   Try again and reprices their store.
 * * **A rule change reuses the spent key.** Preview 20%, preview 25%, apply: the
 *   server replays the answer it gave the first rule and the sheet shows prices
 *   that were never written.
 *
 * Every price string in here is on the mocked response, never in an expectation
 * this file computed. That is the §21 rule as a test convention: if a number in
 * an assertion were arithmetic rather than a fixture, this file would be the
 * second pricing implementation it exists to forbid.
 */

import React from "react";
import { act, fireEvent, render } from "@testing-library/react-native";

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
  useLogiNexusReducedMotion: () => true
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

/**
 * Two mocks, because the point is which one got called.
 *
 * A single mock standing in for both would make the central assertion of this
 * file unwritable: "Preview changes must not write" is only checkable if the
 * write has its own spy that can be shown never to have been touched.
 */
const mockBatch = jest.fn();
const mockPreview = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  batchMarketplaceSellerListings: (...args: unknown[]) => mockBatch(...args),
  previewMarketplaceSellerBatch: (...args: unknown[]) => mockPreview(...args)
}));

import type {
  MarketplaceBatchPreview,
  MarketplaceBatchPreviewResult,
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

/**
 * Live, approved, priced — the shape a reprice is actually aimed at.
 *
 * Note `bulk_eligibility` carries no `price` key and cannot: what blocks a
 * reprice depends on a rule that does not exist when the list is fetched. A row
 * that looks fully eligible here is exactly the row the server may still call
 * `UNKNOWN_COST`, which is why the counts on the confirm face have to come off
 * the dry run.
 */
const LIVE = {
  status: "active",
  approval_status: "approved",
  readiness: READY,
  bulk_eligibility: { publish: { code: "ALREADY_PUBLISHED", reason: "Already published" }, hide: null }
} as Partial<MarketplaceListing>;

function listing(id: number, over: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id,
    listing_id: id,
    seller_name: "Bright Coffee Co",
    title: `Listing ${id}`,
    price_label: "49.00",
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

function previewEntry(
  listing_id: number,
  outcome: MarketplaceBatchPreviewResult["outcome"],
  over: Partial<MarketplaceBatchPreviewResult> = {}
): MarketplaceBatchPreviewResult {
  return { listing_id, outcome, title: `Listing ${listing_id}`, ...over };
}

function previewResponse(results: MarketplaceBatchPreviewResult[]): MarketplaceBatchPreview {
  return {
    ok: true,
    preview: true,
    action: "price",
    requested_count: results.length,
    eligible_count: results.filter((item) => item.outcome === "would_apply").length,
    blocked_count: results.filter((item) => item.outcome === "blocked").length,
    failed_count: results.filter((item) => item.outcome === "failed").length,
    results
  };
}

function batchResponse(results: MarketplaceBatchResult[]): MarketplaceBatchResponse {
  return {
    ok: true,
    batch_id: "batch-1",
    action: "price",
    requested_count: results.length,
    successful_count: results.filter((item) => item.outcome === "succeeded").length,
    blocked_count: results.filter((item) => item.outcome === "blocked").length,
    failed_count: results.filter((item) => item.outcome === "failed").length,
    results
  };
}

/** Both rows would move, one of them off the storefront. */
const TWO_WOULD_APPLY = previewResponse([
  previewEntry(1, "would_apply", {
    current_price_label: "$49.00",
    price_label: "$58.80",
    returns_to_review: true
  }),
  previewEntry(2, "would_apply", { current_price_label: "$20.00", price_label: "$24.00" })
]);

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

function selectEverything(view: View, count: number) {
  fireEvent(view.getByText("Listing 1"), "longPress");
  if (count > 6) fireEvent.press(view.getByLabelText(`See all ${count} listings`));
  fireEvent.press(view.getByText("Listing 1"));
  fireEvent.press(view.getByText(`Select all ${count} shown`));
}

async function settle(view: View, press: () => void) {
  await act(async () => {
    press();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
  return view;
}

/**
 * Selection → Price → rule face. Stops short of typing a rule, because several
 * tests are about the state of the sheet before there is one.
 */
function openRuleFace(view: View) {
  fireEvent.press(view.getByLabelText("Price"));
  fireEvent.press(view.getByLabelText("Edit pricing"));
}

/** Type a percentage and ask the server what it comes to. */
async function previewPercent(view: View, value: string) {
  fireEvent.changeText(view.getByLabelText("Cost + % value"), value);
  await settle(view, () => fireEvent.press(view.getByLabelText("Preview changes")));
}

/** The whole leg: select, open, type 20%, preview. Leaves the confirm face up. */
async function reachConfirmFace(count = 2) {
  const view = await renderScreen();
  selectEverything(view, count);
  openRuleFace(view);
  await previewPercent(view, "20");
  return view;
}

beforeEach(() => {
  mockLoad.mockReset();
  mockBatch.mockReset();
  mockPreview.mockReset();
  mockLoad.mockResolvedValue(result([listing(1, LIVE), listing(2, LIVE)]));
  mockPreview.mockResolvedValue(TWO_WOULD_APPLY);
  mockBatch.mockResolvedValue(
    batchResponse([
      { listing_id: 1, outcome: "succeeded", price_label: "$58.80", returns_to_review: true },
      { listing_id: 2, outcome: "succeeded", price_label: "$24.00" }
    ])
  );
});

/* ------------------------------------------------------------------ *
 * Getting to the rule — §33, and decision 4 on the docked bar
 * ------------------------------------------------------------------ */

describe("arming a reprice", () => {
  it("offers a Price action with no count on it", async () => {
    // The other two arm a number because the list payload knows their verdict
    // per row. A reprice has no verdict before there is a rule, so the bar says
    // what the tap does instead of guessing at how many it will touch.
    const view = await renderScreen();
    selectEverything(view, 2);

    fireEvent.press(view.getByLabelText("Price"));

    expect(view.getByLabelText("Edit pricing")).toBeTruthy();
    expect(view.queryByLabelText(/^Reprice \d/)).toBeNull();
  });

  it("leaves that button live even though nothing is precomputed-eligible", async () => {
    // The trap this is pinned against: `partition` refuses `price`, so the
    // eligible count is structurally zero. A CTA sharing publish's
    // `eligibleCount === 0` guard would be greyed out forever and the feature
    // unreachable — with every unit test still green.
    const view = await renderScreen();
    selectEverything(view, 2);
    fireEvent.press(view.getByLabelText("Price"));

    const cta = view.getByLabelText("Edit pricing");
    expect(cta.props.accessibilityState.disabled).toBe(false);
  });

  it("opens on the rule, not on a confirmation", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);
    openRuleFace(view);

    expect(view.getByLabelText("Preview changes")).toBeTruthy();
    // §34 as a structural fact: there is no Apply on this face to tap past.
    expect(view.queryByLabelText(/^Review new prices/)).toBeNull();
    expect(view.getByText("2 products selected")).toBeTruthy();
  });

  it("will not preview an empty or malformed rule", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);
    openRuleFace(view);
    expect(view.getByLabelText("Preview changes").props.accessibilityState.disabled).toBe(true);

    fireEvent.changeText(view.getByLabelText("Cost + % value"), "12abc");

    expect(view.getByLabelText("Preview changes").props.accessibilityState.disabled).toBe(true);
    expect(mockPreview).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------------ *
 * §34 — the dry run, and the fact that it is dry
 * ------------------------------------------------------------------ */

describe("what Preview changes actually sends", () => {
  it("asks the preview endpoint and writes nothing", async () => {
    // The one assertion this whole file is for.
    await reachConfirmFace();

    expect(mockPreview).toHaveBeenCalledTimes(1);
    expect(mockBatch).not.toHaveBeenCalled();
  });

  it("sends the rule the seller typed, over every selected row", async () => {
    await reachConfirmFace();

    expect(mockPreview).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "price",
        listingIds: [1, 2],
        pricingRule: { type: "COST_PLUS_PERCENT", value: 20 }
      })
    );
  });

  it("sends a fixed amount in minor units", async () => {
    // The failure that is invisible everywhere else: `5` instead of `500` is a
    // five-cent markup, which no validation refuses and no response flags. The
    // batch succeeds and the seller finds out from their margin.
    const view = await renderScreen();
    selectEverything(view, 2);
    openRuleFace(view);
    fireEvent.press(view.getByLabelText("Cost + amount"));
    fireEvent.changeText(view.getByLabelText("Cost + amount value"), "5");
    await settle(view, () => fireEvent.press(view.getByLabelText("Preview changes")));

    expect(mockPreview.mock.calls[0][0].pricingRule).toEqual({
      type: "COST_PLUS_FIXED",
      value: 500
    });
  });

  it("does not spend the commit's idempotency key on the dry run", async () => {
    // A key the server has already been asked under one rule is answered by
    // replay, not by reading the payload. The preview therefore carries its own.
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));

    expect(mockPreview.mock.calls[0][0].idempotencyKey).not.toBe(
      mockBatch.mock.calls[0][0].idempotencyKey
    );
    expect(mockBatch.mock.calls[0][0].idempotencyKey).toMatch(/^bulk-price-/);
  });
});

/* ------------------------------------------------------------------ *
 * §21 / §34 — the numbers on the confirm face are the server's
 * ------------------------------------------------------------------ */

describe("the prices the seller reads before committing", () => {
  it("shows the move as the server's two labels, not a computed one", async () => {
    const view = await reachConfirmFace();

    expect(view.getByText("$49.00 → $58.80")).toBeTruthy();
    expect(view.getByText("$20.00 → $24.00")).toBeTruthy();
  });

  it("renders an absent current price as an absence, not as $0.00", async () => {
    // §11. A listing with no price has *no* price, and an arrow starting at
    // "$0.00" or "Free" would invent the fact the rule forbids inventing.
    mockPreview.mockResolvedValue(
      previewResponse([
        previewEntry(1, "would_apply", { current_price_label: "", price_label: "$12.00" }),
        previewEntry(2, "would_apply", { current_price_label: "$20.00", price_label: "$24.00" })
      ])
    );
    const view = await reachConfirmFace();

    expect(view.getByText("Set to $12.00")).toBeTruthy();
    expect(view.queryByText(/\$0\.00 →/)).toBeNull();
    expect(view.queryByText(/Free →/)).toBeNull();
  });

  it("warns per row that a live product goes back to review", async () => {
    // `price_label` is a material field. The seller repricing their storefront
    // is entitled to know which rows leave it *before* the tap — and it is not
    // true of all of them, so it cannot be said once in the subtitle.
    const view = await reachConfirmFace();

    expect(view.getAllByText("Goes back to review").length).toBe(1);
  });

  it("lists the rows the rule cannot price, with the server's reason", async () => {
    // The case a purely merchant-authored store hits on every row: no supplier
    // cost, so no cost-derived price. A blocked row is a task, not an error.
    mockPreview.mockResolvedValue(
      previewResponse([
        previewEntry(1, "would_apply", { current_price_label: "$49.00", price_label: "$58.80" }),
        previewEntry(2, "blocked", {
          reason: "No supplier cost on file",
          error_code: "UNKNOWN_COST"
        })
      ])
    );
    const view = await reachConfirmFace();

    expect(view.getByText("Staying as they are")).toBeTruthy();
    expect(view.getByText("No supplier cost on file")).toBeTruthy();
    expect(view.getByText("Reprice 1 · 1 blocked")).toBeTruthy();
  });

  it("will not let a reprice be confirmed when nothing would move", async () => {
    mockPreview.mockResolvedValue(
      previewResponse([
        previewEntry(1, "blocked", { reason: "No supplier cost on file" }),
        previewEntry(2, "blocked", { reason: "No supplier cost on file" })
      ])
    );
    const view = await reachConfirmFace();

    const cta = view.getByLabelText("Nothing to reprice");
    expect(cta.props.accessibilityState.disabled).toBe(true);
  });

  it("does not borrow publish's vocabulary for a batch that wrote nothing", async () => {
    // The reason `would_apply` is not `succeeded`. A preview rendered through
    // the result face would say "2 products repriced" over two untouched rows.
    const view = await reachConfirmFace();

    expect(view.queryByText(/products repriced/)).toBeNull();
    expect(view.queryByLabelText("Done")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * §23 / §31 — committing what was reviewed
 * ------------------------------------------------------------------ */

describe("what the commit carries", () => {
  it("sends the reviewed rule, not the field as it stands now", async () => {
    // The draft is live and the review is frozen. A seller who reviews 20%, then
    // idly retypes the field before tapping Apply, must get the batch they read.
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));

    expect(mockBatch).toHaveBeenCalledWith(
      expect.objectContaining({
        action: "price",
        listingIds: [1, 2],
        pricingRule: { type: "COST_PLUS_PERCENT", value: 20 }
      })
    );
  });

  it("includes the rows the dry run called blocked", async () => {
    // The preview is a snapshot; the server re-checks each row at write time.
    // Filtering here would drop a row whose cost arrived in the last minute.
    mockPreview.mockResolvedValue(
      previewResponse([
        previewEntry(1, "would_apply", { current_price_label: "$49.00", price_label: "$58.80" }),
        previewEntry(2, "blocked", { reason: "No supplier cost on file" })
      ])
    );
    mockBatch.mockResolvedValue(
      batchResponse([
        { listing_id: 1, outcome: "succeeded", price_label: "$58.80" },
        { listing_id: 2, outcome: "blocked", reason: "No supplier cost on file" }
      ])
    );
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 1")));

    expect(mockBatch.mock.calls[0][0].listingIds).toEqual([1, 2]);
  });

  it("reuses the key for a retry after the write failed", async () => {
    mockBatch.mockRejectedValueOnce(
      new PulseApiError("PulseSoc took too long to respond. Try again.", 504, "request_timeout")
    );
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));
    expect(view.getByText("Couldn't finish")).toBeTruthy();

    await settle(view, () => fireEvent.press(view.getByLabelText("Try again")));

    expect(mockBatch).toHaveBeenCalledTimes(2);
    const [first, second] = mockBatch.mock.calls.map((call) => call[0].idempotencyKey);
    expect(second).toBe(first);
  });
});

/* ------------------------------------------------------------------ *
 * Going back to the rule — the loop §33's "Review Changes" implies
 * ------------------------------------------------------------------ */

describe("changing the rule after reading the review", () => {
  it("returns to the rule with the number still in the field", async () => {
    // Without this the only way from "$49.00 → $58.80" to a different percentage
    // was Cancel, which drops the sheet, and then retyping the rule from scratch.
    const view = await reachConfirmFace();

    fireEvent.press(view.getByLabelText("Change rule"));

    expect(view.getByLabelText("Cost + % value").props.value).toBe("20");
    // The old rule's prices are gone: a review left on screen while the seller
    // edits the number that produced it is a preview of something else.
    expect(view.queryByText("$49.00 → $58.80")).toBeNull();
  });

  it("offers the same way back from a rule the server refused", async () => {
    // The dead end this closes. "Try again" on an INVALID_PRICING_RULE is the
    // identical refusal, so without a way back to the field the seller's only
    // option was to abandon the selection.
    mockPreview.mockRejectedValue(
      new PulseApiError("That pricing rule isn't supported.", 400, "INVALID_PRICING_RULE")
    );
    const view = await renderScreen();
    selectEverything(view, 2);
    openRuleFace(view);
    await previewPercent(view, "20");

    fireEvent.press(view.getByLabelText("Change rule"));

    expect(view.getByLabelText("Preview changes")).toBeTruthy();
    expect(view.queryByText("Couldn't finish")).toBeNull();
  });

  it("previews the second rule without committing the first", async () => {
    const view = await reachConfirmFace();
    fireEvent.press(view.getByLabelText("Change rule"));
    await previewPercent(view, "25");

    expect(mockPreview).toHaveBeenCalledTimes(2);
    expect(mockPreview.mock.calls[1][0].pricingRule).toEqual({
      type: "COST_PLUS_PERCENT",
      value: 25
    });
    expect(mockBatch).not.toHaveBeenCalled();
  });

  it("does not commit a second rule under the first rule's key", async () => {
    // §23's other half, on the flow that makes it reachable: the seller applies
    // 20%, the write fails, they go back and change the rule to 25%, and apply.
    // Both batches are "price on [1,2]" and only the rule differs — so a key over
    // (action, ids) alone would be reused, and a key the server has already
    // claimed is answered by replaying the 20% batch while the sheet shows the
    // 25% prices. The seller reads a confirmation for prices that were never
    // written.
    //
    // Two mechanisms hold this, deliberately: `previewBulk` drops the held key
    // when a new review arrives, and `isSameAttempt` compares the rule. Either
    // alone passes this test — removing both is what fails it. That is the
    // assertion being made on purpose, since what matters is that a second rule
    // cannot inherit the first one's key, not which line prevents it.
    mockBatch.mockRejectedValueOnce(new PulseApiError("PulseSoc request failed.", 500, "server_error"));
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));
    expect(view.getByText("Couldn't finish")).toBeTruthy();

    fireEvent.press(view.getByLabelText("Change rule"));
    await previewPercent(view, "25");
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));

    const [first, second] = mockBatch.mock.calls.map((call) => call[0].idempotencyKey);
    expect(mockBatch.mock.calls[1][0].pricingRule).toEqual({
      type: "COST_PLUS_PERCENT",
      value: 25
    });
    expect(second).not.toBe(first);
  });

  it("is not offered for publish or hide, which have no rule", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);
    fireEvent.press(view.getByLabelText("Hide"));
    fireEvent.press(view.getByLabelText("Hide 2"));

    expect(view.queryByLabelText("Change rule")).toBeNull();
    expect(view.getByLabelText("Cancel")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * The shared error face, and the write hiding behind it
 * ------------------------------------------------------------------ */

describe("a dry run that failed", () => {
  it("does not turn Try again into a reprice", async () => {
    // One error face serves both requests. Wired to the commit handler, a seller
    // whose *preview* timed out taps Try again and reprices their whole store —
    // having never seen a single new price.
    mockPreview.mockRejectedValue(
      new PulseApiError("PulseSoc took too long to respond. Try again.", 504, "request_timeout")
    );
    const view = await renderScreen();
    selectEverything(view, 2);
    openRuleFace(view);
    await previewPercent(view, "20");
    expect(view.getByText("Couldn't finish")).toBeTruthy();

    await settle(view, () => fireEvent.press(view.getByLabelText("Try again")));

    expect(mockBatch).not.toHaveBeenCalled();
    expect(mockPreview).toHaveBeenCalledTimes(2);
  });

  it("shows the server's refusal rather than one of its own", async () => {
    mockPreview.mockRejectedValue(
      new PulseApiError("That pricing rule isn't supported.", 400, "INVALID_PRICING_RULE")
    );
    const view = await renderScreen();
    selectEverything(view, 2);
    openRuleFace(view);
    await previewPercent(view, "20");

    expect(view.getByText("That pricing rule isn't supported.")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * §19 / §31 — reading back what happened
 * ------------------------------------------------------------------ */

describe("the result of a reprice", () => {
  it("reads back the stored price per row, not the word done", async () => {
    // §31's last step. A number the seller can check against the list behind the
    // sheet is the only version of READ BACK that proves anything.
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));

    expect(view.getByText("2 products repriced")).toBeTruthy();
    expect(view.getByText("$58.80")).toBeTruthy();
    expect(view.getByText("$24.00")).toBeTruthy();
  });

  it("says repriced, not published", async () => {
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));

    expect(view.queryByText(/products published/)).toBeNull();
    expect(view.queryByText("Submitted for review")).toBeNull();
  });

  it("repeats the re-review warning after the write, from the server's own field", async () => {
    // The seller who scrolled past the warning on the confirm face still has to
    // learn that a product left the storefront. Same field name both sides, so
    // the phone never derives it from `status == "pending_review"` — which would
    // be wrong for a listing that was already in the queue beforehand.
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));

    expect(view.getByText("Back in review")).toBeTruthy();
  });

  it("counts a partial reprice honestly", async () => {
    mockPreview.mockResolvedValue(
      previewResponse([
        previewEntry(1, "would_apply", { current_price_label: "$49.00", price_label: "$58.80" }),
        previewEntry(2, "blocked", { reason: "No supplier cost on file" })
      ])
    );
    mockBatch.mockResolvedValue(
      batchResponse([
        { listing_id: 1, outcome: "succeeded", price_label: "$58.80" },
        { listing_id: 2, outcome: "blocked", reason: "No supplier cost on file" }
      ])
    );
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 1")));

    expect(view.getByText("1 product repriced")).toBeTruthy();
    expect(view.getByText("1 product needs attention")).toBeTruthy();
    expect(view.queryByText("Couldn't finish")).toBeNull();
  });

  it("reloads the store from the server before the seller lands back on it", async () => {
    const view = await reachConfirmFace();
    expect(mockLoad).toHaveBeenCalledTimes(1);

    // What the server will say when asked again.
    mockLoad.mockResolvedValue(result([listing(1, { ...LIVE, price_label: "58.80" }), listing(2, LIVE)]));
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new prices, 2")));

    expect(mockLoad).toHaveBeenCalledTimes(2);
    fireEvent.press(view.getByLabelText("Done"));
    expect(view.queryByText(/Select all/)).toBeNull();
  });
});

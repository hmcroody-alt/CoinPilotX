/**
 * The bulk category move on the real screen — §21, §23, §31, §33, §34.
 *
 * The sibling of `StoreDashboardPrice.test.tsx`, and it exists for the same
 * reason that one does: the module tests either side of this wiring are green
 * whether or not the screen connects them. `storeBulkCategory` can prove it
 * normalises a draft, `listing_batch` can prove it clears a subcategory, and
 * neither can see that the button labelled "Preview changes" reached the commit
 * endpoint.
 *
 * What it is built to catch, beyond the price file's five:
 *
 * * **The wrong draft sent.** Two payload actions now share one `payload` field,
 *   one `previewBulk`, and one `rule` phase. A category preview that sends
 *   `pricing_rule` is answered `INVALID_PRICING_RULE` and reads to the seller as
 *   "that aisle is not valid".
 * * **The category face never renders.** The `rule` phase branches on the action;
 *   pointed at the price component, a seller who taps "Set category" is asked for
 *   a percentage.
 * * **The move is rendered as a price move.** `changeDetail` is keyed by action,
 *   and a category entry carries no `price_label` — so the wrong key means every
 *   changing row loses its detail, and the confirm face filters rows *by*
 *   `detail`, which hides them entirely.
 * * **The subcategory is not cleared.** The seller's whole selection ends up
 *   filed under a parent it was moved into and a child from the aisle it left.
 * * **"Change category" goes back to the pricing rule.** One shared footer, one
 *   shared phase.
 *
 * Every category string asserted here is on the mocked response, not computed by
 * this file — the same §21 convention the price file keeps about numbers.
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

/** Two spies, so "Preview changes must not write" is a writable assertion. */
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
 * Live, approved, and filed somewhere — the shape a move is aimed at, and the
 * shape whose move costs a moderation cycle.
 *
 * `bulk_eligibility` carries no `category` key and cannot, for the reason it
 * carries no `price` key: what blocks a move depends on an aisle that does not
 * exist when the list is fetched.
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
    price_label: "49.00",
    currency: "USD",
    quantity: 20,
    category: "Education",
    subcategory: "Crypto Basics",
    ...LIVE,
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
    action: "category",
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
    batch_id: "batch-c1",
    action: "category",
    requested_count: results.length,
    successful_count: results.filter((item) => item.outcome === "succeeded").length,
    blocked_count: results.filter((item) => item.outcome === "blocked").length,
    failed_count: results.filter((item) => item.outcome === "failed").length,
    results
  };
}

/** Both rows would move out of Education; one of them off the storefront. */
const TWO_WOULD_APPLY = previewResponse([
  previewEntry(1, "would_apply", {
    current_category: "Education",
    current_subcategory: "Crypto Basics",
    category: "Home & Kitchen",
    subcategory: "",
    returns_to_review: true
  }),
  previewEntry(2, "would_apply", {
    current_category: "Education",
    current_subcategory: "Crypto Basics",
    category: "Home & Kitchen",
    subcategory: ""
  })
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

/** Selection → Category → the picker face. No aisle chosen yet. */
function openCategoryFace(view: View) {
  fireEvent.press(view.getByLabelText("Category"));
  fireEvent.press(view.getByLabelText("Set category"));
}

async function previewCategory(view: View, name: string, sub?: string) {
  fireEvent.changeText(view.getByLabelText("New category"), name);
  if (sub !== undefined) {
    fireEvent.changeText(view.getByLabelText("New subcategory, optional"), sub);
  }
  await settle(view, () => fireEvent.press(view.getByLabelText("Preview changes")));
}

/** The whole leg: select two, open, type an aisle, preview. Leaves confirm up. */
async function reachConfirmFace() {
  const view = await renderScreen();
  selectEverything(view, 2);
  openCategoryFace(view);
  await previewCategory(view, "Home & Kitchen");
  return view;
}

beforeEach(() => {
  mockLoad.mockReset();
  mockBatch.mockReset();
  mockPreview.mockReset();
  mockLoad.mockResolvedValue(result([listing(1), listing(2)]));
  mockPreview.mockResolvedValue(TWO_WOULD_APPLY);
  mockBatch.mockResolvedValue(
    batchResponse([
      {
        listing_id: 1,
        outcome: "succeeded",
        category: "Home & Kitchen",
        subcategory: "",
        returns_to_review: true
      },
      { listing_id: 2, outcome: "succeeded", category: "Home & Kitchen", subcategory: "" }
    ])
  );
});

/* ------------------------------------------------------------------ *
 * Arming the move
 * ------------------------------------------------------------------ */

describe("arming a category move", () => {
  it("offers a Category action with no count on it", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);

    fireEvent.press(view.getByLabelText("Category"));

    expect(view.getByLabelText("Set category")).toBeTruthy();
    expect(view.queryByLabelText(/^Move \d/)).toBeNull();
  });

  it("leaves that button live even though nothing is precomputed-eligible", async () => {
    // The same trap the price CTA is pinned against: `partition` refuses a
    // payload action, so the eligible count is structurally zero. Sharing
    // publish's `eligibleCount === 0` guard would grey the button out forever
    // and make the feature unreachable with every unit test still green.
    const view = await renderScreen();
    selectEverything(view, 2);
    fireEvent.press(view.getByLabelText("Category"));

    expect(view.getByLabelText("Set category").props.accessibilityState.disabled).toBe(false);
  });

  it("opens the category face and not the pricing rule", async () => {
    // The `rule` phase now serves two actions. Pointed at the wrong component, a
    // seller who tapped "Set category" is asked for a percentage.
    const view = await renderScreen();
    selectEverything(view, 2);
    openCategoryFace(view);

    expect(view.getByLabelText("New category")).toBeTruthy();
    expect(view.queryByLabelText("Cost + % value")).toBeNull();
  });

  it("suggests the aisles the store already uses", async () => {
    // Free text invites "Home & Kitchen" beside "Home and Kitchen", which no
    // filter can join. The chips are how a store stays spelled one way.
    const view = await renderScreen();
    selectEverything(view, 2);
    openCategoryFace(view);

    expect(view.getByLabelText("Move to Education")).toBeTruthy();
  });

  it("clears the child when a suggestion sets the parent", async () => {
    // The subcategory field belongs to whichever parent was in the box when it
    // was typed. Tapping a different parent and leaving "Crypto Basics" behind
    // builds the incoherent pair by hand — "Home & Kitchen / Crypto Basics" —
    // which is the exact filing that clearing the child on a move exists to
    // prevent, arrived at through the control that was meant to be the safe one.
    const view = await renderScreen();
    selectEverything(view, 2);
    openCategoryFace(view);
    fireEvent.changeText(view.getByLabelText("New category"), "Home & Kitchen");
    fireEvent.changeText(view.getByLabelText("New subcategory, optional"), "Crypto Basics");

    fireEvent.press(view.getByLabelText("Move to Education"));

    expect(view.getByLabelText("New category").props.value).toBe("Education");
    expect(view.getByLabelText("New subcategory, optional").props.value).toBe("");
  });

  it("sends the cleared child, so a chip does not leave the old one in place", async () => {
    // The same guarantee at the wire, because the field could be cleared on
    // screen and the stale value still sent from a draft the picker did not own.
    const view = await renderScreen();
    selectEverything(view, 2);
    openCategoryFace(view);
    fireEvent.changeText(view.getByLabelText("New subcategory, optional"), "Crypto Basics");
    fireEvent.press(view.getByLabelText("Move to Education"));

    await settle(view, () => fireEvent.press(view.getByLabelText("Preview changes")));

    expect(mockPreview.mock.calls[0][0].categoryTarget).toEqual({
      category: "Education",
      subcategory: ""
    });
  });

  it("will not preview until an aisle is chosen", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);
    openCategoryFace(view);

    expect(view.getByLabelText("Preview changes").props.accessibilityState.disabled).toBe(true);
    expect(mockPreview).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------------ *
 * §34 — the dry run
 * ------------------------------------------------------------------ */

describe("previewing a move", () => {
  it("asks the server and writes nothing", async () => {
    await reachConfirmFace();

    expect(mockPreview).toHaveBeenCalledTimes(1);
    expect(mockBatch).not.toHaveBeenCalled();
  });

  it("sends the category, not a pricing rule", async () => {
    // The wiring bug this file exists for: one `previewBulk` serving two payload
    // actions. Sending `pricing_rule` here is answered `INVALID_PRICING_RULE`,
    // which the seller reads as "that aisle is not valid".
    await reachConfirmFace();

    const sent = mockPreview.mock.calls[0][0];
    expect(sent.action).toBe("category");
    expect(sent.categoryTarget).toEqual({ category: "Home & Kitchen", subcategory: "" });
    expect(sent.pricingRule).toBeUndefined();
  });

  it("sends a subcategory the seller typed", async () => {
    const view = await renderScreen();
    selectEverything(view, 2);
    openCategoryFace(view);
    await previewCategory(view, "Home & Kitchen", "Lighting");

    expect(mockPreview.mock.calls[0][0].categoryTarget).toEqual({
      category: "Home & Kitchen",
      subcategory: "Lighting"
    });
  });

  it("sends every selected id, including ones it expects to be blocked", async () => {
    await reachConfirmFace();

    expect(mockPreview.mock.calls[0][0].listingIds).toEqual([1, 2]);
  });

  it("shows the move the server described, per row", async () => {
    // Off the response, not computed here. A row's detail is what makes it
    // visible on the confirm face at all — the face filters changing rows by
    // `detail`, so reading the wrong key hides the whole selection.
    const view = await reachConfirmFace();

    // Both selected rows, not one: a face that rendered the move once as a
    // summary line would pass a `getByText` and still be hiding a row.
    expect(view.getAllByText("Education / Crypto Basics → Home & Kitchen")).toHaveLength(2);
  });

  it("warns that a live product goes back to review", async () => {
    const view = await reachConfirmFace();

    expect(view.getByText("Goes back to review")).toBeTruthy();
  });

  it("names the move on the confirm button", async () => {
    const view = await reachConfirmFace();

    expect(view.getByText("Move 2")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * §31 — the commit
 * ------------------------------------------------------------------ */

describe("applying a move", () => {
  it("sends the category the seller approved, then reads the store back", async () => {
    const view = await reachConfirmFace();

    await settle(view, () => fireEvent.press(view.getByLabelText("Review new categories, 2")));

    const sent = mockBatch.mock.calls[0][0];
    expect(sent.action).toBe("category");
    expect(sent.categoryTarget).toEqual({ category: "Home & Kitchen", subcategory: "" });
    expect(sent.pricingRule).toBeUndefined();
    // §31's last step: the list is re-read from the server, not patched locally.
    expect(mockLoad.mock.calls.length).toBeGreaterThan(1);
  });

  it("cannot be re-aimed from the confirm face, because the field is not on it", async () => {
    // The draft is live state; the review is a frozen answer about one target.
    // If the field were still reachable behind the confirm face, a seller could
    // type "Garden", tap Move 2, and re-file their store into an aisle whose
    // per-row consequences they never saw — the preview on screen would be
    // describing a different move than the one about to be written.
    //
    // The guarantee is structural rather than defensive: the phase swaps the
    // field out, so there is nothing to edit. Asserted as the *absence* of the
    // control for that reason. Editing the target is still possible, but only
    // through "Change category", which discards the review and re-previews —
    // see the idempotency test above.
    const view = await reachConfirmFace();

    expect(view.queryByLabelText("New category")).toBeNull();
    expect(view.queryByLabelText("New subcategory, optional")).toBeNull();

    await settle(view, () => fireEvent.press(view.getByLabelText("Review new categories, 2")));

    expect(mockBatch.mock.calls[0][0].categoryTarget.category).toBe("Home & Kitchen");
  });

  it("reads the stored filing back on each row, not the word done", async () => {
    const view = await reachConfirmFace();

    await settle(view, () => fireEvent.press(view.getByLabelText("Review new categories, 2")));

    expect(view.getByText("2 products moved")).toBeTruthy();
    // Twice: both rows read back the aisle the server says it stored.
    expect(view.getAllByText("Home & Kitchen")).toHaveLength(2);
  });

  it("still says which product left the storefront", async () => {
    // On the result face, not only the preview: a seller who scrolled past the
    // warning has to learn about it from somewhere.
    const view = await reachConfirmFace();

    await settle(view, () => fireEvent.press(view.getByLabelText("Review new categories, 2")));

    expect(view.getByText("Back in review")).toBeTruthy();
  });

  it("reports the rows the server would not move as tasks, not errors", async () => {
    mockPreview.mockResolvedValue(
      previewResponse([
        previewEntry(1, "would_apply", {
          current_category: "Education",
          category: "Home & Kitchen",
          subcategory: ""
        }),
        previewEntry(2, "blocked", {
          error_code: "CATEGORY_UNCHANGED",
          reason: "Already in that category"
        })
      ])
    );
    const view = await renderScreen();
    selectEverything(view, 2);
    openCategoryFace(view);
    await previewCategory(view, "Home & Kitchen");

    expect(view.getByText("Already in that category")).toBeTruthy();
    expect(view.getByText("Move 1 · 1 blocked")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * §23 — one attempt, one key
 * ------------------------------------------------------------------ */

describe("idempotency", () => {
  it("uses one key for a double tap", async () => {
    const view = await reachConfirmFace();
    const confirm = view.getByLabelText("Review new categories, 2");

    await settle(view, () => {
      fireEvent.press(confirm);
      fireEvent.press(confirm);
    });

    const keys = new Set(mockBatch.mock.calls.map((call) => call[0].idempotencyKey));
    expect(keys.size).toBe(1);
  });

  it("mints a new key once the seller picks a different aisle", async () => {
    // The failure: a spent key is answered by replay rather than by reading the
    // payload, so reusing it across a category change would show the seller a
    // confirmation for a move into the *first* aisle.
    const view = await reachConfirmFace();
    fireEvent.press(view.getByLabelText("Change category"));
    await previewCategory(view, "Garden");
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new categories, 2")));

    const firstPreviewKey = mockPreview.mock.calls[0][0].idempotencyKey;
    const secondPreviewKey = mockPreview.mock.calls[1][0].idempotencyKey;
    expect(secondPreviewKey).not.toBe(firstPreviewKey);
    expect(mockBatch.mock.calls[0][0].categoryTarget.category).toBe("Garden");
  });

  it("does not spend the commit key on a dry run", async () => {
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new categories, 2")));

    expect(mockBatch.mock.calls[0][0].idempotencyKey).not.toBe(
      mockPreview.mock.calls[0][0].idempotencyKey
    );
  });
});

/* ------------------------------------------------------------------ *
 * Going back, and failing
 * ------------------------------------------------------------------ */

describe("changing your mind", () => {
  it("goes back to the category face, keeping what was chosen", async () => {
    const view = await reachConfirmFace();

    fireEvent.press(view.getByLabelText("Change category"));

    expect(view.getByLabelText("New category").props.value).toBe("Home & Kitchen");
    expect(view.queryByText("Education / Crypto Basics → Home & Kitchen")).toBeNull();
  });

  it("does not offer that button on a publish sheet", async () => {
    // There is no face behind publish to go back to, so the button would land
    // the seller somewhere that action never had.
    const view = await renderScreen();
    selectEverything(view, 2);
    fireEvent.press(view.getByLabelText("Hide"));
    fireEvent.press(view.getByLabelText(/^Hide 2/));

    expect(view.queryByLabelText("Change category")).toBeNull();
    expect(view.queryByLabelText("Change rule")).toBeNull();
  });

  it("retries the dry run, not the write, when the preview failed", async () => {
    // The error face is shared. Pointed at the commit, a seller whose *preview*
    // timed out taps Try again and moves their whole store.
    mockPreview.mockRejectedValueOnce(new PulseApiError("Network request failed", 0, "network_error"));
    const view = await renderScreen();
    selectEverything(view, 2);
    openCategoryFace(view);
    await previewCategory(view, "Home & Kitchen");

    await settle(view, () => fireEvent.press(view.getByLabelText("Try again")));

    expect(mockPreview).toHaveBeenCalledTimes(2);
    expect(mockBatch).not.toHaveBeenCalled();
  });

  it("retries the write with the same key when the commit failed", async () => {
    mockBatch.mockRejectedValueOnce(new PulseApiError("Network request failed", 0, "network_error"));
    const view = await reachConfirmFace();
    await settle(view, () => fireEvent.press(view.getByLabelText("Review new categories, 2")));

    await settle(view, () => fireEvent.press(view.getByLabelText("Try again")));

    expect(mockBatch).toHaveBeenCalledTimes(2);
    expect(mockBatch.mock.calls[1][0].idempotencyKey).toBe(
      mockBatch.mock.calls[0][0].idempotencyKey
    );
  });
});

/**
 * §32, on the screen rather than in a module.
 *
 * The single-product journey is: open a draft, read what is left, tap a blocker,
 * land on the field that fixes it, preview it as a buyer, publish, and see the
 * listing come back changed. Every one of those steps crosses a boundary the
 * pure tests cannot see — a ref that has not flushed, a prop spelled wrong, a
 * response merged over stale state — and the bulk path already proved that is
 * where the real defects live: `StoreDashboardBulk` found a double-tap minting
 * two idempotency keys that every module test agreed was impossible.
 *
 * So these render the actual screen and press the actual buttons.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

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
const mockInvalidate = jest.fn().mockResolvedValue(undefined);
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined),
  invalidateNativeSync: (...args: unknown[]) => mockInvalidate(...args)
}));
jest.mock("../../components/NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn(() => null)
}));

const mockSnapshot = jest.fn();
const mockCommercialTerms = jest.fn();
const mockUpdate = jest.fn();
const mockSubmit = jest.fn();
jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  loadSellerStoreSnapshot: (...args: unknown[]) => mockSnapshot(...args),
  getMarketplaceCommercialTerms: (...args: unknown[]) => mockCommercialTerms(...args),
  loadCachedSellerStore: jest.fn().mockResolvedValue(null),
  updateMarketplaceSellerListing: (...args: unknown[]) => mockUpdate(...args),
  submitMarketplaceSellerListing: (...args: unknown[]) => mockSubmit(...args)
}));

import { SellerStoreScreen } from "../SellerStoreScreen";
import { activateLocale } from "../../i18n/engine";

/* ------------------------------------------------------------------ *
 * Fixtures — shaped exactly like `pulse_marketplace_seller_listing_payload`
 * ------------------------------------------------------------------ */

const READY = {
  publishable: true,
  checkout_ready: true,
  blockers: [],
  warnings: [],
  summary: "Ready to publish",
  fixes: [],
  notes: []
};

/** Two blockers in two different sections, so the routing has somewhere to go. */
const UNREADY = {
  publishable: false,
  checkout_ready: false,
  blockers: ["MISSING_PRICE", "NO_VALID_MEDIA"],
  warnings: [],
  summary: "2 things left",
  fixes: [
    { code: "MISSING_PRICE", label: "Add price", section: "pricing" },
    { code: "NO_VALID_MEDIA", label: "Add photo", section: "media" }
  ],
  notes: []
};

/** Finished and publishable, and still nobody can buy it. §7's other half. */
const PUBLISHABLE_BUT_UNBUYABLE = {
  publishable: true,
  checkout_ready: false,
  blockers: [],
  warnings: ["UNKNOWN_INVENTORY"],
  summary: "Ready to publish",
  fixes: [],
  notes: [{ code: "UNKNOWN_INVENTORY", label: "Set stock count", section: "inventory" }]
};

function listing(over: Record<string, unknown> = {}) {
  return {
    id: 42,
    title: "Roasted Beans",
    short_description: "One kilo bag",
    description: "Single origin, roasted weekly.",
    category: "Food",
    price_label: "",
    quantity: 5,
    status: "draft",
    approval_status: "draft",
    readiness: UNREADY,
    bulk_eligibility: {
      publish: { code: "NOT_READY", reason: "2 things left", blockers: UNREADY.blockers },
      hide: null
    },
    ...over
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockSnapshot.mockResolvedValue({ live: true, listings: [listing()], orders: [] });
  mockCommercialTerms.mockResolvedValue({ terms: { acceptance: null } });
  mockUpdate.mockResolvedValue({ listing: listing(), message: "Listing updated." });
});

beforeAll(async () => {
  await activateLocale("en");
});

async function openEditor(rows?: Record<string, unknown>[]) {
  if (rows) mockSnapshot.mockResolvedValue({ live: true, listings: rows, orders: [] });
  const navigation = { navigate: jest.fn() };
  const view = render(
    <SellerStoreScreen route={{ params: { mode: "create", listingId: 42 } as never }} navigation={navigation} />
  );
  await waitFor(() => expect(mockSnapshot).toHaveBeenCalled());
  await waitFor(() => expect(mockCommercialTerms).toHaveBeenCalled());
  await waitFor(() => expect(view.getByText("Ready to sell")).toBeTruthy());
  return { ...view, navigation };
}

/* ------------------------------------------------------------------ *
 * §7 / §32: what is left, in the server's words
 * ------------------------------------------------------------------ */

describe("the Ready to Sell panel", () => {
  it("names the exact work left, not just that it is a draft", async () => {
    const view = await openEditor();
    // Twice on purpose: once as the panel's summary, once as the label of the
    // button that will not fire. A seller who reads only the button still gets
    // the count.
    expect(view.getAllByText("2 things left").length).toBe(2);
    expect(view.getByText("Add price")).toBeTruthy();
    expect(view.getByText("Add photo")).toBeTruthy();
  });

  it("says nothing is left when nothing is", async () => {
    const view = await openEditor([
      listing({ readiness: READY, bulk_eligibility: { publish: null, hide: null } })
    ]);
    expect(view.getByText("Ready to publish")).toBeTruthy();
    expect(view.queryByText("Add price")).toBeNull();
  });

  it("speaks up about a listing that publishes and cannot be bought", async () => {
    // The case an empty fix list hides. `publishable` is true, so a card that
    // only rendered blockers would show a clean bill of health above a live
    // Publish button — and the seller would ship a product with no checkout.
    const view = await openEditor([
      listing({
        quantity: null,
        readiness: PUBLISHABLE_BUT_UNBUYABLE,
        bulk_eligibility: { publish: null, hide: null }
      })
    ]);
    expect(view.getByText("Won't stop you publishing, but nobody can buy it yet")).toBeTruthy();
    expect(view.getByText("Set stock count")).toBeTruthy();
  });

  it("admits it was not told rather than showing an empty list", async () => {
    // A payload with no verdict. Absence is not a clean bill of health, so the
    // card says so and Publish stays shut.
    const view = await openEditor([
      listing({ readiness: undefined, bulk_eligibility: undefined })
    ]);
    expect(view.getByText(/haven't heard back about this listing yet/)).toBeTruthy();
    expect(view.getByLabelText("Not checked yet").props.accessibilityState.disabled).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * §32: tap the blocker, land on the fix
 * ------------------------------------------------------------------ */

describe("tapping a blocker", () => {
  it("takes the seller to the camera when the missing thing is a photo", async () => {
    const view = await openEditor();
    fireEvent.press(view.getByText("Add photo"));
    expect(view.navigation.navigate).toHaveBeenCalledWith(
      "CameraStudio",
      expect.objectContaining({ target: "marketplace" })
    );
  });

  it("does not navigate away when the fix is a field on this screen", async () => {
    // The complement of the camera case, and as far as this renderer can see.
    // A pricing blocker focuses a `TextInput` ref, and a host element in the
    // test renderer exposes no focus handle at all — so the assertion here is
    // that the tap is handled *locally*: no route change, no error message, no
    // silent bounce to some other screen. Which of the six sections maps to
    // which field is covered exhaustively in `storeFixTarget.test.ts`, and the
    // keyboard actually opening is a simulator check.
    const view = await openEditor();
    fireEvent.press(view.getByText("Add price"));
    expect(view.navigation.navigate).not.toHaveBeenCalled();
    expect(view.queryByText(/policy review/)).toBeNull();
  });

  it("tells the truth about a blocker it cannot route to", async () => {
    const view = await openEditor([
      listing({
        readiness: {
          publishable: false,
          checkout_ready: false,
          blockers: ["RESTRICTED_PRODUCT"],
          warnings: [],
          summary: "1 thing left",
          fixes: [
            { code: "RESTRICTED_PRODUCT", label: "Resolve policy review", section: "policies" }
          ],
          notes: []
        }
      })
    ]);
    fireEvent.press(view.getByText("Resolve policy review"));
    expect(view.getByText(/needs a policy review we can't clear from the app/)).toBeTruthy();
    // And it does not pretend to navigate somewhere.
    expect(view.navigation.navigate).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------------ *
 * §21: the publish gate is the server's, and it is the bulk path's
 * ------------------------------------------------------------------ */

describe("the Publish button", () => {
  it("stays shut on an unfinished listing, wearing the reason", async () => {
    const view = await openEditor();
    const cta = view.getByLabelText("2 things left");
    expect(cta.props.accessibilityState.disabled).toBe(true);
    fireEvent.press(cta);
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("stays shut on a finished listing that is already live", async () => {
    // The case no local derivation reaches. `publishable` is true and every
    // field is filled, so a button gated on readiness alone would publish it —
    // knocking a live product off the storefront and back into review. Only
    // `bulk_eligibility.publish` knows, and it is the same function the bulk
    // bar asks.
    const view = await openEditor([
      listing({
        status: "active",
        approval_status: "approved",
        readiness: READY,
        bulk_eligibility: {
          publish: { code: "ALREADY_PUBLISHED", reason: "Already published" },
          hide: null
        }
      })
    ]);
    const cta = view.getByLabelText("Already published");
    expect(cta.props.accessibilityState.disabled).toBe(true);
    fireEvent.press(cta);
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("publishes a ready draft and redraws from what came back", async () => {
    // §31 end to end: tap, request, backend change, read back, UI update. The
    // response carries a *different* verdict from the one on screen, so a card
    // that redrew from local state would still say "Ready to publish" over a
    // draft the server has moved to review.
    mockSubmit.mockResolvedValue({
      listing: {
        ...listing({
          status: "pending_review",
          approval_status: "pending",
          readiness: READY,
          bulk_eligibility: {
            publish: { code: "ALREADY_PUBLISHED", reason: "Already published" },
            hide: null
          }
        })
      },
      message: "Sent for review."
    });
    const view = await openEditor([
      listing({ readiness: READY, bulk_eligibility: { publish: null, hide: null } })
    ]);

    await act(async () => {
      fireEvent.press(view.getByLabelText("Publish"));
    });

    expect(mockSubmit).toHaveBeenCalledWith(42);
    await waitFor(() => expect(view.getByText("Sent for review.")).toBeTruthy());
    // The read-back: the button now wears the server's new answer.
    await waitFor(() => expect(view.getByLabelText("Already published")).toBeTruthy());
    expect(mockInvalidate).toHaveBeenCalledWith(
      ["seller_inventory", "marketplace"],
      "listing_published"
    );
  });

  it("sends one submit for a double tap", async () => {
    // §23 on the single path. `busy` is state; two presses in one frame both
    // read the pre-flush value and both fire, which is two submits for one
    // intent. The in-flight ref is what makes the second press a no-op.
    let release: (value: unknown) => void = () => undefined;
    mockSubmit.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        })
    );
    const view = await openEditor([
      listing({ readiness: READY, bulk_eligibility: { publish: null, hide: null } })
    ]);

    const cta = view.getByLabelText("Publish");
    fireEvent.press(cta);
    fireEvent.press(cta);

    expect(mockSubmit).toHaveBeenCalledTimes(1);
    await act(async () => {
      release({ listing: listing({ readiness: READY }), message: "Sent for review." });
    });
  });

  it("keeps the seller on the listing when the publish fails", async () => {
    // A failed publish must not look like a successful one, and must not leave
    // the button jammed — the seller has to be able to try again.
    mockSubmit.mockRejectedValue(new Error("Network is down"));
    const view = await openEditor([
      listing({ readiness: READY, bulk_eligibility: { publish: null, hide: null } })
    ]);

    await act(async () => {
      fireEvent.press(view.getByLabelText("Publish"));
    });

    await waitFor(() => expect(view.getByText("Network is down")).toBeTruthy());
    const cta = view.getByLabelText("Publish");
    expect(cta.props.accessibilityState.disabled).toBe(false);
    await act(async () => {
      fireEvent.press(cta);
    });
    expect(mockSubmit).toHaveBeenCalledTimes(2);
  });
});

/* ------------------------------------------------------------------ *
 * §32: preview before commit
 * ------------------------------------------------------------------ */

describe("Preview as buyer", () => {
  it("opens the buyer page for this listing, draft or not", async () => {
    const view = await openEditor();
    fireEvent.press(view.getByLabelText("Preview as buyer"));
    expect(view.navigation.navigate).toHaveBeenCalledWith(
      "MarketplaceProduct",
      expect.objectContaining({ listingId: 42 })
    );
  });

  it("hands the buyer screen the listing, because a draft is not fetchable", async () => {
    // The buyer endpoint does not return unpublished rows. If this stopped
    // passing `listing`, Preview would render an empty product page for exactly
    // the listings a seller most needs to preview.
    const view = await openEditor();
    fireEvent.press(view.getByLabelText("Preview as buyer"));
    const [, params] = view.navigation.navigate.mock.calls[0];
    expect(params.listing.id).toBe(42);
  });
});

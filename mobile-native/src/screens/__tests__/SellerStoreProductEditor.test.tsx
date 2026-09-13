/**
 * Store → Edit(product) → that product's editor. Nothing else on screen.
 *
 * The reported defect: tapping Edit on a necklace landed on what read as a
 * broader Commerce/Listings dashboard. The id was never the problem — it was
 * passed and honoured, which is why the right product's fields were filled in.
 * The problem was `mode: "create"`, whose panel set renders the Storefront
 * readiness hero, the Listing management card and the listing table *above* the
 * editor, under a heading that said "Listings". Correct data, wrong screen.
 *
 * So these tests assert two things the panel-set change is responsible for:
 * absence — no hero, no hub, no picker — and identity, that the product the
 * route names is the product the editor loads, by id and by nothing else.
 *
 * `sellerStoreMode.test.ts` covers the policy in isolation. This file renders
 * the screen, because the failure being fixed was invisible to the policy test:
 * `create` did contain the editor and the panel set was "correct" by that
 * measure. What was wrong was what came with it.
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
  registerSyncInvalidation: jest.fn(() => () => undefined),
  invalidateNativeSync: jest.fn().mockResolvedValue(undefined)
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

/* ------------------------------------------------------------------ *
 * Fixtures — two products, so "the right one" is a distinguishable claim
 * ------------------------------------------------------------------ */

const PRICE_BLOCKED = {
  publishable: false,
  checkout_ready: false,
  blockers: ["MISSING_PRICE"],
  warnings: [],
  summary: "1 thing left",
  fixes: [{ code: "MISSING_PRICE", label: "Add price", section: "pricing" }],
  notes: []
};

const READY = {
  publishable: true,
  checkout_ready: true,
  blockers: [],
  warnings: [],
  summary: "Ready to publish",
  fixes: [],
  notes: []
};

const NECKLACE = {
  id: 101,
  title: "Silver Necklace",
  short_description: "Sterling chain",
  description: "Hand-finished sterling silver.",
  category: "Jewelry",
  price_label: "",
  quantity: 4,
  status: "draft",
  approval_status: "draft",
  readiness: PRICE_BLOCKED,
  bulk_eligibility: {
    publish: { code: "NOT_READY", reason: "1 thing left", blockers: PRICE_BLOCKED.blockers },
    hide: null
  }
};

const PANTS = {
  id: 202,
  title: "Cargo Pants",
  short_description: "Six pockets",
  description: "Heavyweight cotton.",
  category: "Apparel",
  price_label: "$48.00",
  quantity: 12,
  status: "active",
  approval_status: "approved",
  buyer_visible: true,
  readiness: READY,
  bulk_eligibility: { publish: null, hide: null }
};

/** Copy that only appears when a panel other than the editor rendered. */
const OTHER_SURFACES = {
  hero: "Storefront readiness",
  heroKicker: "Marketplace Command",
  listingsHub: "Listing management",
  createListing: "Create Listing",
  picker: "Seller inventory",
  media: "Product media gallery",
  orders: "Orders and payouts",
  trust: "Trust and eligibility",
  application: "Merchant application"
};

beforeEach(() => {
  jest.clearAllMocks();
  mockSnapshot.mockResolvedValue({ live: true, listings: [NECKLACE, PANTS], orders: [] });
  mockCommercialTerms.mockResolvedValue({ terms: { acceptance: null } });
});

beforeAll(async () => {
  await activateLocale("en");
});

async function openProduct(params: Record<string, unknown>) {
  const navigation = { navigate: jest.fn(), goBack: jest.fn() };
  const view = render(
    <SellerStoreScreen route={{ params: { mode: "product", ...params } as never }} navigation={navigation} />
  );
  await waitFor(() => expect(mockSnapshot).toHaveBeenCalled());
  await waitFor(() => expect(mockCommercialTerms).toHaveBeenCalled());
  return { ...view, navigation };
}

/* ------------------------------------------------------------------ *
 * Identity: the product the route names is the product on screen
 * ------------------------------------------------------------------ */

describe("the single-product editor", () => {
  it("opens the necklace on the necklace", async () => {
    const view = await openProduct({ listingId: 101, title: "Silver Necklace" });

    // The read-back the correction asks for, in one assertion each: the route's
    // title as the screen heading, the route's id as the editor's own header,
    // and this product's values in the fields.
    await waitFor(() => expect(view.getByText("Edit listing #101")).toBeTruthy());
    expect(view.getAllByText("Silver Necklace").length).toBeGreaterThan(0);
    expect(view.getByDisplayValue("Silver Necklace")).toBeTruthy();
    expect(view.getByDisplayValue("Jewelry")).toBeTruthy();
    expect(view.getByDisplayValue("4")).toBeTruthy();

    // And the other product is not here at all. A picker listing both would be
    // "make the merchant select the product again".
    expect(view.queryByText("Cargo Pants")).toBeNull();
    expect(view.queryByText("Edit listing #202")).toBeNull();
  });

  it("opens the pants on the pants", async () => {
    // The same route with a different id. Run as its own case rather than as a
    // second navigation, because a first-product fallback or a cached ref would
    // pass the necklace test and fail only on the second product.
    const view = await openProduct({ listingId: 202, title: "Cargo Pants" });

    await waitFor(() => expect(view.getByText("Edit listing #202")).toBeTruthy());
    expect(view.getByDisplayValue("Cargo Pants")).toBeTruthy();
    expect(view.getByDisplayValue("Apparel")).toBeTruthy();
    expect(view.getByDisplayValue("$48.00")).toBeTruthy();

    expect(view.queryByText("Silver Necklace")).toBeNull();
    expect(view.queryByText("Edit listing #101")).toBeNull();
  });

  it("renders the editor and none of the store's other surfaces", async () => {
    const view = await openProduct({ listingId: 101, title: "Silver Necklace" });
    await waitFor(() => expect(view.getByText("Edit listing #101")).toBeTruthy());

    // This is the defect, stated as a test. Every one of these was on screen
    // above the editor when Edit routed through `create`.
    Object.values(OTHER_SURFACES).forEach((copy) => {
      expect(view.queryByText(copy)).toBeNull();
    });
  });

  it("shows the blocker and the fix for it", async () => {
    // §32: the editor has to say what is missing, not just be a form. The
    // readiness panel is shared with the other modes, so this asserts it
    // survived the panel-set narrowing rather than re-testing its contents.
    const view = await openProduct({ listingId: 101, title: "Silver Necklace" });
    await waitFor(() => expect(view.getByText("Ready to sell")).toBeTruthy());
    expect(view.getAllByText("1 thing left").length).toBeGreaterThan(0);
    expect(view.getByText("Add price")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * §27: an id that is not this merchant's
 * ------------------------------------------------------------------ */

describe("an id the merchant does not own", () => {
  it("refuses rather than falling back to the first product", async () => {
    // The dangerous version of this bug, and the one the old code had: the
    // shared derivation ends `|| listings[0]`, so an unresolvable id handed the
    // merchant a *different* product's form — pre-filled, and wired to a Save
    // that writes to that product's id.
    //
    // `listings` only ever holds this merchant's own store, so a cross-store id
    // and a deleted id arrive here identically, and both must be refused.
    const view = await openProduct({ listingId: 999, title: "Someone Else's Ring" });

    await waitFor(() => expect(view.getByText(/isn't in your store/)).toBeTruthy());
    expect(view.queryByText("Edit listing #101")).toBeNull();
    expect(view.queryByText("Edit listing #202")).toBeNull();
    expect(view.queryByText("Edit listing #999")).toBeNull();
    expect(view.queryByDisplayValue("Silver Necklace")).toBeNull();
  });

  it("resolves an id that arrived from a deep link as a string", async () => {
    // The dashboard passes `row.id`, a number. A `/pulse/seller-store?listingId=101`
    // link passes the query string, and `101 === "101"` is false — so without
    // coercion the merchant's own product is refused by the same branch that
    // exists to refuse other merchants'. Both types have to reach one listing.
    const view = await openProduct({ listingId: "101", title: "Silver Necklace" });

    await waitFor(() => expect(view.getByText("Edit listing #101")).toBeTruthy());
    expect(view.getByDisplayValue("Silver Necklace")).toBeTruthy();
    expect(view.queryByText(/isn't in your store/)).toBeNull();
  });

  it("refuses a non-numeric id instead of treating it as no id at all", async () => {
    // `Number("abc")` is NaN and falls to 0, which is also "no listing
    // requested". In product mode that must still be a refusal — falling back to
    // the full list here would put the picker back under a product heading.
    const view = await openProduct({ listingId: "abc", title: "Silver Necklace" });

    await waitFor(() => expect(view.getByText(/isn't in your store/)).toBeTruthy());
    expect(view.queryByDisplayValue("Silver Necklace")).toBeNull();
  });

  it("does not claim the store is empty while refusing an id", async () => {
    // Error and empty must never co-render: the store has two products, and
    // "no listings yet" beside "isn't in your store" gives one blank panel two
    // contradictory reasons.
    const view = await openProduct({ listingId: 999 });
    await waitFor(() => expect(view.getByText(/isn't in your store/)).toBeTruthy());
    expect(view.queryByText(/No listings/i)).toBeNull();
    expect(view.queryByText(/nothing on your shelf/i)).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Finish listing: the same editor, aimed at the blocker
 * ------------------------------------------------------------------ */

describe("the Finish listing deep link", () => {
  it("opens the same editor on the same product when a section is passed", async () => {
    // What `section` must NOT do: change which product loads, or route
    // somewhere else. The focus it causes is a ref call, which this renderer
    // cannot observe — that is the simulator's half of the proof. What is
    // checkable here is that the editor is the same one, on the same product,
    // with no navigation away.
    const view = await openProduct({ listingId: 101, title: "Silver Necklace", section: "pricing" });

    await waitFor(() => expect(view.getByText("Edit listing #101")).toBeTruthy());
    expect(view.getByDisplayValue("Silver Necklace")).toBeTruthy();
    expect(view.navigation.navigate).not.toHaveBeenCalled();
    expect(view.queryByText("Cargo Pants")).toBeNull();
  });

  it("does not navigate away for a section whose fix lives on another screen", async () => {
    // `media` maps to CameraStudio when a blocker row is *tapped*. On open it
    // must not: throwing the merchant into the camera before they have seen
    // their own product is the same class of failure as opening the wrong
    // screen. A tap is a request; arriving is not.
    const view = await openProduct({ listingId: 101, title: "Silver Necklace", section: "media" });

    await waitFor(() => expect(view.getByText("Edit listing #101")).toBeTruthy());
    expect(view.navigation.navigate).not.toHaveBeenCalled();
  });
});

/**
 * What the dropshipping screens promise that a screenshot cannot show.
 *
 * The api-level suite already pins the data contract — no economics leave the
 * client, unknown cost stays `null`, unknown stock stays `UNKNOWN`. This file
 * pins the four things a *screen* can still get wrong with a perfectly correct
 * api module underneath it:
 *
 * 1. **One state at a time.** A failed request must never be drawn as an empty
 *    result. "You have no suppliers" and "we couldn't reach the server" send a
 *    merchant to two different places, and only one of them is useful.
 * 2. **Every outcome is reported.** Nine imports and one refusal is not
 *    "imported". The row that failed is the only row the merchant has to act
 *    on, so it cannot be summarised away.
 * 3. **An unrecognised status is never good news.** A provider inventing a new
 *    connection status must degrade to the raw word, not to "Connected and
 *    working" — a revoked credential shown as connected is a catalogue full of
 *    failing searches with no explanation.
 * 4. **Supplier-owned facts are text, not fields.** Cost and stock come from
 *    the provider. An input beside them implies the merchant can change them,
 *    and an unknown cost drawn as a zero is the number they would price
 *    against.
 */

import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

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
jest.mock("../../../theme/logiNexusMotion", () => ({
  ...jest.requireActual("../../../theme/logiNexusMotion"),
  useLogiNexusReducedMotion: () => true
}));

// The real transport is never reached — every api function these screens call
// is stubbed below — but the module is still imported, and `stateForError` does
// an `instanceof` against this class. Mocking it here keeps the class the tests
// throw and the class the api module checks as one object.
jest.mock("../../../api/pulseApi", () => ({
  pulseApi: jest.fn(),
  PulseApiError: class PulseApiError extends Error {
    status: number;
    code?: string;
    details?: Record<string, unknown>;
    constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
      super(message);
      this.name = "PulseApiError";
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }
}));

const mockResolveScope = jest.fn();
const mockListConnections = jest.fn();
const mockGetCart = jest.fn();
const mockImportSelected = jest.fn();
const mockGetImportedProduct = jest.fn();
const mockListImportedProducts = jest.fn();
const mockPreviewPricing = jest.fn();

jest.mock("../../../api/dropshipping", () => ({
  ...jest.requireActual("../../../api/dropshipping"),
  resolveDropshippingScope: (...args: unknown[]) => mockResolveScope(...args),
  listSupplierConnections: (...args: unknown[]) => mockListConnections(...args),
  getImportCart: (...args: unknown[]) => mockGetCart(...args),
  importSelected: (...args: unknown[]) => mockImportSelected(...args),
  getImportedProduct: (...args: unknown[]) => mockGetImportedProduct(...args),
  listImportedProducts: (...args: unknown[]) => mockListImportedProducts(...args),
  previewPricing: (...args: unknown[]) => mockPreviewPricing(...args)
}));

import { PulseApiError } from "../../../api/pulseApi";
import {
  DROPSHIPPING_STATES,
  type DropshippingState,
  type ImportCartItem,
  type ImportedDraft,
  type SupplierConnection
} from "../../../api/dropshipping";
import { DropshippingStateView } from "../../../components/dropshipping/DropshippingStates";
import { DropshippingProductsScreen } from "../DropshippingProductsScreen";
import { ImportCartScreen } from "../ImportCartScreen";
import { ReviewImportedProductScreen } from "../ReviewImportedProductScreen";
import { SuppliersScreen } from "../SuppliersScreen";
import { resetDropshippingScopeCache } from "../useDropshippingScope";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

function connection(over: Partial<SupplierConnection> = {}): SupplierConnection {
  return {
    id: "conn-1",
    provider: "cj",
    status: "CONNECTED",
    externalShopId: "shop-9",
    environment: "SANDBOX",
    productionFulfillmentEnabled: false,
    lastVerifiedAt: null,
    lastSyncAt: null,
    message: null,
    ...over
  };
}

function cartItem(over: Partial<ImportCartItem> = {}): ImportCartItem {
  return {
    itemId: "item-1",
    connectionId: "conn-1",
    provider: "cj",
    externalProductId: "ext-1",
    selectedVariantIds: [],
    preview: {
      title: "Ceramic Mug",
      coverImageUrl: null,
      category: "Home",
      origin: "CN",
      currency: "USD",
      variantCount: 3,
      costLowCents: 450,
      costHighCents: 450,
      availability: "AVAILABLE"
    },
    stale: false,
    createdAt: null,
    updatedAt: null,
    ...over
  };
}

function draft(over: Partial<ImportedDraft> = {}): ImportedDraft {
  return {
    listingId: 77,
    status: "draft",
    approvalStatus: "pending",
    published: false,
    title: "Ceramic Mug",
    description: "A mug.",
    category: "Home",
    currency: "USD",
    media: ["https://cdn.example/mug.jpg"],
    coverImageUrl: "https://cdn.example/mug.jpg",
    variants: [
      {
        variantId: 1,
        options: { Colour: "White" },
        sku: "MUG-W",
        providerVariantId: "pv-1",
        stockState: "IN_STOCK",
        stockQuantity: 40,
        availability: "AVAILABLE",
        currency: "USD",
        costCents: 450,
        retailCents: 1200,
        proposedRetailCents: 1200,
        marginCents: 750,
        marginPercent: 62.5,
        marginState: "HEALTHY"
      }
    ],
    supplier: {
      provider: "cj",
      fulfillmentMode: "SANDBOX",
      syncState: "OK",
      lastSyncedAt: null,
      supplierCostCents: 450,
      supplierCostCurrency: "USD",
      externalSku: "CJ-1",
      merchantOwnedFields: []
    },
    pricingRule: { type: "COST_PLUS_PERCENT", value: 60 },
    validation: { publishable: true, problems: [] },
    ...over
  };
}

function navigation() {
  return { navigate: jest.fn(), goBack: jest.fn() };
}

beforeEach(() => {
  jest.clearAllMocks();
  resetDropshippingScopeCache();
  mockResolveScope.mockResolvedValue({
    status: "ok",
    scope: { businessId: "biz-1", storeId: "store-1" },
    businessName: "Bright Coffee Co"
  });
  // Nothing under test asks for a live preview quote; a screen that does gets a
  // resolved promise rather than an unhandled rejection in the background.
  mockPreviewPricing.mockResolvedValue({ quotes: [], currency: "USD" });
});

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/* ------------------------------------------------------------------ *
 * 1 — the state machine
 * ------------------------------------------------------------------ */

describe("DropshippingStateView", () => {
  const EMPTY_COPY = { title: "Nothing here yet.", body: "Add something." };

  function renderState(state: DropshippingState) {
    return render(
      <DropshippingStateView
        state={state}
        subject="Products"
        onRetry={() => undefined}
        empty={EMPTY_COPY}
        reducedMotion
      />
    );
  }

  it("never draws an error and an empty state together", () => {
    // The specific bug: `{error && <Error/>}{items.length === 0 && <Empty/>}`,
    // where a failed fetch leaves an empty array behind and both render.
    for (const state of DROPSHIPPING_STATES) {
      const view = renderState(state);
      const sawEmpty = view.queryByText(EMPTY_COPY.title) !== null;
      const sawError = view.queryByText(/didn't load|isn't responding|needs attention|not signed in/i) !== null;
      expect(sawEmpty && sawError).toBe(false);
      view.unmount();
    }
  });

  it("renders content itself for READY and STALE, so stale data is still shown", () => {
    // Stale numbers are real numbers. Hiding a merchant's cart behind a "this
    // may be out of date" card is a bigger lie than the staleness.
    expect(renderState("READY").toJSON()).toBeNull();
    expect(renderState("STALE").toJSON()).toBeNull();
  });

  it("says a supplier problem is a supplier problem, not a generic failure", () => {
    const view = renderState("SUPPLIER_DISCONNECTED");
    expect(view.getByText(/supplier connection needs attention/i)).toBeTruthy();
    expect(view.queryByText(/Products didn't load/)).toBeNull();
  });

  it("keeps provider downtime apart from the merchant's own credentials", () => {
    const view = renderState("PROVIDER_UNAVAILABLE");
    expect(view.getByText(/supplier isn't responding/i)).toBeTruthy();
    // A merchant told to reconnect will re-enter a credential that was fine.
    expect(view.queryByText(/needs attention/i)).toBeNull();
  });

  it("offers no retry copy where retrying cannot help, and says what will", () => {
    const view = render(
      <DropshippingStateView
        state="SUPPLIER_DISCONNECTED"
        subject="Products"
        onRetry={() => undefined}
        onFixConnection={() => undefined}
        reducedMotion
      />
    );
    expect(view.getByText("Check suppliers")).toBeTruthy();
    expect(view.queryByText("Try again")).toBeNull();
  });
});

/**
 * Every screen has to ask "does the state own this screen?" *before* it renders
 * the state view, because a React element is truthy even when it renders to
 * `null`. The first version of these screens wrote
 *
 *     const stateBlock = (<DropshippingStateView … />);
 *     …
 *     data={stateBlock ? [] : rows}
 *
 * which is always the empty branch. Every one of them shipped a permanently
 * blank READY state, and typecheck and lint were both perfectly happy. There is
 * no render assertion that catches this on a screen nobody wrote a harness for,
 * so the shape is pinned directly across all eight.
 */
describe("no screen tests a JSX element for truthiness", () => {
  const SCREENS = [
    "DropshippingHubScreen",
    "DropshippingProductsScreen",
    "DropshippingSyncScreen",
    "ImportCartScreen",
    "ReviewImportedProductScreen",
    "SupplierCatalogScreen",
    "SupplierProductScreen",
    "SuppliersScreen"
  ];

  it.each(SCREENS)("%s gates its state block on the state, not the element", (screen) => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const source: string = require("fs").readFileSync(
      require("path").join(__dirname, "..", `${screen}.tsx`),
      "utf8"
    );
    expect(source).toContain("stateOwnsScreen(state) ? (");
    expect(source).not.toContain("const stateBlock = (");
  });
});

/* ------------------------------------------------------------------ *
 * 2 — Suppliers
 * ------------------------------------------------------------------ */

describe("SuppliersScreen", () => {
  it("does not read a failed list as an empty one", async () => {
    mockListConnections.mockRejectedValue(new Error("network down"));
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();

    await waitFor(() => expect(view.getByText(/Suppliers didn't load/)).toBeTruthy());
    expect(view.queryByText("No suppliers connected yet.")).toBeNull();
  });

  it("shows the empty invitation only when the server actually said zero", async () => {
    mockListConnections.mockResolvedValue([]);
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();

    await waitFor(() => expect(view.getByText("No suppliers connected yet.")).toBeTruthy());
    expect(view.queryByText(/didn't load/)).toBeNull();
  });

  it("never renders an unrecognised status as connected", async () => {
    // A provider adding a status this app has not seen must not be optimistically
    // rounded up. The raw word is unhelpful; "Connected and working" is wrong.
    mockListConnections.mockResolvedValue([connection({ status: "PENDING_MANUAL_REVIEW" })]);
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();

    await waitFor(() => expect(view.getByText("PENDING_MANUAL_REVIEW")).toBeTruthy());
    expect(view.queryByText("Connected and working")).toBeNull();
  });

  it("offers no catalogue for a connection that cannot serve one", async () => {
    mockListConnections.mockResolvedValue([connection({ status: "AUTH_EXPIRED" })]);
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();

    await waitFor(() => expect(view.getByText(/credential expired/i)).toBeTruthy());
    // A "Find products" button on a dead connection is a search that fails with
    // no explanation.
    expect(view.queryByText("Find products")).toBeNull();
  });

  it("states that real fulfilment is off rather than leaving it to be discovered", async () => {
    mockListConnections.mockResolvedValue([connection()]);
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();

    await waitFor(() => expect(view.getByText("Connected and working")).toBeTruthy());
    expect(view.getByText(/Sending real orders to this supplier is switched off/)).toBeTruthy();
    expect(view.getByText("Sandbox")).toBeTruthy();
  });

  it("sends an unauthenticated merchant to sign in, not to retry", async () => {
    mockListConnections.mockRejectedValue(new PulseApiError("nope", 401));
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();

    await waitFor(() => expect(view.getByText(/not signed in to this store/i)).toBeTruthy());
  });
});

/* ------------------------------------------------------------------ *
 * 3 — Import cart
 * ------------------------------------------------------------------ */

describe("ImportCartScreen", () => {
  const route = { params: { connectionId: "conn-1" } };

  async function renderCart(items: ImportCartItem[], staleCount = 0) {
    mockGetCart.mockResolvedValue({
      items,
      count: items.length,
      staleCount,
      maxItems: 200
    });
    const nav = navigation();
    const view = render(<ImportCartScreen navigation={nav} route={route} />);
    await settle();
    await waitFor(() => expect(mockGetCart).toHaveBeenCalled());
    return { view, nav };
  }

  it("calls the import as drafts, on the button and on the screen", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    expect(view.getByText(/Import .* as drafts/)).toBeTruthy();
    expect(
      view.getByText("Imported products are drafts. Nothing appears in your store until you publish it.")
    ).toBeTruthy();
    // There is no publish control here. Publishing is a separate deliberate act
    // on a separate screen.
    expect(view.queryByText(/^Publish/)).toBeNull();
  });

  it("sends ids and a rule, and nothing that looks like a price", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue({
      results: [
        { itemId: "item-1", externalProductId: "ext-1", provider: "cj", outcome: "IMPORTED", listingId: 5, detail: null, variantCount: 3 }
      ],
      requested: 1,
      imported: 1,
      counts: { IMPORTED: 1 },
      published: false,
      pricingRule: { type: "COST_PLUS_PERCENT", value: 60 }
    });

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import 1 products as drafts"));
    });
    await settle();

    const [, connectionId, input] = mockImportSelected.mock.calls[0];
    expect(connectionId).toBe("conn-1");
    expect(input.itemIds).toEqual(["item-1"]);
    // The rule is a policy the server applies to a cost it fetched itself. Any
    // other key here would be the client asserting supplier economics.
    expect(Object.keys(input).sort()).toEqual(["itemIds", "pricingRule"]);
  });

  it("reports every per-item outcome instead of one verdict", async () => {
    const { view } = await renderCart([
      cartItem({ itemId: "item-1", externalProductId: "ext-1" }),
      cartItem({ itemId: "item-2", externalProductId: "ext-2", preview: null }),
      cartItem({ itemId: "item-3", externalProductId: "ext-3", preview: null })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue({
      results: [
        { itemId: "item-1", externalProductId: "ext-1", provider: "cj", outcome: "IMPORTED", listingId: 5, detail: null, variantCount: 3 },
        { itemId: "item-2", externalProductId: "ext-2", provider: "cj", outcome: "ALREADY_EXISTS", listingId: 6, detail: null, variantCount: null },
        { itemId: "item-3", externalProductId: "ext-3", provider: "cj", outcome: "PROVIDER_UNAVAILABLE", listingId: null, detail: null, variantCount: null }
      ],
      requested: 3,
      imported: 1,
      counts: { IMPORTED: 1, ALREADY_EXISTS: 1, PROVIDER_UNAVAILABLE: 1 },
      published: false,
      pricingRule: { type: "COST_PLUS_PERCENT", value: 60 }
    });

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import 3 products as drafts"));
    });
    await settle();

    // The one that failed is the only one the merchant has to do anything
    // about, so it cannot be rounded away into "1 of 3 imported".
    await waitFor(() => expect(view.getByText("1 of 3 imported as drafts.")).toBeTruthy());
    expect(view.getByText(/Imported as a draft/)).toBeTruthy();
    expect(view.getByText("Already in your store")).toBeTruthy();
    expect(view.getByText(/didn't respond — try this one again/)).toBeTruthy();
  });

  it("does not report an unknown outcome as a success", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue({
      results: [
        { itemId: "item-1", externalProductId: "ext-1", provider: "cj", outcome: "SOME_NEW_CODE", listingId: null, detail: null, variantCount: null }
      ],
      requested: 1,
      imported: 0,
      counts: { SOME_NEW_CODE: 1 },
      published: false,
      pricingRule: { type: "COST_PLUS_PERCENT", value: 60 }
    });

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import 1 products as drafts"));
    });
    await settle();

    await waitFor(() => expect(view.getByText("This one couldn't be imported")).toBeTruthy());
    expect(view.queryByText("Imported as a draft")).toBeNull();
  });

  it("says nothing was imported when the run itself failed", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockRejectedValue(
      new PulseApiError("down", 503, "provider_unavailable")
    );

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import 1 products as drafts"));
    });
    await settle();

    await waitFor(() =>
      expect(
        view.getByText("Your supplier didn't respond. Nothing was imported — your cart is unchanged.")
      ).toBeTruthy()
    );
    // No result sheet, because there were no results.
    expect(view.queryByText(/imported as drafts\./)).toBeNull();
  });

  it("says a cost is unknown rather than printing a zero", async () => {
    const { view } = await renderCart([
      cartItem({ preview: { ...cartItem().preview!, costLowCents: null, costHighCents: null } })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    expect(view.getByText("— cost unknown")).toBeTruthy();
    expect(view.queryByText(/0\.00 cost/)).toBeNull();
  });

  it("promises a re-check rather than importing the stale numbers on screen", async () => {
    const { view } = await renderCart([cartItem({ stale: true })], 1);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    expect(view.getByText(/re-checked from your supplier when you import/)).toBeTruthy();
    // Stale content is still content — the row is on screen, not replaced.
    expect(view.getByText("Ceramic Mug")).toBeTruthy();
  });

  it("cannot import when nothing is selected", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByLabelText("Ceramic Mug, selected for import"));
    });

    const button = view.getByLabelText("Import 0 products as drafts");
    expect(button.props.accessibilityState.disabled).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * 4 — Draft review
 * ------------------------------------------------------------------ */

describe("ReviewImportedProductScreen", () => {
  const route = { params: { connectionId: "conn-1", listingId: 77 } };

  async function renderDraft(value: ImportedDraft) {
    mockGetImportedProduct.mockResolvedValue(value);
    const nav = navigation();
    const view = render(<ReviewImportedProductScreen navigation={nav} route={route} />);
    await settle();
    await waitFor(() => expect(mockGetImportedProduct).toHaveBeenCalled());
    return { view, nav };
  }

  it("lists every publish problem at once, not the first one", async () => {
    // Fixing one problem, pressing publish, and being told about the next is
    // the experience this screen exists to avoid.
    const { view } = await renderDraft(
      draft({
        validation: {
          publishable: false,
          problems: ["MISSING_TITLE", "MISSING_PRICE", "MISSING_CATEGORY"]
        }
      })
    );

    await waitFor(() => expect(view.getByText("Give this product a title.")).toBeTruthy());
    expect(view.getByText("Set a price for every variant you want to sell.")).toBeTruthy();
    expect(view.getByText("Choose a category so buyers can find it.")).toBeTruthy();
  });

  it("does not tell the merchant to fix something that is not theirs to fix", async () => {
    const { view } = await renderDraft(
      draft({
        validation: { publishable: false, problems: ["PROVIDER_PRODUCT_UNAVAILABLE"] }
      })
    );

    await waitFor(() =>
      expect(view.getByText("Your supplier no longer offers this product.")).toBeTruthy()
    );
    expect(view.queryByText(/Give this product a title/)).toBeNull();
  });

  it("renders an unknown problem code rather than swallowing it", async () => {
    const { view } = await renderDraft(
      draft({ validation: { publishable: false, problems: ["SOME_FUTURE_PROBLEM"] } })
    );

    // Unhelpful, but visible. A problem list that silently drops codes leaves a
    // merchant with a disabled Publish button and no reason for it.
    await waitFor(() => expect(view.getByText("SOME_FUTURE_PROBLEM")).toBeTruthy());
  });

  it("blocks publish while the server says it is not publishable", async () => {
    const { view } = await renderDraft(
      draft({ validation: { publishable: false, problems: ["MISSING_PRICE"] } })
    );

    await waitFor(() => expect(view.getByLabelText("Publish to your store")).toBeTruthy());
    expect(view.getByLabelText("Publish to your store").props.accessibilityState.disabled).toBe(true);
  });

  it("gives supplier cost no input, only the merchant's price", async () => {
    const { view } = await renderDraft(draft());

    await waitFor(() => expect(view.getByText(/Your cost/)).toBeTruthy());
    // One field per variant, and it is the retail price. An input beside a cost
    // implies the merchant can change what their supplier charges.
    expect(view.getByLabelText("Price for White")).toBeTruthy();
    expect(view.queryByLabelText(/Cost for/)).toBeNull();
    expect(view.getByText("Cost and stock come from your supplier and can't be edited here. Price is yours.")).toBeTruthy();
  });

  it("says a missing cost is missing instead of showing it as free", async () => {
    const { view } = await renderDraft(
      draft({
        variants: [
          {
            ...draft().variants[0],
            costCents: null,
            marginState: "UNKNOWN",
            marginPercent: null,
            proposedRetailCents: null
          }
        ]
      })
    );

    await waitFor(() => expect(view.getByText("Cost — your supplier didn't give one")).toBeTruthy());
    expect(view.getByText("Margin unknown")).toBeTruthy();
    // Not the negative-margin red: a margin nobody could compute is not a loss.
    expect(view.queryByText("Selling at a loss")).toBeNull();
  });

  it("shows unknown stock as unknown, never as sold out", async () => {
    const { view } = await renderDraft(
      draft({
        variants: [
          { ...draft().variants[0], stockState: "UNKNOWN", stockQuantity: null }
        ]
      })
    );

    await waitFor(() => expect(view.getByText("Stock unknown")).toBeTruthy());
    expect(view.queryByText("Out of stock")).toBeNull();
  });

  it("enables Save only once something has actually been edited", async () => {
    const { view } = await renderDraft(draft());

    await waitFor(() => expect(view.getByLabelText("Save changes")).toBeTruthy());
    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(true);

    await act(async () => {
      fireEvent.changeText(view.getByLabelText("Title"), "My Better Mug");
    });

    expect(view.getByLabelText("Save changes").props.accessibilityState.disabled).toBe(false);
    expect(view.getByText("You have unsaved changes. Save them before publishing.")).toBeTruthy();
  });

  it("marks a field the merchant owns so a sync overwriting it looks wrong", async () => {
    const { view } = await renderDraft(
      draft({ supplier: { ...draft().supplier, merchantOwnedFields: ["title"] } })
    );

    await waitFor(() => expect(view.getByText("Yours — sync won't change it")).toBeTruthy());
    // Ownership follows the edit. An untouched field is not claimed.
    expect(view.queryAllByText("Yours — sync won't change it")).toHaveLength(1);
  });

  it("says fulfilment is sandbox on the screen where publishing happens", async () => {
    const { view } = await renderDraft(draft());

    await waitFor(() =>
      expect(view.getByText(/Supplier fulfilment runs in sandbox/)).toBeTruthy()
    );
  });

  it("keeps a failed load out of the publishable path entirely", async () => {
    mockGetImportedProduct.mockRejectedValue(
      new PulseApiError("gone", 503, "provider_unavailable")
    );
    const view = render(<ReviewImportedProductScreen navigation={navigation()} route={route} />);
    await settle();

    await waitFor(() => expect(view.getByText(/supplier isn't responding/i)).toBeTruthy());
    // No form, no publish button, no half-rendered draft to act on.
    expect(view.queryByLabelText("Publish to your store")).toBeNull();
    expect(view.queryByLabelText("Save changes")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * 5 — Imported products
 * ------------------------------------------------------------------ */

describe("DropshippingProductsScreen", () => {
  const route = { params: { connectionId: "conn-1" } };

  function row(over: Record<string, unknown> = {}) {
    return {
      listingId: 77,
      title: "Ceramic Mug",
      status: "draft",
      approvalStatus: "pending",
      currency: "USD",
      coverImageUrl: null,
      updatedAt: null,
      provider: "cj",
      syncState: "OK",
      supplierCostCents: 450,
      providerProductId: "ext-1",
      ...over
    };
  }

  async function renderProducts(items: ReturnType<typeof row>[]) {
    mockListImportedProducts.mockResolvedValue({ items, count: items.length });
    const nav = navigation();
    const view = render(<DropshippingProductsScreen navigation={nav} route={route} />);
    await settle();
    await waitFor(() => expect(mockListImportedProducts).toHaveBeenCalled());
    return { view, nav };
  }

  it("renders the products it was given", async () => {
    // The second screen proving the READY branch is reachable at all. A screen
    // that renders nothing on success passes every other assertion in this file.
    const { view } = await renderProducts([row()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());
  });

  it("says a draft is not in the store, rather than leaving 'Draft' to be read as saved", async () => {
    const { view } = await renderProducts([row()]);
    await waitFor(() => expect(view.getByText("Not in your store yet")).toBeTruthy());
  });

  it("reports an unknown supplier cost as unknown", async () => {
    const { view } = await renderProducts([row({ supplierCostCents: null })]);
    await waitFor(() => expect(view.getByText(/Cost —/)).toBeTruthy());
  });

  it("stays silent about sync when there is nothing to report", async () => {
    const { view } = await renderProducts([row({ syncState: "OK" })]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());
    expect(view.queryByText(/Waiting on your supplier|out of date|failed/)).toBeNull();
  });

  it("names a failed sync so the merchant knows the figures are not current", async () => {
    const { view } = await renderProducts([row({ syncState: "ERROR" })]);
    await waitFor(() =>
      expect(view.getByText("Last sync from your supplier failed")).toBeTruthy()
    );
  });
});

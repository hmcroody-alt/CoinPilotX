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
 * 5. **A promise about fulfilment comes from the server, not the build.** The
 *    "nothing is sent to your supplier" card is the most reassuring thing on
 *    the supplier-orders screen, and the only thing on it that could become a
 *    lie without any code changing. It is drawn from the response, so a screen
 *    that hardcodes it fails here.
 */

import React from "react";
import { FlatList, Text } from "react-native";
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
const mockDiscoverShops = jest.fn();
const mockListConnectionShops = jest.fn();
const mockBindConnectionShop = jest.fn();
const mockConnectSupplier = jest.fn();
const mockSearchProducts = jest.fn();
const mockBindDraftVariant = jest.fn();
const mockListSupplierObligations = jest.fn();

jest.mock("../../../api/dropshipping", () => ({
  ...jest.requireActual("../../../api/dropshipping"),
  resolveDropshippingScope: (...args: unknown[]) => mockResolveScope(...args),
  listSupplierConnections: (...args: unknown[]) => mockListConnections(...args),
  getImportCart: (...args: unknown[]) => mockGetCart(...args),
  importSelected: (...args: unknown[]) => mockImportSelected(...args),
  getImportedProduct: (...args: unknown[]) => mockGetImportedProduct(...args),
  listImportedProducts: (...args: unknown[]) => mockListImportedProducts(...args),
  previewPricing: (...args: unknown[]) => mockPreviewPricing(...args),
  discoverSupplierShops: (...args: unknown[]) => mockDiscoverShops(...args),
  listConnectionShops: (...args: unknown[]) => mockListConnectionShops(...args),
  bindConnectionShop: (...args: unknown[]) => mockBindConnectionShop(...args),
  connectSupplier: (...args: unknown[]) => mockConnectSupplier(...args),
  searchSupplierProducts: (...args: unknown[]) => mockSearchProducts(...args),
  bindDraftVariant: (...args: unknown[]) => mockBindDraftVariant(...args),
  listSupplierObligations: (...args: unknown[]) => mockListSupplierObligations(...args)
}));

import { PulseApiError } from "../../../api/pulseApi";
import {
  DROPSHIPPING_DATA_GAPS,
  DROPSHIPPING_STATES,
  SUPPLIER_OBLIGATION_BLOCKERS,
  SUPPLIER_OBLIGATION_BLOCKER_COPY,
  connectionNeedsAttention,
  type DropshippingState,
  type ImportCartItem,
  type ImportedDraft,
  type SupplierConnection,
  type SupplierObligation
} from "../../../api/dropshipping";
import { DropshippingStateView } from "../../../components/dropshipping/DropshippingStates";
import { ConnectSupplierScreen } from "../ConnectSupplierScreen";
import { DropshippingHubScreen } from "../DropshippingHubScreen";
import { DropshippingOrdersScreen } from "../DropshippingOrdersScreen";
import { DropshippingProductsScreen } from "../DropshippingProductsScreen";
import { DropshippingSyncScreen } from "../DropshippingSyncScreen";
import { ImportCartScreen } from "../ImportCartScreen";
import { ReviewImportedProductScreen } from "../ReviewImportedProductScreen";
import { SupplierCatalogScreen } from "../SupplierCatalogScreen";
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
      // `DROPSHIP`, not `SANDBOX`. This said "SANDBOX" — an environment mode in
      // a fulfilment-mode field, a value `marketplace_product_sources` cannot
      // hold (`MODE_STOCKED` / `MODE_DROPSHIP` are the two). Every test built on
      // it was therefore exercising a listing that is neither dropshipped nor
      // stocked, which is why none of them noticed the variant binding.
      fulfillmentMode: "DROPSHIP",
      syncState: "OK",
      lastSyncedAt: null,
      supplierCostCents: 450,
      supplierCostCurrency: "USD",
      externalSku: "CJ-1",
      // Bound by default, because the default draft here is a publishable one
      // and an unbound dropship draft is not publishable. Tests about the
      // unbound state override these two.
      providerProductId: "ext-1",
      providerVariantId: "pv-1",
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
    storeName: "Bright Coffee Co",
    source: "BUSINESS_OS"
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

  /** The states that report a failure — everything that is not data or waiting. */
  const FAILURE_STATES = DROPSHIPPING_STATES.filter(
    (state) => !["LOADING", "EMPTY", "READY", "STALE"].includes(state)
  );

  /**
   * The sentence on the failure card, or `null` when no failure was drawn.
   *
   * Found by the card's live region rather than by matching a list of known
   * phrases. An alternation of sentences cannot tell "this state drew no error"
   * apart from "this state drew an error whose wording nobody has added to the
   * regex yet", and the second is exactly how a new state ships broken: it
   * falls to `default:`, says "Products didn't load", and every assertion here
   * still passes.
   */
  function failureMessage(view: ReturnType<typeof render>): string | null {
    const [card] = view.UNSAFE_root.findAll(
      (node) => node.props?.accessibilityLiveRegion === "polite"
    );
    if (!card) return null;
    const [text] = card.findAllByType(Text);
    return typeof text?.props.children === "string" ? text.props.children : null;
  }

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
      const sawError = failureMessage(view) !== null;
      expect(sawEmpty && sawError).toBe(false);
      view.unmount();
    }
  });

  /**
   * Every failure gets its own sentence, with one deliberate exception.
   *
   * The bug this whole change came out of was four different failures — a
   * refused write token, a dead session, a store the server could not match,
   * and a store whose selling access was withdrawn — arriving at the merchant
   * as the single sentence "You're not signed in to this store any more". Three
   * of the four merchants reading that signed in again and saw the same screen.
   *
   * So a shared sentence is treated as the defect it was. `SESSION_EXPIRED` and
   * `UNAUTHORIZED` are allowed to share one, and only they: `UNAUTHORIZED` is
   * what an unnamed 401/403 falls back to, and "you may not be signed in" is
   * the honest reading of a refusal the server declined to explain.
   */
  it("gives every failure its own sentence, and shares one only where the cause is one", () => {
    const byMessage = new Map<string, DropshippingState[]>();
    for (const state of FAILURE_STATES) {
      const view = renderState(state);
      const message = failureMessage(view);
      expect(message).toBeTruthy();
      byMessage.set(message as string, [...(byMessage.get(message as string) || []), state]);
      view.unmount();
    }

    const shared = [...byMessage.values()].filter((states) => states.length > 1);
    expect(shared).toEqual([["SESSION_EXPIRED", "UNAUTHORIZED"]]);
  });

  /**
   * A retry button is a claim that pressing it could change the answer. It
   * cannot for a store that is not this merchant's, one whose selling access
   * was withdrawn, or one with no seller record behind it — those need a person,
   * not a second request. A refused write token or a store context that moved on
   * *are* worth another attempt, and they keep theirs.
   *
   * The shop-binding states split along the same line, and the split is the
   * point. Four of them are verdicts on a live list that can move under the
   * merchant — a shop renamed, re-platformed or removed in the supplier's own
   * console — so re-reading it genuinely can answer differently. The fifth,
   * `SHOP_BINDING_REQUIRED`, is a fact about the connection itself: no shop is
   * chosen. Asking again returns the same answer forever, so it gets a route to
   * the screen that fixes it or no button at all, never a "Try again".
   */
  it("offers a retry only where a second attempt could answer differently", () => {
    const retryable = new Set<DropshippingState>([
      "SESSION_EXPIRED",
      "UNAUTHORIZED",
      "CSRF_INVALID",
      "STALE_STORE_CONTEXT",
      "INVALID_CREDENTIAL",
      "SUPPLIER_DISCONNECTED",
      "SHOP_NOT_AUTHORIZED",
      "SHOP_BINDING_CONFLICT",
      "SHOP_CANNOT_FULFIL",
      "SHOP_NAME_AMBIGUOUS",
      "PROVIDER_UNAVAILABLE",
      "ERROR"
    ]);

    for (const state of FAILURE_STATES) {
      const view = renderState(state);
      const hasButton = view.UNSAFE_root.findAll(
        (node) => node.props?.accessibilityRole === "button"
      ).length > 0;
      expect([state, hasButton]).toEqual([state, retryable.has(state)]);
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
 * 1a — an empty result names the search that produced it
 * ------------------------------------------------------------------ */

describe("SupplierCatalogScreen", () => {
  async function catalogue() {
    mockSearchProducts.mockResolvedValue({
      products: [],
      page: 1,
      size: 24,
      total: 0,
      hasMore: false,
      cached: false
    });
    const view = render(
      <SupplierCatalogScreen navigation={navigation()} route={{ params: { connectionId: "c1" } }} />
    );
    await settle();
    return view;
  }

  /**
   * Observed on a live simulator against staging. Searching runs on submit, so
   * typing "phone" and pausing leaves a box that says "phone" and a result set
   * that answers the blank search. The screen captioned the empty state from the
   * box and reported `Nothing matched "phone"` — a finding about a request that
   * had never been made, and one that reads exactly like the provider stocking
   * nothing.
   *
   * The distinction matters beyond tidiness: the merchant's next move after
   * "nothing matched" is to try a different word, which is the wrong move when
   * the word was never sent.
   */
  it("does not report a verdict on a term that has not been searched", async () => {
    const view = await catalogue();
    expect(view.getByText("This supplier has no products to show.")).toBeTruthy();

    fireEvent.changeText(view.getByLabelText("Search your supplier's catalogue"), "phone");
    await settle();

    expect(view.queryByText("Nothing matched “phone”.")).toBeNull();
    expect(view.getByText("This supplier has no products to show.")).toBeTruthy();
    expect(mockSearchProducts).toHaveBeenCalledTimes(1);
  });

  it("names the term once that term is the one the provider answered", async () => {
    const view = await catalogue();
    fireEvent.changeText(view.getByLabelText("Search your supplier's catalogue"), "phone");
    fireEvent.press(view.getByLabelText("Search your store"));
    await settle();

    expect(mockSearchProducts).toHaveBeenLastCalledWith(
      expect.anything(),
      "c1",
      expect.objectContaining({ filters: { keyword: "phone" } })
    );
    expect(view.getByText("Nothing matched “phone”.")).toBeTruthy();
  });

  /**
   * The in-flight case the captured term exists for: the merchant edits the box
   * while a search is still running. The answer that lands belongs to the word
   * that was sent, and must be captioned with it.
   */
  it("captions a landing result with the term that was sent, not the box's later contents", async () => {
    const view = await catalogue();
    let release: (value: unknown) => void = () => undefined;
    mockSearchProducts.mockReturnValueOnce(
      new Promise((resolve) => {
        release = resolve;
      })
    );
    fireEvent.changeText(view.getByLabelText("Search your supplier's catalogue"), "phone");
    fireEvent.press(view.getByLabelText("Search your store"));
    await settle();

    fireEvent.changeText(view.getByLabelText("Search your supplier's catalogue"), "kettle");
    await act(async () => {
      release({ products: [], page: 1, size: 24, total: 0, hasMore: false, cached: false });
      await Promise.resolve();
    });
    await settle();

    expect(view.getByText("Nothing matched “phone”.")).toBeTruthy();
    expect(view.queryByText("Nothing matched “kettle”.")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * 1b — the hub's scope gate
 * ------------------------------------------------------------------ */

describe("DropshippingHubScreen", () => {
  async function hub(nav = navigation()) {
    const view = render(<DropshippingHubScreen navigation={nav} route={{ params: {} }} />);
    await settle();
    return { view, nav };
  }

  /**
   * The reported bug, as a test. A merchant trading as "M&W Store · Open for
   * orders" was shown "Dropshipping needs a business first" — the supplier
   * gateway asked Business OS, which is a different identity from the one that
   * made them a seller. Owning a store is the whole requirement.
   */
  it("lets an existing seller-backed store straight through to Connect", async () => {
    mockResolveScope.mockResolvedValue({
      status: "ok",
      scope: { businessId: "mkt-seller:7001", storeId: "mkt-seller:7001" },
      storeName: "M&W Store",
      source: "MARKETPLACE_SELLER"
    });
    mockListConnections.mockResolvedValue([]);
    const { view } = await hub();

    await waitFor(() =>
      expect(view.getByText("Sell products you don't have to stock.")).toBeTruthy()
    );
    expect(view.getByText("Dropshipping · No supplier connected")).toBeTruthy();
    expect(view.getByText("Connect a supplier")).toBeTruthy();
    expect(view.queryByText(/business/i)).toBeNull();
  });

  /**
   * The other half of the same requirement: a merchant with no store must not
   * get the seller's screen. If these two ever converge, one of them is lying.
   */
  it("sends a merchant with no store to set one up, never to a supplier key form", async () => {
    mockResolveScope.mockResolvedValue({ status: "missing", gap: "NO_STORE" });
    mockListConnections.mockResolvedValue([]);
    const { view, nav } = await hub();

    await waitFor(() =>
      expect(view.getByText("You need a store before you can import products.")).toBeTruthy()
    );
    expect(view.queryByText("Dropshipping · No supplier connected")).toBeNull();
    expect(view.getByText("Dropshipping · No store yet")).toBeTruthy();
    expect(view.queryByText("Connect a supplier")).toBeNull();

    fireEvent.press(view.getByText("Set up"));
    expect(nav.navigate).toHaveBeenCalledWith("MerchantApply", expect.anything());
    expect(nav.navigate).not.toHaveBeenCalledWith("DropshippingConnect", expect.anything());
  });

  /**
   * A merchant awaiting review has already done the thing "Set up" would ask
   * them to do again, so the action re-checks rather than sending them back.
   */
  it("offers a re-check, not a setup form, while the store is under review", async () => {
    mockResolveScope.mockResolvedValue({ status: "missing", gap: "STORE_PENDING_REVIEW" });
    mockListConnections.mockResolvedValue([]);
    const { view, nav } = await hub();

    await waitFor(() =>
      expect(view.getByText("Your store is still being reviewed.")).toBeTruthy()
    );
    expect(view.getByText("Dropshipping · Store in review")).toBeTruthy();
    expect(view.queryByText("Set up")).toBeNull();

    fireEvent.press(view.getByText("Refresh"));
    expect(nav.navigate).not.toHaveBeenCalled();
  });

  it("keeps sending a Business OS merchant with no storefront to Business OS", async () => {
    mockResolveScope.mockResolvedValue({ status: "missing", gap: "NO_STOREFRONT" });
    mockListConnections.mockResolvedValue([]);
    const { view, nav } = await hub();

    await waitFor(() =>
      expect(view.getByText("Your business doesn't have a store yet.")).toBeTruthy()
    );
    fireEvent.press(view.getByText("Set up"));
    expect(nav.navigate).toHaveBeenCalledWith("BusinessOs", expect.anything());
  });

  /**
   * Caught on a simulator, not in this file: with the scope endpoint unreachable
   * the body said "Dropshipping didn't load" while the strip above it said "No
   * supplier connected". The second is a claim about the merchant's account, and
   * a request that never came back cannot support it.
   */
  it("does not report an absent supplier when the store check itself failed", async () => {
    mockResolveScope.mockRejectedValue(new PulseApiError("down", 500, "server_error"));
    mockListConnections.mockResolvedValue([]);
    const { view } = await hub();

    await waitFor(() =>
      expect(view.getByText("Dropshipping · Couldn't check your store")).toBeTruthy()
    );
    expect(view.queryByText("Dropshipping · No supplier connected")).toBeNull();
    expect(view.queryByText("Connect")).toBeNull();
    expect(view.getAllByText("Try again").length).toBeGreaterThan(0);
  });

  /**
   * §10. Caught in production, not here: the server answered `disabled` — the
   * feature is off on this deployment — and the merchant was shown "Couldn't
   * check your store" above "Dropshipping didn't load". Their store was fine.
   * Naming the deployment is the whole point of that code, and a retry beside it
   * only invites tapping at something no merchant can change.
   */
  it("names the deployment, not the merchant's store, when suppliers are switched off", async () => {
    mockResolveScope.mockRejectedValue(new PulseApiError("off", 404, "disabled"));
    mockListConnections.mockResolvedValue([]);
    const { view } = await hub();

    await waitFor(() =>
      expect(view.getByText("Dropshipping · Not enabled here yet")).toBeTruthy()
    );
    expect(view.queryByText("Dropshipping · Couldn't check your store")).toBeNull();
    expect(view.queryByText("Dropshipping · No supplier connected")).toBeNull();
    expect(view.queryByText("Try again")).toBeNull();
    expect(view.queryByText("Connect")).toBeNull();
    expect(
      view.getByText(
        "Supplier connections are available in the PulseSoc sandbox but aren't enabled on this server yet."
      )
    ).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * 1c — connecting a supplier
 * ------------------------------------------------------------------ */

describe("ConnectSupplierScreen", () => {
  async function connectScreen(nav = navigation()) {
    const view = render(<ConnectSupplierScreen navigation={nav} route={{ params: {} }} />);
    await settle();
    return { view, nav };
  }

  it("asks which supplier before asking for a key, and does not pretend the rest work", async () => {
    const { view } = await connectScreen();

    expect(view.getByText("Available suppliers")).toBeTruthy();
    expect(view.getByText("CJ Dropshipping")).toBeTruthy();
    // Named so a merchant knows they are coming, with no control — a button
    // that lands nowhere teaches them the app cannot be trusted.
    expect(view.getByText(/Printful/)).toBeTruthy();
    expect(view.queryByLabelText("CJ API key")).toBeNull();
  });

  /**
   * §1: once CJ is chosen, the merchant is holding a thing CJ calls a "CJ API
   * key". "Supplier access key" is our internal category and names no control
   * that exists on CJ's site, so a merchant reading it has nothing to match.
   */
  it("asks for the credential by the name CJ gives it, not ours", async () => {
    const { view } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));

    expect(view.getByText("Connect CJ Dropshipping")).toBeTruthy();
    expect(view.getByLabelText("CJ API key")).toBeTruthy();
    expect(view.getByPlaceholderText("Paste your CJ API key")).toBeTruthy();
    expect(view.queryByLabelText("Supplier access key")).toBeNull();
    expect(view.queryByText(/access key/i)).toBeNull();
  });

  /**
   * §2: the old help sent merchants to "Account → API" and told them to
   * "create a new access key" — a menu and a control CJ does not have. The
   * replacement names CJ's own controls, and stays opt-in so it does not push
   * the field off the first screen.
   */
  it("gives help in CJ's own control names and invents no menu path", async () => {
    const { view } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));

    expect(view.queryByText("Account → API")).toBeNull();
    expect(view.queryByText(/Under Apps/)).toBeNull();

    fireEvent.press(view.getByLabelText("Where do I find my CJ API key?"));
    expect(view.getByText(/Under Apps, install the API app/)).toBeTruthy();
    expect(view.getByText(/press Add API/)).toBeTruthy();
    expect(view.getByText(/choose API Key as the Type/)).toBeTruthy();
    // No step may resurrect the invented path or the invented control.
    expect(view.queryByText("Account → API")).toBeNull();
    expect(view.queryByText(/create a new access key/i)).toBeNull();
  });

  /**
   * §2: what happens to the key is stated before they paste it, in the same
   * breath as the field — not buried in a policy nobody opens. The guarantee is
   * "encrypted, and never on your phone"; the word "backend" is not used
   * because the copy guard bans it and a merchant does not need it.
   */
  it("states that the key is encrypted and never kept on the device", async () => {
    const { view } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));

    expect(
      view.getByText(
        "PulseSoc encrypts your CJ API key and never keeps it on this device. It is used only to connect your CJ account."
      )
    ).toBeTruthy();
  });

  /**
   * §4: the button says what the merchant is doing — connecting their CJ
   * account. "Find my shops" describes the backend's next call, and "shops" is
   * CJ's word for a thing the merchant has not been shown yet.
   */
  it("labels the action as connecting to CJ, not as a backend step", async () => {
    const { view } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));

    expect(view.getByLabelText("Connect to CJ")).toBeTruthy();
    expect(view.queryByLabelText("Find my shops")).toBeNull();
  });

  /**
   * §3: sandbox is stated before anything is typed. A merchant who finds out
   * after importing forty products found out too late.
   */
  it("says the connection is sandbox before the key is asked for", async () => {
    const { view } = await connectScreen();

    expect(view.getByText("Sandbox connection")).toBeTruthy();
    expect(view.getByText(/no order is placed with the supplier and nothing ships/)).toBeTruthy();
  });

  /**
   * §7: the key is a conduit, not a value the screen owns. It must never be
   * rendered back, and no provider account identifier may surface either.
   */
  it("never renders the key back, and never shows a raw provider account id", async () => {
    mockDiscoverShops.mockResolvedValue([{ externalShopId: "shop-9", name: "M&W Shop" }]);
    const { view } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));

    const field = view.getByLabelText("CJ API key");
    expect(field.props.secureTextEntry).toBe(true);
    fireEvent.changeText(field, "cj-secret-key");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect to CJ"));
    });

    await waitFor(() => expect(view.getByText("Choose a shop")).toBeTruthy());
    expect(view.queryByText(/cj-secret-key/)).toBeNull();
    expect(view.getByText("M&W Shop")).toBeTruthy();
  });

  /**
   * §9: a key that merely exists is not a connection. Nothing is saved until
   * the merchant names the shop the store should import from.
   */
  it("saves nothing until a shop is chosen", async () => {
    mockDiscoverShops.mockResolvedValue([
      { externalShopId: "shop-9", name: "M&W Shop" },
      { externalShopId: "shop-10", name: "Second Shop" }
    ]);
    mockConnectSupplier.mockResolvedValue({ id: "conn-1" });
    const { view, nav } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));
    fireEvent.changeText(view.getByLabelText("CJ API key"), "cj-secret-key");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect to CJ"));
    });
    await waitFor(() => expect(view.getByText("Choose a shop")).toBeTruthy());
    expect(mockConnectSupplier).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect Second Shop"));
    });
    await waitFor(() => expect(mockConnectSupplier).toHaveBeenCalledTimes(1));
    expect(mockConnectSupplier).toHaveBeenCalledWith(
      { businessId: "biz-1", storeId: "store-1" },
      { apiKey: "cj-secret-key", externalShopId: "shop-10" }
    );
    expect(nav.navigate).toHaveBeenCalledWith("DropshippingSuppliers", expect.anything());
  });

  /**
   * §10: a feature this deployment has switched off answers 404, and the old
   * reading of that was "we couldn't work out which store" — blaming the
   * merchant's setup for a server condition. Neither may the screen offer a
   * retry that cannot change the answer.
   */
  it("states a disabled supplier feature as a server condition, with no retry", async () => {
    mockResolveScope.mockRejectedValue(new PulseApiError("nope", 404, "disabled"));
    const { view } = await connectScreen();

    await waitFor(() =>
      expect(view.getByText(/aren't enabled on this server yet/)).toBeTruthy()
    );
    expect(view.queryByText(/which store/)).toBeNull();
    expect(view.queryByText("Try again")).toBeNull();
  });

  it("does not blame the merchant's key when the provider network is switched off", async () => {
    mockDiscoverShops.mockRejectedValue(
      new PulseApiError("nope", 503, "provider_network_disabled")
    );
    const { view } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));
    fireEvent.changeText(view.getByLabelText("CJ API key"), "cj-secret-key");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect to CJ"));
    });

    await waitFor(() => expect(view.getByText(/Your key wasn't the problem/)).toBeTruthy());
    expect(view.queryByText(/didn't work/)).toBeNull();
  });

  /**
   * The production failure, on the screen.
   *
   * Production runs with none of SUPPLIER_CREDENTIAL_KEYS,
   * SUPPLIER_CREDENTIAL_KEY_ACTIVE or SUPPLIER_ACCOUNT_INDEX_KEY set, while
   * staging has all three. So `vault.require_available()` raises, and it raises
   * inside `_bootstrap()` *before* `adapter.authenticate(api_key)` — deliberately,
   * because authenticating claims one of the three CJ account slots this egress
   * IP is allowed and a deployment that cannot persist the result must not spend
   * one. The merchant's key therefore never left PulseSoc.
   *
   * What the merchant got was "Your supplier isn't responding ... try again
   * shortly", because `credential_vault_unavailable` is a 503 and 503 was in the
   * catch-all at the bottom of `stateForError`. Two false claims in one
   * sentence: that CJ was asked, and that waiting would help.
   *
   * The three negative assertions are the load-bearing ones. Asserting only the
   * new sentence would stay green if the old one were printed beside it, and the
   * bug was never that the right words were missing — it was that the wrong ones
   * were there.
   */
  it("does not blame the supplier when this server cannot store the credential", async () => {
    mockDiscoverShops.mockRejectedValue(
      new PulseApiError("nope", 503, "credential_vault_unavailable")
    );
    const { view } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));
    fireEvent.changeText(view.getByLabelText("CJ API key"), "cj-secret-key");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect to CJ"));
    });

    await waitFor(() => expect(view.getByText(/can't store supplier credentials/)).toBeTruthy());
    // Not the supplier's fault, not the key's fault, and not worth retrying.
    expect(view.queryByText(/isn't responding/)).toBeNull();
    expect(view.queryByText(/didn't work/)).toBeNull();
    expect(view.queryByText(/try again shortly/i)).toBeNull();
  });

  /**
   * A rejected key and a working key that owns no shops used to be told apart
   * only by which dead end they reached. They are still told apart — but the
   * second is no longer a dead end.
   *
   * A CJ "shop" is an external storefront (Shopify, Woo) authorized inside the
   * merchant's CJ account. Importing products into PulseSoc needs none, because
   * PulseSoc *is* the storefront. So zero shops is the ordinary answer for a
   * merchant who sells only here, and the old copy — "your account connected,
   * but it has no shops we can sell through yet" — was false about the very
   * thing they were trying to do, and offered no way forward.
   *
   * The rejected key still says so, still says nothing about shops, and still
   * connects nothing.
   */
  it("connects an account that owns no shops, and still names a rejected key", async () => {
    mockDiscoverShops.mockRejectedValue(new PulseApiError("nope", 400, "invalid_api_key"));
    mockConnectSupplier.mockResolvedValue({ id: "conn-1" });
    const { view, nav } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));
    fireEvent.changeText(view.getByLabelText("CJ API key"), "wrong");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect to CJ"));
    });
    await waitFor(() => expect(view.getByText(/CJ API key wasn't accepted/)).toBeTruthy());
    expect(mockConnectSupplier).not.toHaveBeenCalled();

    mockDiscoverShops.mockResolvedValue([]);
    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect to CJ"));
    });
    await waitFor(() => expect(mockConnectSupplier).toHaveBeenCalledTimes(1));
    // Omitted rather than sent empty: an empty string would read as "a shop,
    // named nothing" and be checked against a list it cannot appear in.
    expect(mockConnectSupplier).toHaveBeenCalledWith(
      { businessId: "biz-1", storeId: "store-1" },
      { apiKey: "wrong", externalShopId: null }
    );
    expect(nav.navigate).toHaveBeenCalledWith("DropshippingSuppliers", expect.anything());
    // No shop chooser is shown for an account with no shops to choose between.
    expect(view.queryByText("Choose a shop")).toBeNull();
    expect(view.queryByText(/CJ API key wasn't accepted/)).toBeNull();
  });

  /**
   * The shopless connect can still fail, and when it does the merchant must
   * land on the step they came from.
   *
   * Sending them to the shop list would strand them: that list is empty, which
   * is exactly why the connect was attempted without one. They would see a
   * blank card and an error, with the key field nowhere on screen.
   */
  it("returns to the key step when a shopless connection fails", async () => {
    mockDiscoverShops.mockResolvedValue([]);
    mockConnectSupplier.mockRejectedValue(new PulseApiError("nope", 503, "provider_unavailable"));
    const { view, nav } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));
    fireEvent.changeText(view.getByLabelText("CJ API key"), "cj-secret-key");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect to CJ"));
    });

    await waitFor(() => expect(view.getByText(/isn't responding/)).toBeTruthy());
    expect(nav.navigate).not.toHaveBeenCalledWith("DropshippingSuppliers", expect.anything());
    expect(view.queryByText("Choose a shop")).toBeNull();
    expect(view.getByLabelText("CJ API key")).toBeTruthy();
  });

  /**
   * The reported bug, from the merchant's side.
   *
   * The owner of an approved, open store tapped "Connect to CJ" and read "You're
   * not signed in to this store anymore." They were signed in; the store was
   * theirs; CJ was never contacted. What the server had actually refused was the
   * write token, and with the cause stripped off in transit the screen had only
   * a 403 to go on and guessed the one thing a 403 usually means.
   *
   * Each cause the server can now name is checked here against the sentence a
   * merchant would act on. The sign-in sentence is barred from all of them: it
   * is the one instruction that is useless for every case except a real
   * expired session.
   */
  async function failConnectWith(error: PulseApiError) {
    mockDiscoverShops.mockRejectedValue(error);
    const { view } = await connectScreen();
    fireEvent.press(view.getByLabelText("Connect CJ Dropshipping"));
    fireEvent.changeText(view.getByLabelText("CJ API key"), "cj-secret-key");
    await act(async () => {
      fireEvent.press(view.getByLabelText("Connect to CJ"));
    });
    return view;
  }

  it.each([
    ["csrf", 403, /couldn't prove the request came from you/],
    ["store_not_found", 404, /couldn't match this store to your account/],
    ["store_access_revoked", 403, /can no longer sell/],
    ["store_not_approved", 403, /isn't approved to sell yet/],
    ["merchant_identity_unresolved", 409, /isn't linked to a seller account yet/],
    ["forbidden", 403, /role in this store can't connect suppliers/]
  ])("does not tell an owner they are signed out when the cause is %s", async (code, status, copy) => {
    const view = await failConnectWith(new PulseApiError("no", status as number, code as string));

    await waitFor(() => expect(view.getByText(copy as RegExp)).toBeTruthy());
    expect(view.queryByText(/not signed in to this store/i)).toBeNull();
    // Whatever went wrong, the key the merchant pasted is not part of the answer.
    expect(view.queryByText(/cj-secret-key/)).toBeNull();
  });

  /** A session that really has expired is still the one case that says so. */
  it("still says to sign in when the session is the thing that failed", async () => {
    const view = await failConnectWith(new PulseApiError("no", 401, "login_required"));

    await waitFor(() => expect(view.getByText(/session has expired/i)).toBeTruthy());
  });

  /**
   * §10: a store context that moved on is repaired in place. The merchant cached
   * a scope, their store gained a workspace behind them, and the scope they are
   * holding no longer names their canonical store. That is a client-cache
   * problem with a client-side fix — re-resolve and try again — so the screen
   * does it rather than sending the merchant out to restart the app or sign in
   * again for a session that was never the problem.
   */
  it("refreshes a store context that moved on instead of sending the merchant to sign in", async () => {
    const view = await failConnectWith(new PulseApiError("no", 409, "stale_store_context"));

    await waitFor(() => expect(view.getByText(/store details moved on/i)).toBeTruthy());
    expect(view.queryByText(/not signed in to this store/i)).toBeNull();
    // Once on mount, once because the screen repaired itself.
    await waitFor(() => expect(mockResolveScope).toHaveBeenCalledTimes(2));
  });

  /**
   * A store failure found while resolving scope — before a supplier is even
   * chosen — reads the same way, and offers no retry where a retry cannot help.
   * This is the path that used to answer "we couldn't work out which store to
   * connect this supplier to" for every one of them.
   */
  it("names a store-authority failure found before the supplier was chosen", async () => {
    mockResolveScope.mockRejectedValue(new PulseApiError("no", 403, "store_access_revoked"));
    const { view } = await connectScreen();

    await waitFor(() => expect(view.getByText(/can no longer sell/)).toBeTruthy());
    expect(view.queryByText(/couldn't work out which store/)).toBeNull();
    expect(view.queryByText(/not signed in to this store/i)).toBeNull();
    expect(view.queryByText("Try again")).toBeNull();
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
 * 2b — Suppliers: choosing the shop orders are sent to
 * ------------------------------------------------------------------ */

/**
 * The transition that had a server and no door.
 *
 * `bind_shop` was routed, serviced and tested; `connection_shops` was routed
 * and tested. Nothing in this app called either, and nothing could: the shop
 * list can only be read with the merchant's stored credential, which lives in
 * the vault with no copy on the device. So a connection that connected without
 * a shop — the normal shape, because selling here means PulseSoc *is* the
 * storefront — could import, publish and sell, and then refuse every one of its
 * own orders with `shop_binding_required` forever.
 *
 * These tests are written from the surface that was missing, so the first one
 * asserts the whole path: the honest status, the list, the choice, and the
 * re-read that confirms it. Asserting that a button calls a mock would not have
 * been the claim.
 */
describe("SuppliersScreen — choosing a fulfilment shop", () => {
  const UNBOUND = connection({ externalShopId: null });

  function shop(over: Record<string, unknown> = {}) {
    return {
      externalShopId: "shop-a",
      name: "Main shop",
      platform: "API",
      fulfillable: true,
      unfulfillableReason: null,
      ...over
    };
  }

  async function suppliers(rows = [UNBOUND]) {
    mockListConnections.mockResolvedValue(rows);
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();
    return view;
  }

  async function openPicker(rows = [UNBOUND]) {
    const view = await suppliers(rows);
    await waitFor(() => expect(view.getByLabelText(/Choose a fulfilment shop/)).toBeTruthy());
    await act(async () => {
      fireEvent.press(view.getByLabelText(/Choose a fulfilment shop/));
    });
    await settle();
    return view;
  }

  it("takes a connection with no shop from refusal through to a recorded choice", async () => {
    mockListConnectionShops.mockResolvedValue({ shops: [shop()], boundShopId: null });
    mockBindConnectionShop.mockResolvedValue(undefined);

    const view = await suppliers();

    // It does not claim to be working. That sentence is what this row said
    // before any of this existed, over a connection that could not ship.
    await waitFor(() => expect(view.getByText(/orders need a fulfilment shop/i)).toBeTruthy());
    expect(view.queryByText("Connected and working")).toBeNull();
    // And importing is untouched, which is the whole reason a shopless
    // connection is allowed in the first place.
    expect(view.getByText("Find products")).toBeTruthy();

    await act(async () => {
      fireEvent.press(view.getByLabelText(/Choose a fulfilment shop/));
    });
    await settle();
    await waitFor(() => expect(view.getByText("Main shop")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByLabelText(/Send orders to Main shop/));
    });
    await settle();

    expect(mockBindConnectionShop).toHaveBeenCalledWith(expect.anything(), "conn-1", "shop-a");
    // The connection list is re-read rather than patched locally: it is what
    // every other surface reads, and one source for "what is bound" is worth
    // the extra call.
    await waitFor(() => expect(mockListConnections).toHaveBeenCalledTimes(2));
  });

  it("offers no shop picker to a connection that already has one", async () => {
    // `connection()` is bound by default, as most fixtures here are.
    const view = await suppliers([connection()]);
    await waitFor(() => expect(view.getByText("Connected and working")).toBeTruthy());
    expect(view.queryByLabelText(/Choose a fulfilment shop/)).toBeNull();
  });

  it("offers no shop picker to a connection that could not read a list anyway", async () => {
    // An expired credential cannot fetch shops, so a picker on it would open
    // straight onto a reauth error the merchant did not ask for.
    const view = await suppliers([connection({ status: "AUTH_EXPIRED", externalShopId: null })]);
    await waitFor(() => expect(view.getByText(/credential expired/i)).toBeTruthy());
    expect(view.queryByLabelText(/Choose a fulfilment shop/)).toBeNull();
  });

  /**
   * The state of this merchant's live account, and the most likely outcome of
   * anyone opening this list. The server used to answer it with a 422 — CJ
   * refuses the call for an account that owns no storefront — which matches
   * none of the client's status classes and so rendered as "Something went
   * wrong" for the ordinary case.
   */
  it("says an account with no shops has none, and where to make one", async () => {
    mockListConnectionShops.mockResolvedValue({ shops: [], boundShopId: null });
    const view = await openPicker();

    await waitFor(() => expect(view.getByText("This supplier account has no shops.")).toBeTruthy());
    expect(view.getByText(/Create one there, then reopen this list/)).toBeTruthy();
    expect(view.queryByText(/didn't load/)).toBeNull();
  });

  it("does not read a failed shop read as an account with no shops", async () => {
    mockListConnectionShops.mockRejectedValue(new PulseApiError("x", 503, "provider_unavailable"));
    const view = await openPicker();

    await waitFor(() => expect(view.getByText(/supplier isn't responding/i)).toBeTruthy());
    expect(view.queryByText("This supplier account has no shops.")).toBeNull();
  });

  /**
   * A shop that cannot take an API order gets no control at all, not a disabled
   * one. The verdict is the server's — `dispatch_shop`'s own answer, returned
   * per row — and it is rendered structurally so there is no state in which the
   * row is tappable and the refusal arrives afterwards. That is exactly what
   * used to happen: binding said yes, and the merchant found out one lost order
   * later that the shop was never a destination.
   */
  it("shows why an unusable shop cannot be chosen, and offers no way to choose it", async () => {
    mockListConnectionShops.mockResolvedValue({
      shops: [
        shop({ externalShopId: "shop-b", name: "Storefront", platform: "Shopify",
               fulfillable: false, unfulfillableReason: "api_shop_binding_required" }),
        shop()
      ],
      boundShopId: null
    });
    const view = await openPicker();

    await waitFor(() => expect(view.getByText("Storefront")).toBeTruthy());
    expect(view.getByText(/won't take orders for this shop from an outside app/)).toBeTruthy();
    expect(view.queryByLabelText(/Send orders to Storefront/)).toBeNull();
    // And the shop that can take one is still choosable, so this is not just a
    // list with every button removed.
    expect(view.getByLabelText(/Send orders to Main shop/)).toBeTruthy();
  });

  it("says an unrecognised refusal is a refusal rather than saying nothing", async () => {
    // Silence would leave the row looking ordinary while being the one row with
    // no button — which reads as a rendering bug, not as a verdict.
    mockListConnectionShops.mockResolvedValue({
      shops: [shop({ fulfillable: false, unfulfillableReason: "something_new" })],
      boundShopId: null
    });
    const view = await openPicker();

    await waitFor(() => expect(view.getByText("Can't take orders.")).toBeTruthy());
    expect(view.queryByLabelText(/Send orders to Main shop/)).toBeNull();
  });

  /**
   * A refusal at bind time means the list on screen has gone stale — the shop
   * was renamed, re-platformed or removed in the supplier's console since it
   * was drawn. So the list comes down with the explanation: leaving it up would
   * invite the merchant to tap the same wrong row again.
   */
  it("explains a refused choice in its own terms and takes the stale list down", async () => {
    mockListConnectionShops.mockResolvedValue({ shops: [shop()], boundShopId: null });
    mockBindConnectionShop.mockRejectedValue(new PulseApiError("x", 403, "shop_not_authorized"));
    const view = await openPicker();
    await waitFor(() => expect(view.getByText("Main shop")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByLabelText(/Send orders to Main shop/));
    });
    await settle();

    await waitFor(() => expect(view.getByText(/isn't on your supplier account any more/i)).toBeTruthy());
    // The specific misreading this replaces: `shop_not_authorized` is a 403, so
    // it used to reach a merchant with a perfectly healthy session as "you're
    // not signed in to this store any more".
    expect(view.queryByText(/not signed in to this store/i)).toBeNull();
    expect(view.queryByText("Main shop")).toBeNull();
  });

  it("does not report a refused bind as a bound shop", async () => {
    mockListConnectionShops.mockResolvedValue({ shops: [shop()], boundShopId: null });
    mockBindConnectionShop.mockRejectedValue(new PulseApiError("x", 409, "connection_binding_conflict"));
    const view = await openPicker();
    await waitFor(() => expect(view.getByText("Main shop")).toBeTruthy());

    await act(async () => {
      fireEvent.press(view.getByLabelText(/Send orders to Main shop/));
    });
    await settle();

    await waitFor(() =>
      expect(view.getByText(/already sends orders to a different shop/i)).toBeTruthy());
    // The row behind it still says what it said: nothing was bound, so nothing
    // about the connection changed.
    expect(view.getByText(/orders need a fulfilment shop/i)).toBeTruthy();
    expect(mockListConnections).toHaveBeenCalledTimes(1);
  });

  it("reads the shop list for the connection whose picker was opened", async () => {
    mockListConnectionShops.mockResolvedValue({ shops: [shop()], boundShopId: null });
    await openPicker([connection({ id: "conn-7", externalShopId: null })]);
    expect(mockListConnectionShops).toHaveBeenCalledWith(expect.anything(), "conn-7");
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

  /* ---------------------------------------------------------------- *
   * Which variant this product sells
   *
   * `SUPPLIER_VARIANT_UNBOUND` was a refusal with no answer. The supplier
   * screen pre-selects every in-stock variant, so the ordinary import of a
   * two-size t-shirt produced a draft the publish gate declined; the code had
   * no merchant-readable copy, so it was rendered raw; and `bind-product` had
   * no caller on any screen, so there was nothing to do about it. These tests
   * are about the pair — the words, and the remedy.
   * ---------------------------------------------------------------- */

  /** An unbound dropship draft with two candidate variants — a default import. */
  function unbound(): ImportedDraft {
    const base = draft();
    return {
      ...base,
      variants: [
        base.variants[0],
        {
          ...base.variants[0],
          variantId: 2,
          options: { Colour: "Black" },
          sku: "MUG-B",
          providerVariantId: "pv-2"
        }
      ],
      supplier: { ...base.supplier, providerVariantId: null },
      validation: { publishable: false, problems: ["SUPPLIER_VARIANT_UNBOUND"] }
    };
  }

  it("explains the unbound refusal in words instead of printing its code", async () => {
    const { view } = await renderDraft(unbound());

    await waitFor(() =>
      expect(view.getByText(/Choose which variant you're selling/)).toBeTruthy()
    );
    // The regression this replaces: the code itself on the merchant's screen.
    expect(view.queryByText("SUPPLIER_VARIANT_UNBOUND")).toBeNull();
  });

  it("offers the choice that answers the refusal", async () => {
    const { view } = await renderDraft(unbound());

    await waitFor(() => expect(view.getByText("Which variant are you selling?")).toBeTruthy());
    expect(view.getByLabelText("Sell White")).toBeTruthy();
    expect(view.getByLabelText("Sell Black")).toBeTruthy();
  });

  it("will not bind until the merchant has actually picked one", async () => {
    // Two steps, because the server accepts nothing→one and refuses one→another.
    // A single tap that bound immediately would make a mis-tap permanent.
    const { view } = await renderDraft(unbound());

    await waitFor(() => expect(view.getByLabelText(/Confirm the variant/)).toBeTruthy());
    const confirm = view.getByLabelText("Confirm the variant this product sells");
    expect(confirm.props.accessibilityState.disabled).toBe(true);

    fireEvent.press(view.getByLabelText("Sell Black"));
    await waitFor(() =>
      expect(
        view.getByLabelText("Confirm the variant this product sells").props.accessibilityState
          .disabled
      ).toBe(false)
    );
  });

  it("binds the variant the merchant chose, named by its supplier id", async () => {
    mockBindDraftVariant.mockResolvedValue(undefined);
    const bound = {
      ...unbound(),
      supplier: { ...unbound().supplier, providerVariantId: "pv-2" },
      validation: { publishable: true, problems: [] }
    };
    const { view } = await renderDraft(unbound());

    await waitFor(() => expect(view.getByLabelText("Sell Black")).toBeTruthy());
    fireEvent.press(view.getByLabelText("Sell Black"));
    mockGetImportedProduct.mockResolvedValue(bound);
    fireEvent.press(view.getByLabelText("Confirm the variant this product sells"));

    await waitFor(() => expect(mockBindDraftVariant).toHaveBeenCalled());
    // `pv-2`, not the listing's own variant id and not the first variant: the
    // supplier's identifier for the row the merchant pressed.
    expect(mockBindDraftVariant.mock.calls[0][2]).toEqual({
      listingId: 77,
      providerProductId: "ext-1",
      providerVariantId: "pv-2"
    });
  });

  it("takes the cleared verdict from the server rather than assuming it", async () => {
    // Binding succeeding is not the same claim as the draft having become
    // publishable — only the evaluator can make that one. So the screen re-reads
    // the draft, and the problem disappears because the server stopped saying it.
    mockBindDraftVariant.mockResolvedValue(undefined);
    const { view } = await renderDraft(unbound());
    await waitFor(() => expect(view.getByLabelText("Sell Black")).toBeTruthy());
    const readsBefore = mockGetImportedProduct.mock.calls.length;
    fireEvent.press(view.getByLabelText("Sell Black"));

    mockGetImportedProduct.mockResolvedValue({
      ...unbound(),
      supplier: { ...unbound().supplier, providerVariantId: "pv-2" },
      validation: { publishable: true, problems: [] }
    });
    fireEvent.press(view.getByLabelText("Confirm the variant this product sells"));

    // The re-read is the mechanism, so it is asserted rather than inferred from
    // the screen settling into the right state — a screen that wrote the cleared
    // verdict into its own state would look identical here.
    await waitFor(() =>
      expect(mockGetImportedProduct.mock.calls.length).toBeGreaterThan(readsBefore)
    );
    await waitFor(() => expect(view.queryByText("Which variant are you selling?")).toBeNull());
    expect(view.queryByText(/Choose which variant you're selling/)).toBeNull();
    expect(view.getByText(/Orders go to your supplier for Black/)).toBeTruthy();
  });

  it("keeps the merchant's unsaved typing when they choose a variant", async () => {
    // The chooser re-reads the draft, and re-reading used to mean `adopt`, which
    // resets the form from the response. A merchant who retitled the product and
    // then picked a variant would have watched their words vanish.
    mockBindDraftVariant.mockResolvedValue(undefined);
    const { view } = await renderDraft(unbound());

    await waitFor(() => expect(view.getByLabelText("Title")).toBeTruthy());
    fireEvent.changeText(view.getByLabelText("Title"), "My Own Mug Name");
    fireEvent.press(view.getByLabelText("Sell Black"));

    mockGetImportedProduct.mockResolvedValue({
      ...unbound(),
      title: "Ceramic Mug",
      supplier: { ...unbound().supplier, providerVariantId: "pv-2" },
      validation: { publishable: true, problems: [] }
    });
    fireEvent.press(view.getByLabelText("Confirm the variant this product sells"));

    await waitFor(() => expect(mockBindDraftVariant).toHaveBeenCalled());
    expect(view.getByLabelText("Title").props.value).toBe("My Own Mug Name");
  });

  it("changes nothing and says so when the bind is refused", async () => {
    mockBindDraftVariant.mockRejectedValue(new Error("binding_conflict"));
    const { view } = await renderDraft(unbound());

    await waitFor(() => expect(view.getByLabelText("Sell Black")).toBeTruthy());
    fireEvent.press(view.getByLabelText("Sell Black"));
    fireEvent.press(view.getByLabelText("Confirm the variant this product sells"));

    await waitFor(() => expect(view.getByText(/couldn't be set/)).toBeTruthy());
    // Still unbound, still offering the choice. A failed write that hid the
    // control would leave the merchant with a refusal and no way back to it.
    expect(view.getByText("Which variant are you selling?")).toBeTruthy();
    // And the pick itself survives, which is a separate claim from the card
    // being on screen: clearing `pendingVariantId` in the catch leaves the
    // chooser visible with the confirm button disabled again, so the words "try
    // again" sit beside a control that cannot be pressed. Error and no way back
    // to the action — the same shape as error-and-empty. It survived the
    // mutation battery until these two lines existed.
    expect(view.getByLabelText("Sell Black").props.accessibilityState.checked).toBe(true);
    expect(
      view.getByLabelText("Confirm the variant this product sells").props.accessibilityState
        .disabled
    ).toBe(false);
  });

  it("states the bound variant instead of offering a chooser that cannot change it", async () => {
    const { view } = await renderDraft({
      ...unbound(),
      supplier: { ...unbound().supplier, providerVariantId: "pv-1" },
      validation: { publishable: true, problems: [] }
    });

    await waitFor(() => expect(view.getByText("What this product sells")).toBeTruthy());
    expect(view.getByText(/Orders go to your supplier for White/)).toBeTruthy();
    expect(view.queryByText("Which variant are you selling?")).toBeNull();
  });

  it("asks a stocked listing nothing, because it places no supplier order", async () => {
    // The merchant holds this inventory themselves. There is no supplier order
    // and so nothing to bind; a chooser here would invent a decision.
    const { view } = await renderDraft({
      ...unbound(),
      supplier: { ...unbound().supplier, fulfillmentMode: "STOCKED", providerVariantId: null },
      validation: { publishable: true, problems: [] }
    });

    await waitFor(() => expect(view.getByText("Variants and pricing")).toBeTruthy());
    expect(view.queryByText("Which variant are you selling?")).toBeNull();
    expect(view.queryByText("What this product sells")).toBeNull();
  });

  it("puts the two newest price refusals in words too", async () => {
    // Added in the same drift as the unbound one, and missing for the same
    // reason: the mobile list is a second copy of a Python enumeration.
    const { view } = await renderDraft(
      draft({
        validation: {
          publishable: false,
          problems: ["VARIANT_PRICE_SPREAD", "PRICE_ABOVE_CHECKOUT_LIMIT"]
        }
      })
    );

    await waitFor(() => expect(view.getByText(/Checkout charges one price per product/)).toBeTruthy());
    expect(view.getByText(/above what checkout can charge/)).toBeTruthy();
    expect(view.queryByText("VARIANT_PRICE_SPREAD")).toBeNull();
    expect(view.queryByText("PRICE_ABOVE_CHECKOUT_LIMIT")).toBeNull();
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

/* ------------------------------------------------------------------ *
 * 9 — supplier orders: the sale that owes a supplier purchase
 * ------------------------------------------------------------------ */

describe("DropshippingOrdersScreen", () => {
  const route = { params: { connectionId: "conn-1" } };

  function obligation(over: Partial<SupplierObligation> = {}): SupplierObligation {
    return {
      orderId: 41,
      listingId: 14,
      title: "Ceramic Mug",
      quantity: 2,
      amountCents: 4000,
      currency: "USD",
      orderStatus: "paid",
      paidAt: null,
      orderedAt: null,
      provider: "cj",
      providerProductId: "ext-1",
      providerVariantId: "pv-1",
      supplierSku: "CJ-1",
      supplierCostCents: 820,
      supplierCostCurrency: "USD",
      intentId: null,
      blockers: [],
      canPlaceSupplierOrder: true,
      state: "AWAITING_SUPPLIER_ORDER",
      supplierOrderPlaced: false,
      providerOrderId: null,
      supplierOrderStatus: null,
      lastError: null,
      updatedAt: null,
      ...over
    };
  }

  async function renderOrders(
    obligations: SupplierObligation[],
    options: { isSandbox?: boolean; params?: Record<string, unknown> | undefined } = {}
  ) {
    mockListSupplierObligations.mockResolvedValue({
      obligations,
      isSandbox: options.isSandbox ?? true
    });
    const nav = navigation();
    const view = render(
      <DropshippingOrdersScreen
        navigation={nav}
        route={"params" in options ? ({ params: options.params } as any) : route}
      />
    );
    await settle();
    return { view, nav };
  }

  it("renders a row for each sale that owes a supplier purchase", async () => {
    // The test that would have caught gap 14 at the screen. Before this list
    // existed the screen rendered a permanent "no data" note, so every
    // assertion about its other states passed over an empty surface.
    const { view } = await renderOrders([obligation()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());
    expect(view.getByText(/Order #41 · 2 ×/)).toBeTruthy();
  });

  it("says no supplier order has been placed rather than leaving the row blank", async () => {
    const { view } = await renderOrders([obligation()]);
    await waitFor(() => expect(view.getByText("No supplier order yet")).toBeTruthy());
  });

  it("shows the merchant what the supplier purchase will cost them", async () => {
    const { view } = await renderOrders([obligation()]);
    await waitFor(() => expect(view.getByText(/Your supplier cost/)).toBeTruthy());
  });

  it("reports an unknown supplier cost as unavailable rather than as zero", async () => {
    // A merchant reading $0.00 here concludes the supplier purchase is free.
    const { view } = await renderOrders([obligation({ supplierCostCents: null })]);
    await waitFor(() => expect(view.getByText(/not available/)).toBeTruthy());
    expect(view.queryByText(/\$0\.00/)).toBeNull();
  });

  it("never tells a merchant an unconfirmed order was not placed", async () => {
    // UNKNOWN means the write to the supplier could not be confirmed, so a
    // purchase may already exist. "No supplier order yet" here would invite a
    // second one, and duplicate supplier orders are real money.
    const { view } = await renderOrders([
      obligation({ state: "UNKNOWN", supplierOrderPlaced: true, intentId: "cjf_1" })
    ]);
    await waitFor(() => expect(view.getByText(/do not re-order/i)).toBeTruthy());
    expect(view.queryByText("No supplier order yet")).toBeNull();
  });

  it("says what the merchant can do about a refusal, not which code we raised", async () => {
    // This test used to be called "shows a supplier's refusal in the supplier's
    // own words" and asserted `getByText("preflight_blocked")`. Both halves of
    // that name were false and the assertion pinned the falsehood: nothing a
    // provider says can reach `last_error` — `suppliers/errors.py` exists to
    // guarantee it — and the value is an identifier written in Python. The
    // screen was showing a merchant `preflight_blocked`, and a green test was
    // the reason nobody noticed.
    //
    // It was also one word for about a dozen causes, because `dispatch`
    // flattened them before storing. So both halves of the fix are checked
    // together: a distinguished cause arrives distinguished, and as a sentence.
    const { view } = await renderOrders([
      obligation({
        state: "BLOCKED",
        supplierOrderPlaced: true,
        intentId: "cjf_2",
        lastError: "supplier_quote_expired"
      })
    ]);
    await waitFor(() => expect(view.getByText(/shipping quote expired/i)).toBeTruthy());
    expect(view.queryByText("supplier_quote_expired")).toBeNull();
  });

  it("does not tell a merchant their supplier refused an order it never saw", async () => {
    // Every cause `PREFLIGHT_REASONS` names is raised before `dispatch` calls
    // `_sending`, and the handler turns anything already sent into `UNKNOWN`
    // first — so `BLOCKED` means nothing went out. The copy said "Your supplier
    // refused this order", which sends a merchant to argue with their supplier
    // about a message the supplier never sent.
    const { view } = await renderOrders([
      obligation({
        state: "BLOCKED",
        supplierOrderPlaced: true,
        intentId: "cjf_3",
        lastError: "supplier_cost_changed"
      })
    ]);
    await waitFor(() => expect(view.getByText(/review and approve the new cost/i)).toBeTruthy());
    expect(view.queryByText(/refused/i)).toBeNull();
  });

  it("does not report an unconfirmed send as a failure", async () => {
    // `awaiting_create_readback` is written *after* the write, when the outcome
    // is unknown. Reason copy that reads like a failure here is an instruction
    // to order the same goods twice, which is the one mistake in this subsystem
    // that costs real money.
    const { view } = await renderOrders([
      obligation({
        state: "UNKNOWN",
        supplierOrderPlaced: true,
        intentId: "cjf_4",
        lastError: "awaiting_create_readback"
      })
    ]);
    // The reason line says a send happened; the state line above it carries the
    // "do not re-order" instruction. Two lines, one each, and neither repeating
    // the other — the first version of this copy said "do not re-order" twice,
    // which this assertion caught as an ambiguous match.
    await waitFor(() =>
      expect(view.getByText(/waiting for them to confirm it/i)).toBeTruthy()
    );
    expect(view.getByText(/do not re-order/i)).toBeTruthy();
    expect(view.queryByText(/could not|failed|refused/i)).toBeNull();
  });

  it("renders a reason it has never heard of as unnamed, not as the code", async () => {
    // The fallback that matters more than the state one: falling through here
    // used to mean printing the identifier itself.
    const { view } = await renderOrders([
      obligation({ state: "BLOCKED", lastError: "supplier_ate_the_parcel" })
    ]);
    await waitFor(() => expect(view.getByText(/cannot name yet/i)).toBeTruthy());
    expect(view.queryByText("supplier_ate_the_parcel")).toBeNull();
  });

  it("renders a state it has never heard of as unrecognised, not as good news", async () => {
    // The gap-13 failure mode at a new seam: a state added on the Python side
    // that this build predates. It must not read as "placed".
    const { view } = await renderOrders([obligation({ state: "TELEPORTED" })]);
    await waitFor(() => expect(view.getByText(/does not recognise/)).toBeTruthy());
    expect(view.queryByText("Placed with your supplier")).toBeNull();
  });

  it("promises nothing is sent to the supplier only when the server says so", async () => {
    const { view } = await renderOrders([obligation()], { isSandbox: true });
    await waitFor(() => expect(view.getByText("Sandbox fulfilment")).toBeTruthy());
  });

  it("makes no sandbox promise the server did not make", async () => {
    // The whole point of reading this from the payload. A hardcoded card would
    // keep reassuring merchants after production fulfilment was switched on.
    const { view } = await renderOrders([obligation()], { isSandbox: false });
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());
    expect(view.queryByText("Sandbox fulfilment")).toBeNull();
  });

  it("asks for a supplier before claiming there are no sales to fulfil", async () => {
    // Reached from a hub tile with no connection bound. "No sales to fulfil
    // yet" would be a claim about the merchant's sales that this screen never
    // checked, and it points them nowhere.
    const { view } = await renderOrders([], { params: undefined });
    await waitFor(() => expect(view.getByText("Connect a supplier first.")).toBeTruthy());
    expect(mockListSupplierObligations).not.toHaveBeenCalled();
  });

  it("distinguishes an empty backlog from an unasked question", async () => {
    const { view } = await renderOrders([]);
    await waitFor(() => expect(view.getByText("No sales to fulfil yet.")).toBeTruthy());
  });

  it("never draws a failed request as an empty backlog", async () => {
    // Rule 1 at the most expensive seam in the app: a merchant who reads "no
    // sales to fulfil" through a failed request ships nothing.
    mockListSupplierObligations.mockRejectedValue(new PulseApiError("nope", 500));
    const view = render(<DropshippingOrdersScreen navigation={navigation()} route={route} />);
    await settle();
    await waitFor(() => expect(view.queryByText("No sales to fulfil yet.")).toBeNull());
    expect(view.queryByText("Ceramic Mug")).toBeNull();
  });

  it("clears a stale backlog when a refresh fails", async () => {
    // The rows on screen are money the merchant owes. Leaving them under a
    // failed refresh is a backlog they may already have handled, or one that
    // has grown without them being told.
    const { view } = await renderOrders([obligation()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockListSupplierObligations.mockRejectedValue(new PulseApiError("nope", 500));
    const list = view.UNSAFE_getByType(FlatList as any);
    await act(async () => {
      await list.props.refreshControl.props.onRefresh();
    });
    await waitFor(() => expect(view.queryByText("Ceramic Mug")).toBeNull());
    // The sandbox card is the part a mutation battery caught this test missing.
    // The rows themselves also disappear because an error state owns the list's
    // `data`, so asserting only on the rows passes whether or not the state was
    // cleared. This card is drawn from the header regardless of that state, so
    // it is the one thing on screen that reveals a stale response still held.
    expect(view.queryByText("Sandbox fulfilment")).toBeNull();
  });

  it("states its remaining gap in the words the gap list holds", async () => {
    // Not a sentence written here. A screen that renders its own prose beside a
    // mapped gap entry is the enumeration copied twice, with the copy in prose.
    const { view } = await renderOrders([obligation()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());
    DROPSHIPPING_DATA_GAPS.forEach((gap) => {
      expect(view.getByText(gap.needs)).toBeTruthy();
    });
  });

  it("reads out the bound variant's supplier SKU, which is what the order matches on", async () => {
    // The gap-15 defect as a merchant met it. This line used to show the
    // provider's variant id while the supplier order was matched on the SKU, so
    // the identifier a merchant read out was not the identifier that had to
    // agree for the order to be accepted.
    const { view } = await renderOrders([obligation({ supplierSku: "CJ-VARIANT-1" })]);
    await waitFor(() => expect(view.getByText(/CJ-VARIANT-1/)).toBeTruthy());
  });

  it("falls back to the variant id rather than showing a blank identifier", async () => {
    const { view } = await renderOrders([
      obligation({ supplierSku: null, blockers: ["SUPPLIER_SKU_MISSING"], canPlaceSupplierOrder: false })
    ]);
    await waitFor(() => expect(view.getByText(/pv-1/)).toBeTruthy());
  });

  it("gives every reason a sale cannot be ordered, not just the first", async () => {
    // The shape of defect this repo keeps making: a merchant fixes the one
    // reason shown, comes back, and finds another. They are independent
    // conditions, so all of them are rendered.
    const { view } = await renderOrders([
      obligation({
        supplierSku: null,
        supplierCostCents: null,
        blockers: ["SHOP_BINDING_REQUIRED", "SUPPLIER_SKU_MISSING", "SUPPLIER_COST_UNKNOWN"],
        canPlaceSupplierOrder: false
      })
    ]);
    await waitFor(() =>
      expect(view.getByText(SUPPLIER_OBLIGATION_BLOCKER_COPY.SHOP_BINDING_REQUIRED)).toBeTruthy()
    );
    expect(view.getByText(SUPPLIER_OBLIGATION_BLOCKER_COPY.SUPPLIER_SKU_MISSING)).toBeTruthy();
    expect(view.getByText(SUPPLIER_OBLIGATION_BLOCKER_COPY.SUPPLIER_COST_UNKNOWN)).toBeTruthy();
  });

  it("counts the sales that cannot be ordered separately from the ones merely waiting", async () => {
    // "Waiting" and "cannot go" are different problems. Every row reads
    // "no supplier order yet" while fulfilment is off, so a single count would
    // hide the ones that need the merchant to change something.
    const { view } = await renderOrders([
      obligation(),
      obligation({ orderId: 42, blockers: ["SUPPLIER_SKU_MISSING"], canPlaceSupplierOrder: false })
    ]);
    await waitFor(() => expect(view.getByText(/2 of these have no supplier order yet/)).toBeTruthy());
    expect(view.getByText(/1 could not be ordered as things stand/)).toBeTruthy();
  });

  it("says nothing about blockers when there are none", async () => {
    const { view } = await renderOrders([obligation()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());
    expect(view.queryByText(/could not be ordered as things stand/)).toBeNull();
    SUPPLIER_OBLIGATION_BLOCKERS.forEach((blocker) => {
      expect(view.queryByText(SUPPLIER_OBLIGATION_BLOCKER_COPY[blocker])).toBeNull();
    });
  });

  it("does not call an already-placed order blocked", async () => {
    // The server names `SUPPLIER_ORDER_ALREADY_PLACED` on a row it has already
    // fulfilled, which is true and is not a problem. The state pill says it
    // better, and repeating it as a warning reads as a fault.
    const { view } = await renderOrders([
      obligation({
        state: "LINKED",
        supplierOrderPlaced: true,
        intentId: "cjf_3",
        providerOrderId: "90001",
        blockers: ["SUPPLIER_ORDER_ALREADY_PLACED"],
        canPlaceSupplierOrder: false
      })
    ]);
    await waitFor(() => expect(view.getByText("Placed with your supplier")).toBeTruthy());
    expect(view.queryByText(SUPPLIER_OBLIGATION_BLOCKER_COPY.SUPPLIER_ORDER_ALREADY_PLACED)).toBeNull();
    expect(view.queryByText(/could not be ordered as things stand/)).toBeNull();
  });

  it("renders a blocker it has never heard of as unrecognised, not as nothing", async () => {
    // Dropping it would leave a merchant a row that cannot be ordered with no
    // reason on it, which reads as a bug in the screen rather than as something
    // to go and fix.
    const { view } = await renderOrders([
      obligation({ blockers: ["CUSTOMS_PAPERWORK_FROM_A_NEWER_SERVER"], canPlaceSupplierOrder: false })
    ]);
    await waitFor(() => expect(view.getByText(/stops it being sent to your supplier/)).toBeTruthy());
    expect(view.queryByText(/CUSTOMS_PAPERWORK/)).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * 10 — sync & issues
 *
 * Added because a mutation battery found this screen had no tests at all
 * while rendering the same gap list the supplier-orders screen does. Every
 * defect that list was introduced to prevent could be reintroduced here and
 * nothing would have said so — which is the same shape as the supplier-orders
 * screen passing every assertion in this file over an empty surface.
 * ------------------------------------------------------------------ */

describe("DropshippingSyncScreen", () => {
  const route = { params: { connectionId: "conn-1", title: "Sync & issues" } };

  function product(over: Record<string, unknown> = {}) {
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

  async function renderSync(
    options: {
      items?: Record<string, unknown>[];
      connections?: SupplierConnection[];
      failProducts?: boolean;
    } = {}
  ) {
    const items = options.items ?? [product()];
    mockListConnections.mockResolvedValue(options.connections ?? [connection()]);
    if (options.failProducts) {
      mockListImportedProducts.mockRejectedValue(new PulseApiError("nope", 500));
    } else {
      mockListImportedProducts.mockResolvedValue({ items, count: items.length });
    }
    const nav = navigation();
    const view = render(<DropshippingSyncScreen navigation={nav} route={route as any} />);
    await settle();
    return { view, nav };
  }

  it("says nothing is wrong only when both sources actually loaded", async () => {
    const { view } = await renderSync();
    await waitFor(() => expect(view.getByText("Nothing needs your attention")).toBeTruthy());
  });

  it("never claims a healthy catalogue when one of the two reads failed", async () => {
    // The screen's own header comment promises this. A merchant who reads
    // "nothing needs your attention" through a failed product read believes a
    // catalogue is healthy while every import is silently broken.
    const { view } = await renderSync({ failProducts: true });
    await waitFor(() => expect(view.queryByText("Nothing needs your attention")).toBeNull());
  });

  it("puts the fixable problem in words rather than leaving its code on screen", async () => {
    const { view } = await renderSync({ items: [product({ syncState: "UNAVAILABLE" })] });
    await waitFor(() =>
      expect(view.getByText("Your supplier no longer offers this product")).toBeTruthy()
    );
    expect(view.queryByText("UNAVAILABLE")).toBeNull();
  });

  it("reports no problem for a sync state it has never seen", async () => {
    // An unrecognised state is not evidence of a problem. Reporting one would
    // fill this screen with noise the day a provider adds a value.
    const { view } = await renderSync({ items: [product({ syncState: "TELEPORTED" })] });
    await waitFor(() => expect(view.getByText("Nothing needs your attention")).toBeTruthy());
  });

  it("states its remaining gap in the words the gap list holds", async () => {
    // The mutation this test exists for: `body={gap.needs}` replaced by prose
    // written here. The same defect was already fixed on the supplier-orders
    // screen; leaving the second copy unpinned is how a ledger of recurring
    // defects grows.
    const { view } = await renderSync();
    await waitFor(() => expect(view.getByText("Nothing needs your attention")).toBeTruthy());
    DROPSHIPPING_DATA_GAPS.forEach((gap) => {
      expect(view.getByText(gap.needs)).toBeTruthy();
    });
  });

  it("says a broken connection needs attention instead of showing it as working", async () => {
    const broken = connection({ status: "REAUTH_REQUIRED", message: "Key rejected by CJ" });
    // The first draft of this test invented `NEEDS_REAUTH`, which is not a
    // status the app knows, so the screen read the connection as healthy and
    // the test asserted the wrong branch. Asking the shared predicate first
    // means a renamed status fails here instead of quietly moving this test
    // onto the happy path.
    expect(connectionNeedsAttention(broken)).toBe(true);

    const { view } = await renderSync({ connections: [broken] });
    await waitFor(() =>
      expect(view.getByText("Your supplier connection needs attention")).toBeTruthy()
    );
    expect(view.getByText("Key rejected by CJ")).toBeTruthy();
    expect(view.queryByText("Connection is working")).toBeNull();
  });
});

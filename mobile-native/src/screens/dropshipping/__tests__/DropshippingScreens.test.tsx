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
import { FlatList, RefreshControl, StyleSheet, Text } from "react-native";
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
const mockGetStorePolicy = jest.fn();
const mockUpdateStorePolicy = jest.fn();
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
const mockGetSupplierStatus = jest.fn();
const mockRequestResync = jest.fn();

jest.mock("../../../api/dropshipping", () => ({
  ...jest.requireActual("../../../api/dropshipping"),
  resolveDropshippingScope: (...args: unknown[]) => mockResolveScope(...args),
  listSupplierConnections: (...args: unknown[]) => mockListConnections(...args),
  getImportCart: (...args: unknown[]) => mockGetCart(...args),
  importSelected: (...args: unknown[]) => mockImportSelected(...args),
  getStoreImportPolicy: (...args: unknown[]) => mockGetStorePolicy(...args),
  updateStoreImportPolicy: (...args: unknown[]) => mockUpdateStorePolicy(...args),
  getImportedProduct: (...args: unknown[]) => mockGetImportedProduct(...args),
  listImportedProducts: (...args: unknown[]) => mockListImportedProducts(...args),
  previewPricing: (...args: unknown[]) => mockPreviewPricing(...args),
  discoverSupplierShops: (...args: unknown[]) => mockDiscoverShops(...args),
  listConnectionShops: (...args: unknown[]) => mockListConnectionShops(...args),
  bindConnectionShop: (...args: unknown[]) => mockBindConnectionShop(...args),
  connectSupplier: (...args: unknown[]) => mockConnectSupplier(...args),
  searchSupplierProducts: (...args: unknown[]) => mockSearchProducts(...args),
  bindDraftVariant: (...args: unknown[]) => mockBindDraftVariant(...args),
  listSupplierObligations: (...args: unknown[]) => mockListSupplierObligations(...args),
  getSupplierStatus: (...args: unknown[]) => mockGetSupplierStatus(...args),
  requestSupplierResync: (...args: unknown[]) => mockRequestResync(...args)
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
  type ImportItemResult,
  type ImportRunResult,
  type ImportedDraft,
  type StoreImportPolicy,
  type StoreSupplierStatus,
  type SupplierConnection,
  type SupplierObligation,
  type SupplierStatus
} from "../../../api/dropshipping";
import { DropshippingStateView } from "../../../components/dropshipping/DropshippingStates";
import { ConnectSupplierScreen } from "../ConnectSupplierScreen";
import { DropshippingHubScreen } from "../DropshippingHubScreen";
import { DropshippingOrdersScreen } from "../DropshippingOrdersScreen";
import { DropshippingProductsScreen } from "../DropshippingProductsScreen";
import { DropshippingSyncScreen } from "../DropshippingSyncScreen";
import { ImportCartScreen } from "../ImportCartScreen";
import { ImportPolicyScreen } from "../ImportPolicyScreen";
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

/**
 * One supplier as `/supplier-status` describes it.
 *
 * The default is the ordinary healthy sandbox connection, and every field the
 * screen reads is present — including the ones a careless fixture would leave
 * out. `nextAction` defaults to null rather than to something helpful, because a
 * test that wants a particular button must say so: a fixture that quietly
 * supplied one would let a screen rendering the *wrong* action still pass.
 */
function supplierStatus(over: Partial<SupplierStatus> = {}): SupplierStatus {
  return {
    connectionId: "conn-1",
    provider: "cj",
    connectionState: "CONNECTED",
    message: null,
    environment: "SANDBOX",
    realOrderSubmissionEnabled: false,
    fulfillmentShopState: "BOUND",
    externalShopId: "shop-9",
    credentialPresent: true,
    lastVerifiedAt: null,
    lastSyncAt: null,
    lastProductSyncAt: null,
    products: {
      imported: 0,
      published: 0,
      awaitingReview: 0,
      draft: 0,
      blocked: 0,
      archived: 0,
      other: 0
    },
    syncState: null,
    issues: { products: 0, cost: 0, stock: 0 },
    // Null, not a zeroed block: the default fixture stands for a server that
    // answered, and `null` is what it sends when the fulfilment read failed. A
    // test about order counts supplies its own.
    orders: null,
    nextAction: null,
    needsAttention: false,
    ...over
  };
}

/**
 * The store-wide envelope.
 *
 * `environment` and `realOrderSubmissionEnabled` are taken from the first
 * supplier unless overridden, so a test that sets up a production supplier does
 * not accidentally assert a sandbox banner over it.
 */
function storeStatus(
  suppliers: SupplierStatus[] = [supplierStatus()],
  over: Partial<StoreSupplierStatus> = {}
): StoreSupplierStatus {
  return {
    environment: suppliers[0]?.environment ?? "SANDBOX",
    realOrderSubmissionEnabled: suppliers[0]?.realOrderSubmissionEnabled ?? false,
    suppliers,
    needsAttention: suppliers.some((supplier) => supplier.needsAttention),
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

/** A priced row distinguishable from the default by title.
 *
 * The outcome-reporting tests below used `preview: null` to tell their rows
 * apart, which also — incidentally — made those rows unpriced. That is now a
 * blocking state: the footer will not offer "Import & publish" over a row whose
 * supplier cost it could not read. Naming the row instead keeps each of those
 * tests asserting what it was written to assert.
 */
function pricedPreview(title: string): ImportCartItem["preview"] {
  return { ...cartItem().preview!, title };
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
  // The default store: auto-publish on, store-wide only, no saved rule. Which is
  // what the server answers for a store that has never configured one, so the
  // default case in these tests is the default case in production.
  mockGetStorePolicy.mockResolvedValue(storePolicy());
});

/**
 * A store's import policy, defaulting to the platform's own answer.
 *
 * Given its own fixture because the cart screen's copy is now derived from it:
 * "Import & publish" and "Import as drafts" are the same button under two
 * policies, and a test that could not set the policy could only ever check one
 * of them.
 */
function storePolicy(over: Partial<StoreImportPolicy> = {}): StoreImportPolicy {
  return {
    pricingRule: { type: "TARGET_MARGIN", value: 45 },
    pricingSource: "PLATFORM_DEFAULT",
    autoPublish: true,
    marketplaceAutolist: false,
    // `null`, not `0`, and the default matters: the platform has no freight figure
    // to default to, and a fixture that said `0` would make every test here agree
    // that shipping is free — which is the one wrong answer §12 exists to avoid.
    shippingAllowanceCents: null,
    shippingAllowanceSource: "PLATFORM_DEFAULT",
    configured: false,
    ...over
  };
}

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
 * so the shape is pinned directly.
 *
 * The list is read from the directory rather than written out here, and that is
 * not tidiness. It *was* written out here, eight names long, and it had already
 * fallen behind by one: `DropshippingOrdersScreen` renders a state view and was
 * never added, so the one screen most likely to be missing a harness was the one
 * this guard did not cover. A hand-kept enumeration of files in a directory falls
 * behind the directory, exactly like `PUBLISH_PROBLEMS` fell behind the server.
 *
 * A screen that renders no `DropshippingStateView` at all has no state block to
 * gate — `ConnectSupplierScreen` is a form — so the filter is the presence of the
 * component, which is the thing the rule is actually about.
 */
describe("no screen tests a JSX element for truthiness", () => {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const fs = require("fs");
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const path = require("path");
  const dir = path.join(__dirname, "..");
  const SCREENS: string[] = fs
    .readdirSync(dir)
    .filter((name: string) => name.endsWith("Screen.tsx"))
    .filter((name: string) => fs.readFileSync(path.join(dir, name), "utf8").includes("DropshippingStateView"))
    .map((name: string) => name.replace(/\.tsx$/, ""));

  it("found the screens to check, so a rename cannot quietly shrink this suite", () => {
    // Jest 29 throws on `it.each([])`, so a filter that matches *nothing* is caught
    // without this. What it does not catch is a filter that matches *less*: changing
    // the suffix to "sScreen.tsx" leaves three screens, and those three tests go
    // green while seven screens silently stop being checked. That is the failure
    // this count is here for — a partial match is indistinguishable from a pass.
    expect(SCREENS.length).toBeGreaterThanOrEqual(9);
  });

  it.each(SCREENS)("%s gates its state block on the state, not the element", (screen) => {
    const source: string = fs.readFileSync(path.join(dir, `${screen}.tsx`), "utf8");
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
    mockGetSupplierStatus.mockResolvedValue(storeStatus([]));
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
    mockGetSupplierStatus.mockResolvedValue(storeStatus([]));
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
    mockGetSupplierStatus.mockResolvedValue(storeStatus([]));
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
    mockGetSupplierStatus.mockResolvedValue(storeStatus([]));
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
    mockGetSupplierStatus.mockResolvedValue(storeStatus([]));
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
    mockGetSupplierStatus.mockResolvedValue(storeStatus([]));
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
 * 1b(ii) — the hub's tiles, from the one endpoint
 * ------------------------------------------------------------------ */

/**
 * The hub used to hold the connections list and the imported-products list and
 * infer the rest. These tests are about the inference being gone: every figure
 * below is asserted to come from `/supplier-status`, and the two that cannot be
 * read are asserted *not* to appear as zero.
 */
describe("DropshippingHubScreen — tiles", () => {
  async function hubWith(rows: SupplierStatus[], cart: { count: number } | Error = { count: 0 }) {
    mockResolveScope.mockResolvedValue({
      status: "ok",
      scope: { businessId: "biz-1", storeId: "store-1" },
      storeName: "M&W Store",
      source: "MARKETPLACE_SELLER"
    });
    mockGetSupplierStatus.mockResolvedValue(storeStatus(rows));
    if (cart instanceof Error) mockGetCart.mockRejectedValue(cart);
    else mockGetCart.mockResolvedValue(cart);
    const nav = navigation();
    const view = render(<DropshippingHubScreen navigation={nav} route={{ params: {} }} />);
    await settle();
    return { view, nav };
  }

  it("states the operating mode above the tiles, not under them", async () => {
    const { view } = await hubWith([supplierStatus()]);

    await waitFor(() =>
      expect(view.getByTestId("hub-operating-mode").props.accessibilityLabel).toBe(
        "CJ connected. Sandbox mode. Real fulfilment OFF"
      )
    );
    expect(view.getByText(/No order is really placed with your supplier/)).toBeTruthy();
  });

  /**
   * The §8 defect. "Pricing, publishing, Marketplace" rendered as "Pricing,
   * publishing, Mark…" on a half-width tile, so the merchant was shown a word
   * that had been cut in half rather than a sentence.
   */
  it("gives the import-settings tile a subtitle that fits the tile", async () => {
    const { view } = await hubWith([supplierStatus()]);

    await waitFor(() => expect(view.getByText("Your pricing and publishing rules")).toBeTruthy());
    expect(view.queryByText("Pricing, publishing, Marketplace")).toBeNull();
  });

  it("counts products as imported and live, not imported alone", async () => {
    const { view } = await hubWith([
      supplierStatus({ products: { ...supplierStatus().products, imported: 12, published: 3 } })
    ]);

    await waitFor(() => expect(view.getByText("12 imported · 3 live")).toBeTruthy());
  });

  /**
   * Two suppliers where one cannot authenticate is not "2 connected". That
   * count sent a merchant looking elsewhere for the reason half their catalogue
   * had stopped importing.
   */
  it("does not count a broken supplier among the connected ones", async () => {
    const { view } = await hubWith([
      supplierStatus(),
      supplierStatus({ connectionId: "conn-2", connectionState: "AUTH_EXPIRED" })
    ]);

    await waitFor(() => expect(view.getByText("1 of 2 working")).toBeTruthy());
    expect(view.queryByText("2 connected")).toBeNull();
  });

  it("marks the tile the server's next action belongs to", async () => {
    const { view } = await hubWith([
      supplierStatus({
        syncState: "SYNCED",
        products: { ...supplierStatus().products, imported: 4, published: 4, draft: 2 },
        nextAction: "REVIEW_DRAFTS"
      })
    ]);

    await waitFor(() => expect(view.getByText(/saved as drafts/)).toBeTruthy());
    const products = view.getByLabelText(/^Products\./);
    expect(products.props.accessibilityLabel).toContain("Needs attention");
  });

  /**
   * §24. A fulfilment read that failed comes back as `orders: null`, and the
   * tile must describe the screen rather than claim nothing is waiting — the
   * sales it would be claiming about are ones a buyer has already paid for.
   */
  it("never reports an unreadable order count as nothing waiting", async () => {
    const { view } = await hubWith([supplierStatus({ orders: null })]);

    await waitFor(() =>
      expect(view.getByText("Sales waiting on a supplier purchase")).toBeTruthy()
    );
    expect(view.queryByText("Nothing waiting")).toBeNull();
  });

  it("leads with the orders that cannot be placed, not the ones that can", async () => {
    const { view } = await hubWith([
      supplierStatus({
        orders: { awaitingSupplierOrder: 5, readyToPlace: 2, blocked: 3, placed: 1 }
      })
    ]);

    await waitFor(() => expect(view.getByText("3 can't be ordered yet")).toBeTruthy());
    expect(view.queryByText("2 ready to order")).toBeNull();
  });

  /**
   * A cart whose own request failed must not blank the six tiles that do not
   * depend on it, and must not report itself as empty either.
   */
  it("keeps the rest of the hub when only the cart fails to load", async () => {
    const { view } = await hubWith(
      [supplierStatus({ products: { ...supplierStatus().products, imported: 7, published: 7 } })],
      new PulseApiError("nope", 500, "server_error")
    );

    await waitFor(() => expect(view.getByText("7 imported · 7 live")).toBeTruthy());
    expect(view.getByText("Ready when you are")).toBeTruthy();
    expect(view.queryByText("Nothing selected yet")).toBeNull();
  });

  /**
   * The banner is a claim about the deployment, so it cannot outlive the read
   * that produced it. Left behind, it would sit over an error screen describing
   * a state nobody checked.
   */
  it("does not leave a stale operating-mode banner over a failed read", async () => {
    mockResolveScope.mockResolvedValue({
      status: "ok",
      scope: { businessId: "biz-1", storeId: "store-1" },
      storeName: "M&W Store",
      source: "MARKETPLACE_SELLER"
    });
    mockGetSupplierStatus.mockResolvedValueOnce(storeStatus([supplierStatus()]));
    mockGetCart.mockResolvedValue({ count: 0 });
    const view = render(
      <DropshippingHubScreen navigation={navigation()} route={{ params: {} }} />
    );
    await settle();
    await waitFor(() => expect(view.getByTestId("hub-operating-mode")).toBeTruthy());

    mockGetSupplierStatus.mockRejectedValue(new PulseApiError("down", 500, "server_error"));
    fireEvent(view.UNSAFE_getByType(RefreshControl), "refresh");
    await settle();

    await waitFor(() => expect(view.queryByTestId("hub-operating-mode")).toBeNull());
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
  function suppliersScreen() {
    return render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
  }

  it("does not read a failed list as an empty one", async () => {
    mockGetSupplierStatus.mockRejectedValue(new Error("network down"));
    const view = suppliersScreen();
    await settle();

    await waitFor(() => expect(view.getByText(/Suppliers didn't load/)).toBeTruthy());
    expect(view.queryByText("No suppliers connected yet.")).toBeNull();
  });

  it("shows the empty invitation only when the server actually said zero", async () => {
    mockGetSupplierStatus.mockResolvedValue(storeStatus([]));
    const view = suppliersScreen();
    await settle();

    await waitFor(() => expect(view.getByText("No suppliers connected yet.")).toBeTruthy());
    expect(view.queryByText(/didn't load/)).toBeNull();
  });

  it("never renders an unrecognised status as connected", async () => {
    // A provider adding a status this app has not seen must not be optimistically
    // rounded up. The raw word is unhelpful; "Connected and working" is wrong.
    mockGetSupplierStatus.mockResolvedValue(
      storeStatus([supplierStatus({ connectionState: "PENDING_MANUAL_REVIEW" })])
    );
    const view = suppliersScreen();
    await settle();

    await waitFor(() => expect(view.getByText("PENDING_MANUAL_REVIEW")).toBeTruthy());
    expect(view.queryByText("Connected and working")).toBeNull();
  });

  it("offers no catalogue for a connection that cannot serve one", async () => {
    mockGetSupplierStatus.mockResolvedValue(
      storeStatus([supplierStatus({ connectionState: "AUTH_EXPIRED" })])
    );
    const view = suppliersScreen();
    await settle();

    await waitFor(() => expect(view.getByText(/credential expired/i)).toBeTruthy());
    // A "Find products" button on a dead connection is a search that fails with
    // no explanation.
    expect(view.queryByText("Find products")).toBeNull();
  });

  /**
   * The §1 banner, and the reason it is asserted on the *healthy* fixture.
   *
   * Sandbox and "real fulfilment is off" are the two facts a merchant is most
   * likely to miss, precisely because everything else on the row looks fine.
   */
  it("states the operating mode in one line rather than leaving it to be discovered", async () => {
    mockGetSupplierStatus.mockResolvedValue(storeStatus());
    const view = suppliersScreen();
    await settle();

    await waitFor(() =>
      expect(view.getByText("CJ connected · Sandbox mode · Real fulfilment OFF")).toBeTruthy()
    );
    expect(view.getByText(/No order is really placed with your supplier/)).toBeTruthy();
  });

  /**
   * The banner is unconditional. A banner that only appears in sandbox teaches
   * the merchant to read its absence as production — and absence is also what a
   * failed request looks like.
   */
  it("still states the operating mode when every answer is the dull one", async () => {
    mockGetSupplierStatus.mockResolvedValue(
      storeStatus([supplierStatus({ environment: "PRODUCTION", realOrderSubmissionEnabled: true })])
    );
    const view = suppliersScreen();
    await settle();

    await waitFor(() =>
      expect(view.getByText("CJ connected · Production mode · Real fulfilment ON")).toBeTruthy()
    );
    // Neither explanation applies, and printing one anyway would describe a
    // restriction that is not in force.
    expect(view.queryByText(/No order is really placed/)).toBeNull();
    expect(view.queryByText(/switched off platform-wide/)).toBeNull();
  });

  /**
   * Two switches, and they can disagree. A production connection with the
   * platform switch closed is not in sandbox and must not be described as
   * though it were.
   */
  it("explains a closed platform switch in production without calling it sandbox", async () => {
    mockGetSupplierStatus.mockResolvedValue(
      storeStatus([supplierStatus({ environment: "PRODUCTION" })])
    );
    const view = suppliersScreen();
    await settle();

    await waitFor(() =>
      expect(view.getByText("CJ connected · Production mode · Real fulfilment OFF")).toBeTruthy()
    );
    expect(view.getByText(/switched off platform-wide/)).toBeTruthy();
    expect(view.queryByText(/No order is really placed/)).toBeNull();
  });

  /**
   * The §21 claim, stated as an absence. A connection with nothing imported has
   * no sync state, and the row this replaced said "Up to date" about it — a
   * green tick over a catalogue that did not exist.
   */
  it("does not call an empty catalogue synced", async () => {
    mockGetSupplierStatus.mockResolvedValue(storeStatus());
    const view = suppliersScreen();
    await settle();

    await waitFor(() => expect(view.getByText("Nothing imported yet")).toBeTruthy());
    expect(view.queryByText("Up to date")).toBeNull();
  });

  /** The server decides what is next; the screen only renders it. */
  it("promotes the server's next action as the one button that answers what to do", async () => {
    mockGetSupplierStatus.mockResolvedValue(
      storeStatus([
        supplierStatus({
          fulfillmentShopState: "NOT_SELECTED",
          externalShopId: null,
          nextAction: "CHOOSE_FULFILLMENT_SHOP",
          needsAttention: true
        })
      ])
    );
    const view = suppliersScreen();
    await settle();

    await waitFor(() =>
      expect(view.getByTestId("supplier-primary-action-conn-1")).toBeTruthy()
    );
    expect(view.getByText(/Orders can't be sent until you pick the shop/)).toBeTruthy();
    expect(view.getByText("Needs attention")).toBeTruthy();
  });

  /**
   * A row with nothing wrong still reads as a row with nothing wrong. Drawing
   * "you have drafts" with the same weight as a revoked credential is how a
   * permanent badge stops being read.
   */
  it("does not badge a working supplier as needing attention", async () => {
    mockGetSupplierStatus.mockResolvedValue(
      storeStatus([supplierStatus({ nextAction: "IMPORT_FIRST_PRODUCT" })])
    );
    const view = suppliersScreen();
    await settle();

    // Read off the badge itself rather than off the word: "Working" is also the
    // Connection health row's value, and a text query that matched either would
    // pass with the badge missing entirely.
    await waitFor(() =>
      expect(view.getByTestId("supplier-attention-conn-1").props.accessibilityLabel).toBe("Working")
    );
    expect(view.queryByText("Needs attention")).toBeNull();
  });

  it("sends an unauthenticated merchant to sign in, not to retry", async () => {
    mockGetSupplierStatus.mockRejectedValue(new PulseApiError("nope", 401));
    const view = suppliersScreen();
    await settle();

    await waitFor(() => expect(view.getByText(/not signed in to this store/i)).toBeTruthy());
  });

  /**
   * A failed read takes the banner down with it.
   *
   * Leaving "Real fulfilment OFF" on screen beside an error states a
   * platform-level fact this render has no evidence for — and it is the one
   * fact a merchant would act on.
   */
  it("does not leave a stale operating-mode banner over a failed read", async () => {
    mockGetSupplierStatus.mockResolvedValueOnce(storeStatus());
    const view = suppliersScreen();
    await settle();
    await waitFor(() => expect(view.getByTestId("supplier-operating-mode")).toBeTruthy());

    mockGetSupplierStatus.mockRejectedValue(new Error("network down"));
    await act(async () => {
      fireEvent.press(view.getByLabelText(/Check this supplier connection/));
    });
    await settle();

    await waitFor(() => expect(view.getByText(/Suppliers didn't load/)).toBeTruthy());
    expect(view.queryByTestId("supplier-operating-mode")).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * 2a — Suppliers: the actions have to be on the screen
 * ------------------------------------------------------------------ */

/**
 * §2. The defect these assert against was reported as "unacceptable", and it
 * was: three action pills were laid out in one unwrapped row wider than the
 * card, and React Native neither scrolls nor shrinks an overflowing row — it
 * draws the remainder outside the parent's bounds, where it is invisible *and*
 * untappable. "Check connection" existed, rendered, passed every test that
 * queried it by text, and could not be pressed by a human being.
 *
 * Which is why these read style objects rather than text. A test that finds a
 * button by its label proves the button is in the tree; it says nothing about
 * whether the button is on the screen, and the tree is exactly what was never
 * in doubt. The same lesson is already written down twice in this codebase —
 * the Business Hub revert and `StoreQuickLinkTile` — so the rule here is not
 * prose about geometry, it is arithmetic over the geometry that shipped.
 */
describe("SuppliersScreen — no action lands off the card", () => {
  /**
   * The three-pill row, which is the case that overflowed.
   *
   * `REVIEW_DRAFTS` is the next action that demotes neither "Find products" nor
   * "Sync now", so all three secondaries render together. A fixture with fewer
   * would fit in one line and measure nothing.
   */
  async function threePillRow() {
    mockGetSupplierStatus.mockResolvedValue(
      storeStatus([supplierStatus({ nextAction: "REVIEW_DRAFTS" })])
    );
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();
    await waitFor(() => expect(view.getByTestId("supplier-secondary-actions-conn-1")).toBeTruthy());
    return view;
  }

  it("wraps the action row instead of drawing the third button past the card edge", async () => {
    const view = await threePillRow();
    const row = view.getByTestId("supplier-secondary-actions-conn-1");
    const layout = StyleSheet.flatten(row.props.style);

    expect(row.props.children).toHaveLength(3);
    expect(layout.flexDirection).toBe("row");
    // The whole fix. Without it the third pill is outside the parent's bounds.
    expect(layout.flexWrap).toBe("wrap");
  });

  it("gives no pill enough width for three to share a line", async () => {
    const view = await threePillRow();
    const pills = view
      .getByTestId("supplier-secondary-actions-conn-1")
      .props.children.map((pill: { props: { style: unknown } }) =>
        StyleSheet.flatten(pill.props.style)
      );

    expect(pills).toHaveLength(3);
    for (const pill of pills) {
      // `flexWrap` alone does not decide *when* to wrap — a pill that can
      // shrink below a third of the row would let all three stay on one line
      // and clip their labels instead, which is the same defect wearing the
      // fix's clothes. A basis over a third forces the third one down.
      expect(Number.parseFloat(String(pill.flexBasis))).toBeGreaterThan(33.4);
      expect(Number.parseFloat(String(pill.flexBasis))).toBeLessThanOrEqual(50);
      // Tappable once it is on screen: the row above was only half the report.
      expect(pill.minHeight).toBeGreaterThanOrEqual(44);
    }
  });

  it("lets every action label wrap rather than clipping it to an abbreviation", async () => {
    const view = await threePillRow();
    // Both kinds of button, because they are styled by different rules and the
    // primary carries the longest label in the file ("Choose fulfilment shop").
    const labels = [
      view.getByText("Review drafts"),
      view.getByText("Find products"),
      view.getByText("Sync now"),
      view.getByText("Check connection")
    ];

    for (const label of labels) {
      expect(label.props.numberOfLines).toBe(2);
      // Capped, not uncapped: at the largest accessibility sizes an uncapped
      // label pushes its own pill past the card edge, which is the defect
      // again. Capped at 1 would ignore the OS setting, which is its own
      // accessibility failure — so this asserts a ceiling in between.
      expect(label.props.maxFontSizeMultiplier).toBeGreaterThan(1);
      expect(label.props.maxFontSizeMultiplier).toBeLessThanOrEqual(1.5);
    }
  });

  it("puts the one thing to do next above the three things that can wait", async () => {
    const view = await threePillRow();
    const primary = view.getByTestId("supplier-primary-action-conn-1");

    // Full width and on its own line: a primary that is the same size as its
    // neighbours in the same row cannot mean "this one first", which is what
    // §2 asked the layout to say.
    expect(StyleSheet.flatten(primary.props.style).alignSelf).toBe("stretch");
    const order = view
      .getByTestId("supplier-secondary-actions-conn-1")
      .props.children.map((pill: { props: { accessibilityLabel: string } }) =>
        pill.props.accessibilityLabel
      );
    expect(order[0]).toMatch(/Find products/);
    expect(primary.props.accessibilityLabel).toMatch(/Review drafts/);
  });
});

/* ------------------------------------------------------------------ *
 * 2b — Suppliers: asking for a sync
 * ------------------------------------------------------------------ */

/**
 * "Sync now" had to become true before it could be drawn.
 *
 * The button reports what was *queued*, never what was synced: the request
 * returns the moment the jobs are enqueued and a background worker drains them.
 * A merchant told "synced" would read the same stale costs straight back off the
 * screen and conclude the button does nothing — which is worse than the screen
 * that had no button at all, because now they have stopped watching.
 */
describe("SuppliersScreen — asking for a sync", () => {
  async function screenWith(over: Partial<SupplierStatus> = {}) {
    mockGetSupplierStatus.mockResolvedValue(storeStatus([supplierStatus(over)]));
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();
    return view;
  }

  async function pressSync(view: ReturnType<typeof render>) {
    await waitFor(() => expect(view.getByLabelText(/Refresh this supplier's products/)).toBeTruthy());
    await act(async () => {
      fireEvent.press(view.getByLabelText(/Refresh this supplier's products/));
    });
    await settle();
  }

  it("asks the server to re-read this connection", async () => {
    mockRequestResync.mockResolvedValue({
      queuedProducts: 4,
      queuedJobs: 11,
      truncated: false,
      maxProducts: 200
    });
    const view = await screenWith({ products: { ...supplierStatus().products, imported: 4 } });
    await pressSync(view);

    expect(mockRequestResync).toHaveBeenCalledWith(expect.anything(), "conn-1");
    await waitFor(() => expect(view.getByText(/Refreshing your connection and 4 products/)).toBeTruthy());
  });

  it("says a refresh started, never that anything synced", async () => {
    mockRequestResync.mockResolvedValue({
      queuedProducts: 0,
      queuedJobs: 3,
      truncated: false,
      maxProducts: 200
    });
    const view = await screenWith();
    await pressSync(view);

    await waitFor(() => expect(view.getByText(/Checking your connection/)).toBeTruthy());
    expect(view.queryByText(/Synced/)).toBeNull();
    expect(view.queryByText("Up to date")).toBeNull();
  });

  /**
   * The cap is said out loud. A merchant whose catalogue is larger than one
   * request may enqueue, told simply "syncing", goes looking for a failure that
   * is really a bound.
   */
  it("says so when only part of the catalogue was queued", async () => {
    mockRequestResync.mockResolvedValue({
      queuedProducts: 200,
      queuedJobs: 403,
      truncated: true,
      maxProducts: 200
    });
    const view = await screenWith({ products: { ...supplierStatus().products, imported: 900 } });
    await pressSync(view);

    await waitFor(() => expect(view.getByText(/first 200 products/)).toBeTruthy());
    expect(view.getByText(/Sync again when it finishes/)).toBeTruthy();
  });

  /**
   * Named as a failure to *start*, which is what happened. "Sync failed" would
   * describe a sync that never ran, and sends the merchant to their supplier's
   * status page instead of to the button they just pressed.
   */
  it("reports a refusal as a refusal to start", async () => {
    mockRequestResync.mockRejectedValue(new PulseApiError("x", 503, "provider_unavailable"));
    const view = await screenWith();
    await pressSync(view);

    await waitFor(() => expect(view.getByText(/Couldn't start a refresh just now/)).toBeTruthy());
  });

  it("sends an unauthenticated merchant to sign in rather than blaming the supplier", async () => {
    mockRequestResync.mockRejectedValue(new PulseApiError("x", 401));
    const view = await screenWith();
    await pressSync(view);

    await waitFor(() => expect(view.getByText(/Sign in again to refresh this supplier/)).toBeTruthy());
  });

  it("re-reads the status afterwards so the figures can move", async () => {
    mockRequestResync.mockResolvedValue({
      queuedProducts: 0,
      queuedJobs: 3,
      truncated: false,
      maxProducts: 200
    });
    const view = await screenWith();
    await pressSync(view);

    expect(mockGetSupplierStatus).toHaveBeenCalledTimes(2);
  });

  /**
   * A disconnected supplier is offered no sync, for the same reason it is
   * offered no catalogue: the request cannot succeed, and an offer that cannot
   * succeed is worse than no offer.
   */
  it("offers no sync on a connection that cannot be read", async () => {
    const view = await screenWith({ connectionState: "AUTH_EXPIRED" });
    await waitFor(() => expect(view.getByText(/credential expired/i)).toBeTruthy());
    expect(view.queryByLabelText(/Refresh this supplier's products/)).toBeNull();
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
  const UNBOUND = supplierStatus({
    fulfillmentShopState: "NOT_SELECTED",
    externalShopId: null,
    nextAction: "CHOOSE_FULFILLMENT_SHOP",
    needsAttention: true
  });

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
    mockGetSupplierStatus.mockResolvedValue(storeStatus(rows));
    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();
    return view;
  }

  async function openPicker(rows = [UNBOUND]) {
    const view = await suppliers(rows);
    await waitFor(() => expect(view.getByLabelText(/Choose fulfilment shop for this supplier/)).toBeTruthy());
    await act(async () => {
      fireEvent.press(view.getByLabelText(/Choose fulfilment shop for this supplier/));
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
    await waitFor(() => expect(view.getByText(/Orders can't be sent until you pick the shop/)).toBeTruthy());
    expect(view.queryByText("Connected and working")).toBeNull();
    // And importing is untouched, which is the whole reason a shopless
    // connection is allowed in the first place.
    expect(view.getByText("Find products")).toBeTruthy();

    await act(async () => {
      fireEvent.press(view.getByLabelText(/Choose fulfilment shop for this supplier/));
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
    await waitFor(() => expect(mockGetSupplierStatus).toHaveBeenCalledTimes(2));
  });

  it("offers no shop picker to a connection that already has one", async () => {
    // `supplierStatus()` is bound by default, as most fixtures here are.
    const view = await suppliers([supplierStatus()]);
    await waitFor(() => expect(view.getByText("Connected and working")).toBeTruthy());
    expect(view.queryByLabelText(/Choose fulfilment shop for this supplier/)).toBeNull();
  });

  it("offers no shop picker to a connection that could not read a list anyway", async () => {
    // An expired credential cannot fetch shops, so a picker on it would open
    // straight onto a reauth error the merchant did not ask for.
    const view = await suppliers([
      supplierStatus({
        connectionState: "AUTH_EXPIRED",
        fulfillmentShopState: "NOT_SELECTED",
        externalShopId: null,
        nextAction: "RECONNECT_SUPPLIER",
        needsAttention: true
      })
    ]);
    // The server's own next action is reconnecting, so that — not the shop — is
    // what the row asks for, and the picker it would otherwise offer is gone.
    await waitFor(() => expect(view.getByText(/can't be reached with the credential/i)).toBeTruthy());
    expect(view.queryByLabelText(/Choose fulfilment shop for this supplier/)).toBeNull();
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
    expect(view.getByText(/Create one there, then check again below/)).toBeTruthy();
    expect(view.queryByText(/didn't load/)).toBeNull();
    // And the control that sentence points at is really there. The copy used to
    // ask the merchant to close and reopen the picker, which is the same read
    // with more steps and nothing on screen to tell them it had happened.
    expect(view.getByLabelText("Check for shops again")).toBeTruthy();
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
    expect(view.getByText(/Orders can't be sent until you pick the shop/)).toBeTruthy();
    expect(mockGetSupplierStatus).toHaveBeenCalledTimes(1);
  });

  it("reads the shop list for the connection whose picker was opened", async () => {
    mockListConnectionShops.mockResolvedValue({ shops: [shop()], boundShopId: null });
    await openPicker([{ ...UNBOUND, connectionId: "conn-7" }]);
    expect(mockListConnectionShops).toHaveBeenCalledWith(expect.anything(), "conn-7");
  });

  /**
   * The failure this whole investigation was about, seen from the screen.
   *
   * For two weeks this merchant's picker said "This supplier account has no
   * shops" over an account that owned one. The server was rejecting CJ's
   * `code: 0` success envelope, and `connection_shops` was translating that
   * rejection into an empty list — so a parser fault was printed as a fact
   * about the merchant's supplier account, in a sentence they had no way to
   * doubt and no action that could fix.
   *
   * The server now sends `shop_list_unavailable` (502) instead. This asserts
   * the sentence that replaced it: a problem on our side of the wire, with a
   * retry, and no claim about what the account owns.
   */
  it("does not tell a merchant their account is empty when the list failed to load", async () => {
    mockListConnectionShops.mockRejectedValue(new PulseApiError("x", 502, "shop_list_unavailable"));
    const view = await openPicker();

    await waitFor(() => expect(view.getByText(/supplier isn't responding/i)).toBeTruthy());
    expect(view.queryByText("This supplier account has no shops.")).toBeNull();
    // Retryable, because it is: nothing about the account has to change first.
    expect(view.getByLabelText(/^Retry\./)).toBeTruthy();
  });

  /**
   * A credential that expires between the list loading and the row rendering.
   *
   * The row-level guard two tests up stops a picker opening on a connection
   * already known to be expired. It cannot stop a credential expiring while the
   * picker is open, and that failure must not land in the empty state either —
   * "you own no shops" is the one answer a merchant cannot act on when the real
   * problem is a signed-out supplier.
   */
  it("reads an expired supplier credential as a credential problem, not an empty account", async () => {
    mockListConnectionShops.mockRejectedValue(new PulseApiError("x", 401, "reauth_required"));
    const view = await openPicker();

    await waitFor(() => expect(view.queryByText("This supplier account has no shops.")).toBeNull());
    expect(view.queryByText(/Create one there, then reopen this list/)).toBeNull();
  });

  /**
   * A shop switched off in the supplier's console, which is not the same
   * refusal as a shop of the wrong kind even though dispatch answers both with
   * one code.
   *
   * The server separates them for display only. It matters because the two
   * point in opposite directions: this merchant can turn their shop back on,
   * while a Shopify storefront will never take an API order. Reading the
   * wrong-kind sentence over a switched-off shop sends them looking for a
   * problem with the shop's type that does not exist.
   */
  it("says a switched-off shop is switched off, not the wrong kind of shop", async () => {
    mockListConnectionShops.mockResolvedValue({
      shops: [
        shop({ externalShopId: "shop-c", name: "Paused shop", status: 0,
               fulfillable: false, unfulfillableReason: "shop_disabled" }),
        shop()
      ],
      boundShopId: null
    });
    const view = await openPicker();

    await waitFor(() => expect(view.getByText("Paused shop")).toBeTruthy());
    expect(view.getByText(/Switched off in your supplier's console/)).toBeTruthy();
    expect(view.queryByText(/won't take orders for this shop from an outside app/)).toBeNull();
    expect(view.queryByLabelText(/Send orders to Paused shop/)).toBeNull();
  });

  /**
   * More than one usable shop, which is the case a picker exists for at all.
   *
   * With one shop the screen could bind it automatically and nobody would
   * notice the difference; with two, the choice is real and the wrong one is a
   * misrouted order. So the tapped shop — not the first, not the last — is the
   * one that must reach the server.
   */
  it("sends orders to the shop that was tapped when several could take them", async () => {
    mockListConnectionShops.mockResolvedValue({
      shops: [shop(), shop({ externalShopId: "shop-b", name: "Second shop" }),
              shop({ externalShopId: "shop-c", name: "Third shop" })],
      boundShopId: null
    });
    mockBindConnectionShop.mockResolvedValue(undefined);
    const view = await openPicker();
    await waitFor(() => expect(view.getByText("Third shop")).toBeTruthy());
    expect(view.getByLabelText(/Send orders to Second shop/)).toBeTruthy();

    await act(async () => {
      fireEvent.press(view.getByLabelText(/Send orders to Second shop/));
    });
    await settle();

    expect(mockBindConnectionShop).toHaveBeenCalledWith(expect.anything(), "conn-1", "shop-b");
    expect(mockBindConnectionShop).toHaveBeenCalledTimes(1);
  });

  /**
   * A shop created in the supplier's console while this screen was open.
   *
   * There is no client-side shop cache, and this is the test that keeps it that
   * way: every read is live, so a merchant who goes to CJ, makes a shop and
   * comes back sees it without signing out or reinstalling. A cache added here
   * later — even a well-meant one keyed on connection id — fails this.
   *
   * The empty state carries its own control for exactly this, which no other
   * empty state in the app does. Without it the only way to re-read is to close
   * the picker and reopen it, and the merchant has to be told so in prose.
   */
  it("re-reads the list on request rather than showing what it saw before", async () => {
    mockListConnectionShops.mockResolvedValue({ shops: [], boundShopId: null });
    const view = await openPicker();
    await waitFor(() => expect(view.getByText("This supplier account has no shops.")).toBeTruthy());

    // The shop is created in the supplier's console, outside this app.
    mockListConnectionShops.mockResolvedValue({
      shops: [shop({ name: "Just created" })], boundShopId: null });
    await act(async () => {
      fireEvent.press(view.getByLabelText("Check for shops again"));
    });
    await settle();

    await waitFor(() => expect(view.getByText("Just created")).toBeTruthy());
    expect(view.queryByText("This supplier account has no shops.")).toBeNull();
    expect(view.getByLabelText(/Send orders to Just created/)).toBeTruthy();
  });

  /**
   * What the merchant sees on the row itself once the choice is made, and after
   * closing the app.
   *
   * Both halves are the same claim: the binding lives on the server and the row
   * is drawn from it. So the card changes without a manual refresh because it
   * re-reads, and it survives a relaunch because there was never any local
   * state to survive. Asserting only the first would leave "it looked right
   * until you closed the app" untested, which is the shape this bug had.
   */
  it("shows the bound shop on the card straight away and again after a relaunch", async () => {
    const BOUND = supplierStatus({ fulfillmentShopState: "BOUND", externalShopId: "shop-a",
                                   needsAttention: false });
    mockListConnectionShops.mockResolvedValue({ shops: [shop()], boundShopId: null });
    mockBindConnectionShop.mockResolvedValue(undefined);
    mockGetSupplierStatus.mockResolvedValueOnce(storeStatus([UNBOUND]));
    mockGetSupplierStatus.mockResolvedValue(storeStatus([BOUND]));

    const view = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();
    await act(async () => {
      fireEvent.press(view.getByLabelText(/Choose fulfilment shop for this supplier/));
    });
    await settle();
    await act(async () => {
      fireEvent.press(view.getByLabelText(/Send orders to Main shop/));
    });
    await settle();

    await waitFor(() => expect(view.getByText("Connected and working")).toBeTruthy());
    expect(view.queryByText(/Orders can't be sent until you pick the shop/)).toBeNull();
    view.unmount();

    // A cold start reads the same server state and draws the same row.
    const relaunched = render(<SuppliersScreen navigation={navigation()} route={{ params: {} }} />);
    await settle();
    await waitFor(() => expect(relaunched.getByText("Connected and working")).toBeTruthy());
    expect(relaunched.queryByLabelText(/Choose fulfilment shop for this supplier/)).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * 3 — Import cart
 * ------------------------------------------------------------------ */

describe("ImportCartScreen", () => {
  const route = { params: { connectionId: "conn-1" } };

  /** One row of an import run, defaulting to the ordinary outcome: live. */
  function itemResult(over: Partial<ImportItemResult> = {}): ImportItemResult {
    return {
      itemId: "item-1",
      externalProductId: "ext-1",
      provider: "cj",
      outcome: "PUBLISHED",
      listingId: 5,
      detail: null,
      variantCount: 3,
      published: true,
      problems: [],
      priceLabel: "$18.00",
      quantity: 12,
      ...over
    };
  }

  /**
   * A whole run, with its counts derived from the rows rather than passed in.
   *
   * Derived on purpose: a fixture that let a test state "3 published" alongside
   * one published row could make the summary line say anything, and the summary
   * line is the §30 claim under test.
   */
  function runResult(results: ImportItemResult[], over: Partial<ImportRunResult> = {}): ImportRunResult {
    const publishedCount = results.filter((item) => item.published).length;
    return {
      results,
      requested: results.length,
      imported: results.filter((item) => item.listingId !== null).length,
      counts: {},
      published: publishedCount === results.length,
      publishedCount,
      needsAttention: results.filter((item) => item.outcome === "NEEDS_ATTENTION").length,
      pricingRule: { type: "TARGET_MARGIN", value: 45 },
      pricingSource: "PLATFORM_DEFAULT",
      autoPublish: true,
      marketplaceAutolist: false,
      ...over
    };
  }

  /**
   * `"unavailable"` rather than a mock set by the caller beforehand: the screen
   * reads the policy during this function's own `render`, so a rejection set
   * outside it is overwritten by the line below before it is ever used. That is
   * how the failed-read test first passed while proving nothing.
   */
  async function renderCart(
    items: ImportCartItem[],
    staleCount = 0,
    policy: StoreImportPolicy | "unavailable" = storePolicy()
  ) {
    mockGetCart.mockResolvedValue({
      items,
      count: items.length,
      staleCount,
      maxItems: 200
    });
    if (policy === "unavailable") {
      mockGetStorePolicy.mockRejectedValue(new PulseApiError("down", 503, "provider_unavailable"));
    } else {
      mockGetStorePolicy.mockResolvedValue(policy);
    }
    const nav = navigation();
    const view = render(<ImportCartScreen navigation={nav} route={route} />);
    await settle();
    await waitFor(() => expect(mockGetCart).toHaveBeenCalled());
    return { view, nav };
  }

  it("calls it import and publish, because that is what it does now", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    expect(view.getByText("Import & publish 1")).toBeTruthy();
    expect(view.getByText(/go live in your store, priced and ready to sell/)).toBeTruthy();
    // The old copy is the specific lie this screen must not tell: a merchant told
    // their products are drafts will not go looking at a live storefront.
    expect(view.queryByText(/Nothing appears in your store until you publish/)).toBeNull();
    expect(view.queryByText(/as drafts/)).toBeNull();
  });

  it("says drafts when the store's policy really is drafts", async () => {
    // Both labels are reachable and neither is hardcoded. A merchant who turned
    // auto-publish off is getting drafts, and §44's rule is that the app does not
    // force publishing onto someone who said no.
    const { view } = await renderCart([cartItem()], 0, storePolicy({ autoPublish: false }));
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    expect(view.getByText("Import 1 as drafts")).toBeTruthy();
    expect(
      view.getByText("Imported products are drafts. Nothing appears in your store until you publish it.")
    ).toBeTruthy();
    expect(view.queryByText(/Import & publish/)).toBeNull();
  });

  it("does not send a pricing rule the merchant never chose", async () => {
    // The §8 inversion, at the only place it can be observed. The picker is on
    // screen showing 45%, and that number is the *store's* answer being displayed
    // back — sending it would record a per-request override on every import and
    // permanently outrank the policy the merchant saves later.
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue(runResult([itemResult()]));

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 1 products to your store"));
    });
    await settle();

    const [, connectionId, input] = mockImportSelected.mock.calls[0];
    expect(connectionId).toBe("conn-1");
    expect(input.itemIds).toEqual(["item-1"]);
    // The rule is a policy the server applies to a cost it fetched itself. Any
    // other key here would be the client asserting supplier economics.
    expect(Object.keys(input).sort()).toEqual(["itemIds", "pricingRule"]);
    expect(input.pricingRule).toBeNull();
  });

  it("reports every per-item outcome instead of one verdict", async () => {
    const { view } = await renderCart([
      cartItem({ itemId: "item-1", externalProductId: "ext-1" }),
      cartItem({ itemId: "item-2", externalProductId: "ext-2", preview: pricedPreview("Linen Shirt") }),
      cartItem({ itemId: "item-3", externalProductId: "ext-3", preview: pricedPreview("Canvas Tote") })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue(
      runResult([
        itemResult(),
        itemResult({
          itemId: "item-2",
          externalProductId: "ext-2",
          outcome: "NEEDS_ATTENTION",
          listingId: 6,
          published: false,
          problems: ["SUPPLIER_VARIANT_UNBOUND"],
          priceLabel: null
        }),
        itemResult({
          itemId: "item-3",
          externalProductId: "ext-3",
          outcome: "PROVIDER_UNAVAILABLE",
          listingId: null,
          published: false,
          variantCount: null,
          priceLabel: null
        })
      ])
    );

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 3 products to your store"));
    });
    await settle();

    // Three different things happened and the summary says all three. Rounding to
    // "1 of 3 imported" loses the row that needs the merchant, and rounding the
    // other way reports a live product as a failure.
    await waitFor(() =>
      expect(view.getByText("1 published, 1 need attention, 1 couldn't be imported.")).toBeTruthy()
    );
    expect(view.getByText(/Published — live and ready to sell/)).toBeTruthy();
    expect(view.getByText(/needs one fix before it can go live/)).toBeTruthy();
    expect(view.getByText(/didn't respond — try this one again/)).toBeTruthy();
  });

  it("says so plainly when the whole run went live", async () => {
    const { view } = await renderCart([
      cartItem({ itemId: "item-1", externalProductId: "ext-1" }),
      cartItem({ itemId: "item-2", externalProductId: "ext-2", preview: pricedPreview("Linen Shirt") })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue(
      runResult([itemResult(), itemResult({ itemId: "item-2", externalProductId: "ext-2", listingId: 6 })])
    );

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 2 products to your store"));
    });
    await settle();

    await waitFor(() =>
      expect(view.getByText("All 2 are published and ready to sell.")).toBeTruthy()
    );
  });

  it("gives a product that needs a fix somewhere to go", async () => {
    // §27. A reason with no remedy is where this screen used to leave the
    // merchant: the sheet named an outcome and nothing on it was pressable.
    const { view, nav } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue(
      runResult([
        itemResult({
          outcome: "NEEDS_ATTENTION",
          listingId: 42,
          published: false,
          problems: ["SUPPLIER_VARIANT_UNBOUND"],
          priceLabel: null
        })
      ])
    );

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 1 products to your store"));
    });
    await settle();

    // The reason is in words on the row, not a code.
    await waitFor(() => expect(view.getByText(/Choose which variant you're selling/)).toBeTruthy());
    expect(view.queryByText("SUPPLIER_VARIANT_UNBOUND")).toBeNull();

    await act(async () => {
      fireEvent.press(view.getByLabelText("Fix this product so it can go live"));
    });
    // The listing id, not the cart item id. The draft editor is the only place
    // this problem can be solved, and it needs the listing.
    expect(nav.navigate).toHaveBeenCalledWith(
      "DropshippingDraft",
      expect.objectContaining({ connectionId: "conn-1", listingId: 42 })
    );
  });

  it("offers no fix for a problem that is not the merchant's to fix", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue(
      runResult([
        itemResult({
          outcome: "NEEDS_ATTENTION",
          listingId: 42,
          published: false,
          problems: ["PROVIDER_PRODUCT_UNAVAILABLE"],
          priceLabel: null
        })
      ])
    );

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 1 products to your store"));
    });
    await settle();

    await waitFor(() => expect(view.getByText(/no longer offers this product/)).toBeTruthy());
    // A button opening an editor where nothing can be changed is worse than none:
    // it says the supplier's decision is the merchant's mistake.
    expect(view.queryByLabelText("Fix this product so it can go live")).toBeNull();
  });

  it("only points at the store for a product that is actually in it", async () => {
    const { view, nav } = await renderCart([
      cartItem({ itemId: "item-1", externalProductId: "ext-1" }),
      cartItem({ itemId: "item-2", externalProductId: "ext-2", preview: pricedPreview("Linen Shirt") })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue(
      runResult([
        itemResult({ listingId: 7 }),
        // Says PUBLISHED but the server did not set `published`. Liveness comes
        // from the field, so this row gets no store link.
        itemResult({
          itemId: "item-2",
          externalProductId: "ext-2",
          listingId: 8,
          published: false,
          priceLabel: null
        })
      ])
    );

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 2 products to your store"));
    });
    await settle();

    await waitFor(() =>
      expect(view.getAllByLabelText("View this product in your store")).toHaveLength(1)
    );
    await act(async () => {
      fireEvent.press(view.getAllByLabelText("View this product in your store")[0]);
    });
    expect(nav.navigate).toHaveBeenCalledWith(
      "SellerStore",
      expect.objectContaining({ mode: "product", listingId: 7 })
    );
  });

  it("shows the price a buyer will pay on a row that went live", async () => {
    // The first question an auto-priced import raises. The label is the server's
    // formatting — this screen does no arithmetic on money.
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue(runResult([itemResult({ priceLabel: "$18.00" })]));

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 1 products to your store"));
    });
    await settle();

    await waitFor(() => expect(view.getByText(/\$18\.00/)).toBeTruthy());
  });

  it("says whether an import goes beyond this store", async () => {
    // §19/§20. One tap publishing to the merchant's own store is the feature;
    // one tap broadcasting across PulseSoc is a distribution decision, and the
    // merchant is told which one they are about to make.
    const store = await renderCart([cartItem()]);
    await waitFor(() => expect(store.view.getByText("Ceramic Mug")).toBeTruthy());
    expect(store.view.getByText(/They stay in your store/)).toBeTruthy();

    const wide = await renderCart([cartItem()], 0, storePolicy({ marketplaceAutolist: true }));
    await waitFor(() => expect(wide.view.getByText("Ceramic Mug")).toBeTruthy());
    expect(wide.view.getByText(/also offered across the PulseSoc Marketplace/)).toBeTruthy();
  });

  it("does not report an unknown outcome as a success", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockResolvedValue(
      runResult([
        itemResult({
          outcome: "SOME_NEW_CODE",
          listingId: null,
          published: false,
          variantCount: null,
          priceLabel: null
        })
      ])
    );

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 1 products to your store"));
    });
    await settle();

    await waitFor(() => expect(view.getByText("This one couldn't be imported")).toBeTruthy());
    expect(view.queryByText(/Published/)).toBeNull();
    expect(view.queryByText("Imported as a draft")).toBeNull();
  });

  it("keeps its pricing claim honest when it could not read the policy", async () => {
    // A failed policy read must not cost the merchant the ability to import, and
    // must not present the platform default as though it were their store's
    // setting. The server resolves the real policy either way.
    const { view } = await renderCart([cartItem()], 0, "unavailable");
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    expect(view.getByText(/couldn't read your store's pricing rule/)).toBeTruthy();
    expect(view.getByLabelText("Import and publish 1 products to your store").props.accessibilityState.disabled).toBe(false);
  });

  it("says nothing was imported when the run itself failed", async () => {
    const { view } = await renderCart([cartItem()]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    mockImportSelected.mockRejectedValue(
      new PulseApiError("down", 503, "provider_unavailable")
    );

    await act(async () => {
      fireEvent.press(view.getByLabelText("Import and publish 1 products to your store"));
    });
    await settle();

    await waitFor(() =>
      expect(
        view.getByText("Your supplier didn't respond. Nothing was imported — your cart is unchanged.")
      ).toBeTruthy()
    );
    // No result sheet, because there were no results. Asserted on the sheet's own
    // button and its outcome rows — a text regex like /ready to sell/ matches the
    // footer note that is on screen before any import runs, so it would have
    // passed whether the sheet was drawn or not.
    expect(view.queryByLabelText("Open your imported products")).toBeNull();
    expect(view.queryByText(/Published — live and ready to sell/)).toBeNull();
    expect(view.queryByText("This one couldn't be imported")).toBeNull();
  });

  it("says a cost is unknown rather than printing a zero", async () => {
    const { view } = await renderCart([
      cartItem({ preview: { ...cartItem().preview!, costLowCents: null, costHighCents: null } })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    expect(view.getByText("— cost unknown")).toBeTruthy();
    expect(view.queryByText(/0\.00 cost/)).toBeNull();
  });

  it("will not offer to publish a row it cannot price", async () => {
    const { view } = await renderCart([
      cartItem({ preview: { ...cartItem().preview!, costLowCents: null, costHighCents: null } })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    // The defect in one assertion: "Import & publish 1" over a row with no cost
    // promises a live, priced product. The server then refuses it on
    // MISSING_PRICE and leaves a draft, so the button was never telling the
    // truth about what the tap would do.
    expect(view.queryByText(/Import & publish/)).toBeNull();
    expect(view.getByText("Resolve pricing issues")).toBeTruthy();
    expect(
      view.getByLabelText("Resolve pricing issues on 1 products before importing").props
        .accessibilityState.disabled
    ).toBe(true);
  });

  it("names the unpriced product and says how to get past it", async () => {
    const { view } = await renderCart([
      cartItem({ itemId: "item-1", externalProductId: "ext-1" }),
      cartItem({
        itemId: "item-2",
        externalProductId: "ext-2",
        preview: { ...pricedPreview("Linen Shirt")!, costLowCents: null, costHighCents: null }
      })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());

    // Named, not counted. "1 product has no cost" leaves the merchant hunting a
    // list for which one.
    // Matched as one string so the title has to be *inside the note*, not merely
    // somewhere on screen — it is also the row's own label, which would pass a
    // looser assertion while the note said nothing useful.
    expect(
      view.getByText(/couldn't read a supplier cost for Linen Shirt.*Untick it to import the rest/)
    ).toBeTruthy();
  });

  it("lets the priced rows through once the unpriced one is unticked", async () => {
    const { view } = await renderCart([
      cartItem({ itemId: "item-1", externalProductId: "ext-1" }),
      cartItem({
        itemId: "item-2",
        externalProductId: "ext-2",
        preview: { ...pricedPreview("Linen Shirt")!, costLowCents: null, costHighCents: null }
      })
    ]);
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());
    expect(view.getByText("Resolve pricing issues")).toBeTruthy();

    // The positive control. Without it the pair above only proves the button can
    // be disabled, not that unticking is the way out — which is the whole
    // instruction the note gives the merchant.
    await act(async () => {
      fireEvent.press(view.getByLabelText("Linen Shirt, selected for import"));
    });

    expect(view.getByText("Import & publish 1")).toBeTruthy();
    expect(view.queryByText("Resolve pricing issues")).toBeNull();
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

    const button = view.getByLabelText("Import and publish 0 products to your store");
    expect(button.props.accessibilityState.disabled).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * 3b — Import settings
 *
 * The three values the server consults on every import. Before this screen they
 * were readable and writable over HTTP and unreachable from the app, which is the
 * same defect as `SUPPLIER_VARIANT_UNBOUND` having no remedy: a setting nobody can
 * change is a hardcoded constant wearing a table, and the import cart's line about
 * turning Marketplace listing on was a promise about a control that did not exist.
 * ------------------------------------------------------------------ */

describe("ImportPolicyScreen", () => {
  const route = { params: {} };

  async function renderPolicy(policy: StoreImportPolicy | "unavailable" = storePolicy()) {
    if (policy === "unavailable") {
      mockGetStorePolicy.mockRejectedValue(new PulseApiError("down", 503, "provider_unavailable"));
    } else {
      mockGetStorePolicy.mockResolvedValue(policy);
    }
    const nav = navigation();
    const view = render(<ImportPolicyScreen navigation={nav} route={route} />);
    await settle();
    await waitFor(() => expect(mockGetStorePolicy).toHaveBeenCalled());
    return { view, nav };
  }

  it("shows each setting in the state the server says it is in", async () => {
    const { view } = await renderPolicy(
      storePolicy({ autoPublish: true, marketplaceAutolist: false })
    );
    await waitFor(() => expect(view.getByTestId("import-policy-auto-publish")).toBeTruthy());

    expect(view.getByTestId("import-policy-auto-publish").props.value).toBe(true);
    expect(view.getByTestId("import-policy-marketplace-autolist").props.value).toBe(false);
  });

  it("writes only the field that changed", async () => {
    // The PATCH claim, at the screen. Four settings share one row, so a writer
    // that sent the whole policy would overwrite a margin the merchant changed on
    // the import cart thirty seconds ago with this screen's stale copy of it.
    const { view } = await renderPolicy();
    await waitFor(() => expect(view.getByTestId("import-policy-marketplace-autolist")).toBeTruthy());

    mockUpdateStorePolicy.mockResolvedValue(storePolicy({ marketplaceAutolist: true }));
    await act(async () => {
      fireEvent(view.getByTestId("import-policy-marketplace-autolist"), "valueChange", true);
    });

    expect(mockUpdateStorePolicy).toHaveBeenCalledTimes(1);
    const [, changes] = mockUpdateStorePolicy.mock.calls[0];
    expect(Object.keys(changes)).toEqual(["marketplaceAutolist"]);
    expect(changes.marketplaceAutolist).toBe(true);
  });

  it("takes the server's answer rather than assuming the write landed", async () => {
    // `configured` and `pricingSource` are computed server-side. A screen that
    // assumed its own optimistic value would go on saying "PulseSoc's default"
    // after the merchant had just configured one.
    const { view } = await renderPolicy(storePolicy({ configured: false }));
    await waitFor(() => expect(view.getByText(/You haven't set a rule/)).toBeTruthy());

    mockUpdateStorePolicy.mockResolvedValue(
      storePolicy({ configured: true, pricingSource: "STORE", autoPublish: false })
    );
    await act(async () => {
      fireEvent(view.getByTestId("import-policy-auto-publish"), "valueChange", false);
    });

    await waitFor(() => expect(view.getByText(/Every product you import is priced by this rule/)).toBeTruthy());
    expect(view.getByTestId("import-policy-auto-publish").props.value).toBe(false);
  });

  it("puts a failed toggle back where it was, and says so", async () => {
    // The worst available outcome is a toggle that stays where the merchant left
    // it after the write failed: the app then disagrees with the server about
    // whether every future import goes marketplace-wide, silently.
    const { view } = await renderPolicy(storePolicy({ marketplaceAutolist: false }));
    await waitFor(() => expect(view.getByTestId("import-policy-marketplace-autolist")).toBeTruthy());

    mockUpdateStorePolicy.mockRejectedValue(new PulseApiError("down", 503, "provider_unavailable"));
    await act(async () => {
      fireEvent(view.getByTestId("import-policy-marketplace-autolist"), "valueChange", true);
    });

    await waitFor(() => expect(view.getByText("That didn't save. Your settings are unchanged.")).toBeTruthy());
    expect(view.getByTestId("import-policy-marketplace-autolist").props.value).toBe(false);
  });

  it("draws a failed read as a failure, never as no settings", async () => {
    const { view } = await renderPolicy("unavailable");

    await waitFor(() => expect(view.queryByTestId("import-policy-auto-publish")).toBeNull());
    // The EMPTY copy belongs to a merchant with no store. Showing it here would
    // tell someone whose request timed out that they have nothing to configure.
    expect(view.queryByText(/You need a store before you can set import rules/)).toBeNull();
  });

  it("says publishing and marketplace distribution are two decisions", async () => {
    // §19/§20's whole point, and the reason these are two switches and not one.
    const { view } = await renderPolicy();
    await waitFor(() => expect(view.getByTestId("import-policy-auto-publish")).toBeTruthy());

    expect(view.getByText(/Marketplace listing is a separate decision/)).toBeTruthy();
  });

  /**
   * §12 at the surface: the merchant can finally state what freight costs them.
   *
   * The backend has priced against landed cost for a while, and until this control
   * existed the only way to supply the number was a hand-rolled PATCH — which
   * means in practice nobody supplied it, and every margin PulseSoc showed was
   * measured against the item cost alone. That is the §31 failure exactly: a
   * feature that is real everywhere except where a merchant could reach it.
   */
  describe("declaring what the supplier charges to ship", () => {
    async function withAllowance(cents: number | null) {
      const { view } = await renderPolicy(
        storePolicy({
          shippingAllowanceCents: cents,
          shippingAllowanceSource: cents === null ? "PLATFORM_DEFAULT" : "STORE"
        })
      );
      await waitFor(() =>
        expect(view.getByTestId("import-policy-shipping-allowance")).toBeTruthy()
      );
      return view;
    }

    const field = (view: any) => view.getByTestId("import-policy-shipping-allowance");
    const saveButton = (view: any) => view.getByTestId("import-policy-shipping-allowance-save");

    it("shows an undeclared allowance as blank, never as zero", async () => {
      // The whole §12 defect in one assertion. A field rendering `null` as "0.00"
      // is the screen telling the merchant their supplier ships free, in the
      // supplier's own voice, on no evidence at all.
      const view = await withAllowance(null);
      expect(field(view).props.value).toBe("");
    });

    it("does not describe an undeclared allowance as free shipping", async () => {
      const view = await withAllowance(null);
      expect(view.getByText(/Nobody has told us/)).toBeTruthy();
      // No copy anywhere on the card may reduce "we don't know" to "it's free".
      expect(view.queryByText(/free/i)).toBeNull();
      expect(view.queryByText(/no shipping cost/i)).toBeNull();
    });

    it("shows a declared amount in the units the merchant typed it in", async () => {
      const view = await withAllowance(1250);
      expect(field(view).props.value).toBe("12.50");
    });

    it("states the stored figure in prose, not only inside the text box", async () => {
      // The text box is a draft — it holds whatever is being typed, including
      // after a save fails. That is only safe because the setting itself is
      // written out somewhere the draft cannot overwrite.
      const view = await withAllowance(1250);
      expect(view.getByText(/Your store is set to \$12\.50 a unit/)).toBeTruthy();
    });

    it("tells a merchant who declared zero what they declared", async () => {
      // `0` and `null` render as two different cards on purpose. Sharing copy
      // between them would make a real answer look like a missing one, and the
      // merchant would keep being nagged for a number they already gave.
      const view = await withAllowance(0);
      expect(field(view).props.value).toBe("0.00");
      expect(view.getByText(/already inside what your supplier charges/)).toBeTruthy();
    });

    it("saves what the merchant typed, in cents, and nothing else", async () => {
      const view = await withAllowance(null);
      mockUpdateStorePolicy.mockResolvedValue(storePolicy({ shippingAllowanceCents: 450 }));

      fireEvent.changeText(field(view), "4.50");
      await act(async () => {
        fireEvent.press(saveButton(view));
      });

      expect(mockUpdateStorePolicy).toHaveBeenCalledTimes(1);
      const [, changes] = mockUpdateStorePolicy.mock.calls[0];
      expect(Object.keys(changes)).toEqual(["shippingAllowanceCents"]);
      expect(changes.shippingAllowanceCents).toBe(450);
    });

    it("saves a typed zero as zero", async () => {
      // A merchant whose supplier bakes freight into the item price has a real
      // answer, and it is `0`. The path that drops it is the same falsy check that
      // drops it in the client, so it is pinned at both ends.
      const view = await withAllowance(null);
      mockUpdateStorePolicy.mockResolvedValue(storePolicy({ shippingAllowanceCents: 0 }));

      fireEvent.changeText(field(view), "0");
      await act(async () => {
        fireEvent.press(saveButton(view));
      });

      const [, changes] = mockUpdateStorePolicy.mock.calls[0];
      expect(changes.shippingAllowanceCents).toBe(0);
    });

    it("warns before saving zero, not after", async () => {
      const view = await withAllowance(null);
      fireEvent.changeText(field(view), "0");
      await waitFor(() =>
        expect(view.getByText(/tells us shipping is already in the item price/)).toBeTruthy()
      );
      expect(mockUpdateStorePolicy).not.toHaveBeenCalled();
    });

    it("will not write a half-typed number", async () => {
      // "1" is a waypoint to "12.50". A field that wrote on every keystroke would
      // price the merchant's entire store against one cent of freight for as long
      // as it took them to type the second character.
      const view = await withAllowance(null);
      fireEvent.changeText(field(view), "1");
      fireEvent.changeText(field(view), "12");
      fireEvent.changeText(field(view), "12.50");
      await settle();
      expect(mockUpdateStorePolicy).not.toHaveBeenCalled();
    });

    it("offers no Save until the typed amount is usable and different", async () => {
      const view = await withAllowance(900);
      // Unchanged: the field agrees with the store, so there is nothing to save.
      expect(saveButton(view).props.accessibilityState.disabled).toBe(true);

      fireEvent.changeText(field(view), "abc");
      await waitFor(() =>
        expect(view.getByText(/Enter an amount like 4.50/)).toBeTruthy()
      );
      expect(saveButton(view).props.accessibilityState.disabled).toBe(true);

      fireEvent.changeText(field(view), "12.50");
      await waitFor(() =>
        expect(saveButton(view).props.accessibilityState.disabled).toBe(false)
      );
    });

    it("ignores a press on a Save that is off", async () => {
      const view = await withAllowance(900);
      await act(async () => {
        fireEvent.press(saveButton(view));
      });
      expect(mockUpdateStorePolicy).not.toHaveBeenCalled();
    });

    it("ignores a press begun while Save was on and finished after it went off", async () => {
      // The one press `disabled` does not stop, and the reason the handler carries
      // the same rule as the affordance rather than only checking for a null parse.
      //
      // A Pressable that is disabled at rest blocks every path in — so a test that
      // just presses a greyed-out button proves nothing about the handler. What
      // gets through is a press whose *grant* happened while the button was live:
      // the touch is already owned by the time the value changes underneath it, and
      // the release still runs. Reproduced here by driving the responder directly,
      // because `fireEvent.press` is grant-and-release in one call and cannot
      // express a state change in the middle.
      //
      // The value it lands on has to parse cleanly, or this proves nothing: the
      // handler's other check (`parsed === null`) would catch an empty field on its
      // own, and the test would stay green with the `!canSave` guard deleted. So
      // the merchant corrects the figure back to the one already stored — a
      // perfectly valid number that must not be written, because writing it is a
      // PATCH that changes nothing and re-times a rate limit for no reason.
      const view = await withAllowance(900);
      fireEvent.changeText(field(view), "12.50");
      await waitFor(() =>
        expect(saveButton(view).props.accessibilityState.disabled).toBe(false)
      );

      const touch = { nativeEvent: {}, currentTarget: 1, target: 1, persist: () => undefined };
      saveButton(view).props.onStartShouldSetResponder?.();
      saveButton(view).props.onResponderGrant?.(touch);
      // Changes back under the finger, between touch-down and touch-up.
      fireEvent.changeText(field(view), "9.00");
      await act(async () => {
        saveButton(view).props.onResponderRelease?.(touch);
      });

      expect(mockUpdateStorePolicy).not.toHaveBeenCalled();
    });

    it("lets a merchant take a declaration back without claiming shipping is free", async () => {
      // The way back to "unknown" for someone who declared a figure they can no
      // longer stand behind. It must not be spelled `0`, which is the opposite
      // claim, so it sends the sentinel the server compares against.
      const view = await withAllowance(900);
      mockUpdateStorePolicy.mockResolvedValue(storePolicy({ shippingAllowanceCents: null }));

      await act(async () => {
        fireEvent.press(view.getByTestId("import-policy-shipping-allowance-clear"));
      });

      const [, changes] = mockUpdateStorePolicy.mock.calls[0];
      expect(changes.shippingAllowanceCents).toBe("UNKNOWN");
      await waitFor(() => expect(field(view).props.value).toBe(""));
    });

    it("offers no way to clear an allowance nobody declared", async () => {
      const view = await withAllowance(null);
      expect(view.queryByTestId("import-policy-shipping-allowance-clear")).toBeNull();
    });

    it("takes the server's figure rather than the merchant's", async () => {
      // The server normalises and may answer with something other than what was
      // sent. A screen that kept its own optimistic copy would show a number the
      // store is not priced against.
      const view = await withAllowance(null);
      mockUpdateStorePolicy.mockResolvedValue(storePolicy({ shippingAllowanceCents: 1000 }));

      fireEvent.changeText(field(view), "9.99");
      await act(async () => {
        fireEvent.press(saveButton(view));
      });

      await waitFor(() => expect(field(view).props.value).toBe("10.00"));
    });

    it("keeps the draft when the write fails, and does not claim it saved", async () => {
      // Both halves matter. Discarding the entry would let a 503 silently eat a
      // number the merchant believes they set; keeping it without correcting the
      // prose would leave the box reading $9.00 beside a store priced at nothing.
      const view = await withAllowance(null);
      mockUpdateStorePolicy.mockRejectedValue(
        new PulseApiError("down", 503, "provider_unavailable")
      );

      fireEvent.changeText(field(view), "9.00");
      await act(async () => {
        fireEvent.press(saveButton(view));
      });

      await waitFor(() =>
        expect(view.getByText("That didn't save. Your settings are unchanged.")).toBeTruthy()
      );
      expect(field(view).props.value).toBe("9.00");
      // The setting itself did not move, and the card still says so.
      expect(view.getByText(/Nobody has told us/)).toBeTruthy();
      // And Save is still live, so retrying is one tap rather than a retype.
      expect(saveButton(view).props.accessibilityState.disabled).toBe(false);
    });

    it("re-seeds the box only when the server has confirmed a figure", async () => {
      // The rollback case above and this one differ by one thing — whether the
      // write landed — and the screen must not tell them apart by watching a value
      // move. An optimistic write and its rollback land in the same React commit,
      // so a value-watching effect sees nothing happen at all.
      const view = await withAllowance(null);
      mockUpdateStorePolicy.mockResolvedValue(storePolicy({ shippingAllowanceCents: 900 }));

      fireEvent.changeText(field(view), "9");
      await act(async () => {
        fireEvent.press(saveButton(view));
      });

      // Normalised to the stored figure's own formatting, from the server's answer.
      await waitFor(() => expect(field(view).props.value).toBe("9.00"));
      expect(view.getByText(/Your store is set to \$9\.00 a unit/)).toBeTruthy();
      expect(saveButton(view).props.accessibilityState.disabled).toBe(true);
    });
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
      // Always present, never undefined. `normalizeImportedRow` fills it on every
      // row it hands out, so a fixture that omitted it would be describing a shape
      // production cannot produce. This screen iterates it, so the omission showed
      // up as a crash rather than as a wrong answer -- which is the good outcome.
      attention: [] as string[],
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
    options: {
      isSandbox?: boolean;
      // Defaults to a healthy drain so the tests written before the drain
      // existed keep asserting what they were written to assert. The banner is
      // opt-in here, and its absence is itself asserted below.
      drainState?: string | null;
      params?: Record<string, unknown> | undefined;
    } = {}
  ) {
    mockListSupplierObligations.mockResolvedValue({
      obligations,
      isSandbox: options.isSandbox ?? true,
      drainState: options.drainState ?? "DRAINING"
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

  it("does not leave a queued order claiming it is about to be sent when nothing sends", async () => {
    // The gap-17 defect at the screen. `READY` renders "Queued to send to your
    // supplier" — a promise about a background worker that has no entry point
    // in the Procfile, so the order sits there permanently. Nothing on the
    // screen could contradict it, and nothing could even observe it: the worker
    // returned its counts to stdout and recorded nothing, so the claim was
    // unfalsifiable rather than merely wrong.
    const { view } = await renderOrders([obligation({ state: "READY", supplierOrderPlaced: true })], {
      drainState: "NO_DRAIN_HAS_EVER_RUN"
    });
    await waitFor(() => expect(view.getByText("Queued to send to your supplier")).toBeTruthy());
    expect(view.getByText("Queued orders are not being sent")).toBeTruthy();
    expect(view.getByText(/not running on this account yet/i)).toBeTruthy();
  });

  it("does not raise the banner when the server says the queue is moving", async () => {
    // A banner on a healthy queue is worse than none: it teaches the merchant
    // that this card is noise, and the card only exists for the case where it
    // is the one true thing on the screen.
    const { view } = await renderOrders([obligation({ state: "READY" })], {
      drainState: "DRAINING"
    });
    await waitFor(() => expect(view.getByText("Queued to send to your supplier")).toBeTruthy());
    expect(view.queryByText("Queued orders are not being sent")).toBeNull();
  });

  it("distinguishes a worker that is failing from one that was never started", async () => {
    // Different next actions: one is an incident on a process that is running,
    // the other is setup that was never finished. Collapsing them sends whoever
    // reads this hunting for a process that is already there.
    const { view } = await renderOrders([obligation({ state: "READY" })], {
      drainState: "TICKING_BUT_NOT_COMPLETING"
    });
    await waitFor(() => expect(view.getByText(/failing on this account/i)).toBeTruthy());
    expect(view.queryByText(/not running on this account yet/i)).toBeNull();
  });

  it("says nothing about the drain when the server does not report one", async () => {
    // An older server sends no `drain`. Silence is correct — this build has
    // genuinely not been told anything, which is not the same as the old defect
    // of making a positive promise on no evidence.
    const { view } = await renderOrders([obligation({ state: "READY" })], {
      drainState: null
    });
    await waitFor(() => expect(view.getByText("Queued to send to your supplier")).toBeTruthy());
    expect(view.queryByText("Queued orders are not being sent")).toBeNull();
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
    // Rendered with an unhealthy drain on purpose, so the banner is genuinely
    // on screen before the refresh fails. Asserting that a card is absent after
    // an error proves nothing unless it was present beforehand.
    const { view } = await renderOrders([obligation()], {
      drainState: "NO_DRAIN_HAS_EVER_RUN"
    });
    await waitFor(() => expect(view.getByText("Ceramic Mug")).toBeTruthy());
    expect(view.getByText("Queued orders are not being sent")).toBeTruthy();

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
    // And the drain banner, which the next battery caught this test missing for
    // exactly the same reason one gap later. A drain verdict is a claim about
    // the server sourced from one response; holding it through a failed refresh
    // tells the merchant something no live response is saying.
    expect(view.queryByText("Queued orders are not being sent")).toBeNull();
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

  it("does not count an order that was never sent as one that was placed", async () => {
    // A row combination the server could not previously produce. `BLOCKED` is
    // what `dispatch` settles to when it refuses to send — nothing reached the
    // supplier — yet the obligation arrived with `supplierOrderPlaced: true`
    // and `SUPPLIER_ORDER_ALREADY_PLACED`, because both were derived from an
    // intent row existing rather than from any evidence of a send. The buyer
    // had paid, nothing had been ordered, and this screen said it was handled.
    //
    // Now it is an obligation again: no supplier order yet, and orderable.
    const { view } = await renderOrders([
      obligation({
        state: "BLOCKED",
        supplierOrderPlaced: false,
        intentId: "cjf_dead",
        providerOrderId: null,
        lastError: "supplier_sku_missing",
        blockers: [],
        canPlaceSupplierOrder: true
      })
    ]);
    await waitFor(() => expect(view.getByText("Not sent — needs your attention")).toBeTruthy());
    expect(view.queryByText(/1 of these have no supplier order yet/)).toBeTruthy();
    // And not in the blocked count. The previous attempt failed, but the order
    // is actionable, so calling it un-orderable would send the merchant looking
    // for a reason that is no longer there.
    expect(view.queryByText(/could not be ordered as things stand/)).toBeNull();
    expect(view.queryByText(SUPPLIER_OBLIGATION_BLOCKER_COPY.SUPPLIER_ORDER_ALREADY_PLACED)).toBeNull();
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
      // Always present, never undefined. `normalizeImportedRow` fills it on every
      // row it hands out, so a fixture that omitted it would be describing a shape
      // production cannot produce. This screen iterates it, so the omission showed
      // up as a crash rather than as a wrong answer -- which is the good outcome.
      attention: [] as string[],
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

  /* ------------------------------------------------------------------
   * The second kind of wrong
   *
   * `syncState` answers "can we still reach the supplier about this". It reads
   * OK for a product whose cost quadrupled overnight, because that sync
   * succeeded -- so a screen keyed on sync state alone printed "Nothing needs
   * your attention" over a listing losing money on every sale. Every test above
   * this line passes on that screen. These are the ones that do not.
   * ------------------------------------------------------------------ */

  it("does not call a catalogue healthy when a synced product is selling below cost", async () => {
    const { view } = await renderSync({
      items: [product({ syncState: "OK", attention: ["SELLING_BELOW_COST"] })]
    });
    await waitFor(() =>
      expect(view.getByText("Selling below what the supplier charges")).toBeTruthy()
    );
    // The whole defect in one assertion: sync state is fine and the screen used
    // to have nothing else to look at.
    expect(view.queryByText("Nothing needs your attention")).toBeNull();
  });

  it("puts the attention reason in words and never its code", async () => {
    const { view } = await renderSync({ items: [product({ attention: ["MARGIN_LOST"] })] });
    await waitFor(() => expect(view.getByText("Almost no margin left")).toBeTruthy());
    expect(view.queryByText("MARGIN_LOST")).toBeNull();
  });

  it("files what the merchant can act on apart from what they can only be told", async () => {
    // "Needs you" is only read if everything in it is actionable. An unreadable
    // stock count is real and entirely outside the merchant's control, so it
    // belongs beneath the things they can fix, not among them.
    const { view } = await renderSync({
      items: [product({ attention: ["SELLING_BELOW_COST", "STOCK_UNREADABLE"] })]
    });
    await waitFor(() => expect(view.getByText("Needs you")).toBeTruthy());
    expect(view.getByText("Worth knowing")).toBeTruthy();
    expect(view.getByText("Stock could not be read")).toBeTruthy();
  });

  it("shows a sync problem and a supplier problem on the same product as two rows", async () => {
    // These used to be keyed by listing id alone, which collapsed two distinct
    // problems on one product into a single row -- and a duplicate React key.
    const { view } = await renderSync({
      items: [product({ syncState: "UNAVAILABLE", attention: ["SUPPLIER_OUT_OF_STOCK"] })]
    });
    await waitFor(() =>
      expect(view.getByText("Your supplier no longer offers this product")).toBeTruthy()
    );
    expect(view.getByText("The supplier has none left")).toBeTruthy();
  });

  it("ignores an attention reason this build has no words for", async () => {
    // Same rule as an unknown sync state. A reason the backend grew and this
    // build cannot translate is dropped by `revisionAttention` rather than shown
    // raw -- and `test_revision_attention_copy.py` is what stops that silence
    // from becoming permanent.
    const { view } = await renderSync({ items: [product({ attention: ["TELEPORTED"] })] });
    await waitFor(() => expect(view.getByText("Nothing needs your attention")).toBeTruthy());
    expect(view.queryByText("TELEPORTED")).toBeNull();
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

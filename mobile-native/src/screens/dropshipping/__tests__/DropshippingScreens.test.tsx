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
import { Text } from "react-native";
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
const mockConnectSupplier = jest.fn();
const mockSearchProducts = jest.fn();

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
  connectSupplier: (...args: unknown[]) => mockConnectSupplier(...args),
  searchSupplierProducts: (...args: unknown[]) => mockSearchProducts(...args)
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
import { ConnectSupplierScreen } from "../ConnectSupplierScreen";
import { DropshippingHubScreen } from "../DropshippingHubScreen";
import { DropshippingProductsScreen } from "../DropshippingProductsScreen";
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
   */
  it("offers a retry only where a second attempt could answer differently", () => {
    const retryable = new Set<DropshippingState>([
      "SESSION_EXPIRED",
      "UNAUTHORIZED",
      "CSRF_INVALID",
      "STALE_STORE_CONTEXT",
      "INVALID_CREDENTIAL",
      "SUPPLIER_DISCONNECTED",
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

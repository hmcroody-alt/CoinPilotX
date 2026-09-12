/**
 * The dropshipping client, pinned at the points where a plausible "cleanup"
 * would turn it into a liar.
 *
 * Each block below corresponds to an invariant that has a specific, expensive
 * failure mode:
 *
 * 1. UNKNOWN IS NOT ZERO. `centsOrNull` returns null for anything unreadable.
 *    The one-character change to `Number(x) || 0` makes every product with an
 *    unreadable supplier cost look free, and a merchant pricing off that ships
 *    at a loss. Pinned on the helper *and* through every normalizer that uses
 *    it, because a normalizer that stops calling it is the same bug.
 *
 * 2. UNKNOWN STOCK IS NOT OUT OF STOCK. The default for a missing stock signal
 *    is UNKNOWN. Defaulting to OUT_OF_STOCK hides sellable products; defaulting
 *    to IN_STOCK oversells them. Both are worse than saying so.
 *
 * 3. THE CLIENT CANNOT ASSERT ECONOMICS. `importSelected` sends ids and a
 *    pricing rule. There is no parameter, and no serialized key, through which
 *    cost, stock or title could travel from this device to the server. This is
 *    asserted against the actual request body rather than the type signature,
 *    since a type is not a wire format.
 *
 * 4. ERROR CLASSIFICATION IS BY CAUSE. A disconnected supplier, an unavailable
 *    provider and a signed-out session produce different states, because they
 *    have different fixes and only one of them is the merchant's to make.
 *
 * 5. THE GAP LEDGER IS COUNTED. Faking supplier orders would shorten this list,
 *    and this test says so.
 */

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args),
  PulseApiError: class PulseApiError extends Error {
    status: number;
    code?: string;
    details?: Record<string, unknown>;
    constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
      super(message);
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }
}));

import {
  DROPSHIPPING_DATA_GAPS,
  IMPORT_OUTCOMES,
  PUBLISH_PROBLEMS,
  bindConnectionShop,
  centsOrNull,
  connectionCanFulfil,
  connectionIsUsable,
  connectionNeedsAttention,
  listConnectionShops,
  getImportCart,
  getImportedProduct,
  getSupplierProduct,
  importNeedsReview,
  importSelected,
  listImportedProducts,
  listSupplierConnections,
  previewPricing,
  retryAfterSeconds,
  searchSupplierProducts,
  stateForError,
  updateImportedProduct
} from "../dropshipping";
import { PulseApiError } from "../pulseApi";

const SCOPE = { businessId: "biz-1", storeId: 42 } as any;

beforeEach(() => {
  mockPulseApi.mockReset();
});

/** The body of the nth request, parsed. */
function bodyOf(call = 0): any {
  const options = mockPulseApi.mock.calls[call]?.[1];
  return options?.body ? JSON.parse(options.body) : null;
}

/* ------------------------------------------------------------------ *
 * 1. Unknown cost is null, never zero
 * ------------------------------------------------------------------ */

describe("centsOrNull refuses to invent a number", () => {
  it("returns null for every unreadable input", () => {
    // `[]`, `""`, `" "` and `false` all coerce to 0 under plain `Number`. Each
    // one of them is a supplier price this app must refuse to invent.
    for (const input of [null, undefined, "", " ", "abc", {}, [], [1], NaN, Infinity, -Infinity, true, false]) {
      expect(centsOrNull(input)).toBeNull();
    }
  });

  it("keeps a real zero, which is a different fact from unknown", () => {
    // A supplier that genuinely says 0 is saying something. Mapping it to null
    // would be the mirror of the bug this whole module is about.
    expect(centsOrNull(0)).toBe(0);
    expect(centsOrNull("0")).toBe(0);
  });

  it("reads numeric strings, because JSON money often arrives as one", () => {
    expect(centsOrNull("1999")).toBe(1999);
    expect(centsOrNull(1999)).toBe(1999);
  });
});

describe("unknown cost survives normalization", () => {
  it("leaves a missing catalogue price null rather than zero", async () => {
    mockPulseApi.mockResolvedValue({
      products: [{ external_product_id: "p1", title: "Thing", cost_low_cents: null, cost_high_cents: "oops" }],
      total: 1
    });
    const result = await searchSupplierProducts(SCOPE, "c1", { filters: { keyword: "thing" } });
    expect(result.products[0].costLowCents).toBeNull();
    expect(result.products[0].costHighCents).toBeNull();
  });

  it("leaves a missing variant cost null on an imported draft", async () => {
    mockPulseApi.mockResolvedValue({
      listing_id: 7,
      variants: [{ variant_id: 1, cost_cents: null, retail_cents: null, margin_state: "UNKNOWN" }],
      supplier: {},
      validation: { publishable: false, problems: [] }
    });
    const draft = await updateImportedProduct(SCOPE, "c1", 7, { title: "x" });
    expect(draft.variants[0].costCents).toBeNull();
    expect(draft.variants[0].retailCents).toBeNull();
  });

  it("leaves a missing supplier cost null on the imported products list", async () => {
    mockPulseApi.mockResolvedValue({ items: [{ id: 5, title: "T", supplier_cost_cents: null }], count: 1 });
    const result = await listImportedProducts(SCOPE, "c1");
    expect(result.items[0].supplierCostCents).toBeNull();
  });

  it("keeps a fractional margin percentage unrounded", async () => {
    // Rounding 24.6 to 25 moves the product across the healthy/low boundary and
    // tells the merchant a different thing than the server decided.
    mockPulseApi.mockResolvedValue({
      rule: { type: "COST_PLUS_PERCENT", value: 60 },
      quotes: [{ cost_cents: 1000, proposed_retail_cents: 1600, margin_percent: 24.6, margin_state: "LOW_MARGIN" }]
    });
    const result = await previewPricing([1000], { type: "COST_PLUS_PERCENT", value: 60 });
    expect(result.quotes[0].marginPercent).toBe(24.6);
  });

  it("carries a null cost into the pricing preview instead of dropping it", async () => {
    mockPulseApi.mockResolvedValue({ rule: { type: "MULTIPLIER", value: 2 }, quotes: [] });
    await previewPricing([1000, null, 250], { type: "MULTIPLIER", value: 2 });
    expect(bodyOf().cost_cents).toEqual([1000, null, 250]);
  });
});

/* ------------------------------------------------------------------ *
 * 2. Unknown stock is not out of stock
 * ------------------------------------------------------------------ */

describe("unknown stock stays unknown", () => {
  it("defaults an absent stock signal to UNKNOWN, not OUT_OF_STOCK", async () => {
    mockPulseApi.mockResolvedValue({
      product: { external_product_id: "p1", title: "Thing", variants: [{ provider_variant_id: "v1" }] }
    });
    const detail = await getSupplierProduct(SCOPE, "c1", "p1");
    expect(detail.variants[0].stockState).toBe("UNKNOWN");
    expect(detail.variants[0].stockQuantity).toBeNull();
  });

  it("does not treat an unreadable quantity as zero stock", async () => {
    mockPulseApi.mockResolvedValue({
      product: {
        external_product_id: "p1",
        variants: [{ provider_variant_id: "v1", stock_quantity: "unknown", stock_state: "UNKNOWN" }]
      }
    });
    const detail = await getSupplierProduct(SCOPE, "c1", "p1");
    expect(detail.variants[0].stockQuantity).toBeNull();
    expect(detail.variants[0].stockState).not.toBe("OUT_OF_STOCK");
  });
});

/* ------------------------------------------------------------------ *
 * 3. The client cannot assert supplier economics
 * ------------------------------------------------------------------ */

describe("the import request carries ids, never economics", () => {
  const FORBIDDEN = [
    "cost",
    "cost_cents",
    "price",
    "supplier_cost",
    "supplier_cost_cents",
    "stock",
    "stock_quantity",
    "inventory",
    "title",
    "variants",
    "media"
  ];

  it("sends only item ids and a pricing rule", async () => {
    mockPulseApi.mockResolvedValue({ results: [], imported: 0, requested: 2 });
    await importSelected(SCOPE, "c1", {
      itemIds: ["i1", "i2"],
      pricingRule: { type: "COST_PLUS_PERCENT", value: 60 }
    });

    const body = bodyOf();
    const keys = Object.keys(body);
    // Whatever else the scope contributes, none of it is a supplier fact.
    for (const forbidden of FORBIDDEN) {
      expect(keys).not.toContain(forbidden);
    }
    expect(body.item_ids).toEqual(["i1", "i2"]);

    // Nothing outside the rule mentions supplier economics at all. The rule is
    // excluded from the sweep because `COST_PLUS_PERCENT` is a policy name, not
    // a cost — it is the one place the word may legitimately appear.
    const { pricing_rule: _rule, ...rest } = body;
    expect(JSON.stringify(rest)).not.toMatch(/cost|stock|inventory|price/i);
  });

  it("sends a rule, which is a policy, not a computed price", async () => {
    mockPulseApi.mockResolvedValue({ results: [], imported: 0, requested: 1 });
    await importSelected(SCOPE, "c1", { itemIds: ["i1"], pricingRule: { type: "MULTIPLIER", value: 3 } });
    expect(bodyOf().pricing_rule).toEqual({ type: "MULTIPLIER", value: 3 });
    expect(bodyOf().retail_cents).toBeUndefined();
  });

  it("cannot send a supplier-owned field through a draft edit", async () => {
    mockPulseApi.mockResolvedValue({ listing_id: 7, variants: [], supplier: {}, validation: {} });
    await updateImportedProduct(SCOPE, "c1", 7, {
      title: "Mine",
      // Deliberately smuggled in as an untyped extra to prove the serializer,
      // not the type, is what stops it.
      ...({ costCents: 1, stockQuantity: 99, providerVariantId: "v9" } as any)
    });
    const fields = bodyOf().fields;
    expect(Object.keys(fields)).toEqual(["title"]);
  });

  it("sends only the fields the merchant actually edited", async () => {
    // Sending untouched fields would mark them merchant-owned and freeze them
    // against supplier corrections the merchant never asked to stop receiving.
    mockPulseApi.mockResolvedValue({ listing_id: 7, variants: [], supplier: {}, validation: {} });
    await updateImportedProduct(SCOPE, "c1", 7, { category: "Home" });
    expect(Object.keys(bodyOf().fields)).toEqual(["category"]);
  });

  it("unprices a variant with null rather than zero", async () => {
    mockPulseApi.mockResolvedValue({ listing_id: 7, variants: [], supplier: {}, validation: {} });
    await updateImportedProduct(SCOPE, "c1", 7, { prices: { "1": null, "2": 2500 } });
    expect(bodyOf().fields.price_cents).toEqual({ "1": null, "2": 2500 });
  });
});

/* ------------------------------------------------------------------ *
 * 4. States are classified by cause
 * ------------------------------------------------------------------ */

describe("stateForError separates causes that have different fixes", () => {
  it("maps auth failures to UNAUTHORIZED", () => {
    expect(stateForError(new PulseApiError("no", 401))).toBe("UNAUTHORIZED");
    expect(stateForError(new PulseApiError("no", 403))).toBe("UNAUTHORIZED");
  });

  /**
   * The store-session bug in full. A merchant who owned an approved, open store
   * tapped "Connect to CJ" and was told they were not signed in to it. Three
   * things stacked up to produce that: the write gate refused the native app's
   * bearer, the supplier pack answered with a field the client does not read,
   * and — with no code left to read — this function fell through to the status
   * check, where a 403 is indistinguishable from a dead session.
   *
   * The status fallback still stands (the two assertions above), because a
   * server that genuinely says nothing has told us nothing. What is pinned here
   * is that each cause the server *does* name survives the trip: seven codes,
   * seven different things for a merchant to do about them. Collapsing any of
   * them back into UNAUTHORIZED sends a merchant to sign in again and land on
   * the identical screen.
   */
  it.each([
    ["csrf", 403, "CSRF_INVALID"],
    ["login_required", 401, "SESSION_EXPIRED"],
    ["unauthorized", 401, "SESSION_EXPIRED"],
    ["store_not_found", 404, "STORE_NOT_FOUND"],
    ["store_access_revoked", 403, "STORE_ACCESS_REVOKED"],
    ["account_hold", 403, "STORE_ACCESS_REVOKED"],
    ["stale_store_context", 409, "STALE_STORE_CONTEXT"],
    ["merchant_identity_unresolved", 409, "STORE_MAPPING_MISSING"],
    ["forbidden", 403, "SUPPLIER_CONNECTION_FORBIDDEN"],
    ["store_not_approved", 403, "STORE_NOT_APPROVED"]
  ])("reads %s as its own cause, not as a signed-out session", (code, status, state) => {
    expect(stateForError(new PulseApiError("no", status as number, code as string))).toBe(state);
  });

  /**
   * The same collapse, one list further down, and the reason the ordering in
   * `stateForError` is load-bearing rather than cosmetic.
   *
   * `DISCONNECTED_CODES` sits below the bare `status === 401 || 403` check, so
   * every entry in it that actually arrives as a 401 was unreachable: the
   * catch-all matched first and answered UNAUTHORIZED. This was found live --
   * a CJ key that CJ itself rejected ("APIkey is wrong, please check and try
   * again", provider code 1600005) maps to `reauth_required` with HTTP 401, and
   * the merchant was told "You're not signed in to this store any more".
   *
   * That is the worst possible sentence for this cause. The PulseSoc session is
   * fine; signing out and back in changes nothing and costs the merchant their
   * place. The thing that needs re-authorizing is the *supplier* connection.
   */
  it.each([
    ["reauth_required", 401],
    ["auth_expired", 401],
    ["credential_missing", 401],
    ["supplier_disconnected", 403]
  ])("reads %s as a broken supplier connection, not a dead PulseSoc session", (code, status) => {
    expect(stateForError(new PulseApiError("no", status as number, code as string)))
      .toBe("SUPPLIER_DISCONNECTED");
  });

  /** A named provider fault likewise outranks the status it happens to carry. */
  it("keeps a named provider fault out of UNAUTHORIZED", () => {
    expect(stateForError(new PulseApiError("no", 403, "provider_unavailable"))).toBe("PROVIDER_UNAVAILABLE");
  });

  /**
   * `store_not_found` is deliberately not the generic `not_found` the rest of
   * the Business OS pack answers with. A missing *connection*, draft or cart
   * item is a 404 too, and reading those as a store the server could not match
   * would tell a merchant their store is broken when a row they deleted is
   * simply gone.
   */
  it("does not read a missing row as a missing store", () => {
    expect(stateForError(new PulseApiError("no", 404, "not_found"))).toBe("ERROR");
  });

  /**
   * A server that names no cause is the one case where the status is all there
   * is, and it must stay that way: the fix for the store-session bug was to
   * make the server name the cause, not to invent one here. An error with no
   * code and a 403 is still just "refused".
   */
  it("invents no cause when the server named none", () => {
    expect(stateForError(new PulseApiError("no", 403, ""))).toBe("UNAUTHORIZED");
    expect(stateForError(new PulseApiError("no", 409))).toBe("ERROR");
  });

  it("maps supplier credential problems to SUPPLIER_DISCONNECTED", () => {
    expect(stateForError(new PulseApiError("x", 400, "supplier_disconnected"))).toBe("SUPPLIER_DISCONNECTED");
  });

  it("maps provider outages and rate limits to PROVIDER_UNAVAILABLE", () => {
    for (const status of [429, 503, 504]) {
      expect(stateForError(new PulseApiError("x", status))).toBe("PROVIDER_UNAVAILABLE");
    }
  });

  /**
   * A server that cannot store a credential has not talked to the supplier.
   *
   * `vault.require_available()` runs in `_bootstrap()` *before*
   * `adapter.authenticate(api_key)`, deliberately: authenticating claims one of
   * the three CJ account slots this egress IP is allowed, and a deployment that
   * cannot persist the result must not spend one. So `credential_vault_unavailable`
   * is proof the key never left PulseSoc.
   *
   * It arrives as a 503, and a 503 is in the catch-all on the last line of
   * `stateForError`, so it used to land on PROVIDER_UNAVAILABLE and the connect
   * screen said "Your supplier isn't responding ... try again shortly". Both
   * halves were false: the supplier was never asked, and retrying could not
   * help, because the missing thing was three environment variables on our own
   * server. That is what a real merchant hit on production, where
   * SUPPLIER_CREDENTIAL_KEYS / _KEY_ACTIVE / SUPPLIER_ACCOUNT_INDEX_KEY are all
   * unset while staging has them.
   *
   * The second assertion is the one that fails if someone "simplifies" the fix
   * by adding the code to PROVIDER_CODES instead — that would restore exactly
   * the wrong answer while keeping the first assertion green.
   */
  it("does not blame the supplier when our own credential storage is the problem", () => {
    expect(stateForError(new PulseApiError("x", 503, "credential_vault_unavailable")))
      .toBe("CREDENTIAL_STORAGE_UNAVAILABLE");
    expect(stateForError(new PulseApiError("x", 503, "credential_vault_unavailable")))
      .not.toBe("PROVIDER_UNAVAILABLE");
  });

  /**
   * The named code has to outrank the status even if the status changes.
   * Pinning only the 503 spelling would let a server that answered 500 or 502
   * for the same condition fall back through to a generic ERROR.
   */
  it("reads the named cause regardless of the status it rides in on", () => {
    for (const status of [500, 502, 503]) {
      expect(stateForError(new PulseApiError("x", status, "credential_vault_unavailable")))
        .toBe("CREDENTIAL_STORAGE_UNAVAILABLE");
    }
  });

  /**
   * The vault's three answers are three different sentences.
   *
   * The server used to answer `credential_vault_unavailable` 503 for every way
   * a credential could fail, including two that are not outages at all: a call
   * site handing the vault something malformed, and a stored row that will not
   * open under this scope. Splitting them server-side is only half the fix —
   * the half a merchant sees is here, and adding a status the mapper has never
   * met is how a fix becomes a regression.
   *
   * 409 matches none of the status classes at the bottom of `stateForError`, so
   * without an entry in DISCONNECTED_CODES an unusable credential reads as a
   * bare "Something went wrong". The merchant would be one button away from the
   * fix — reconnect — and be shown no button at all.
   *
   * The 500 is deliberately generic and this test says so, so that a later
   * reader does not "complete the set" by giving it a screen of its own. It
   * means the bug is ours; there is nothing for the merchant to do.
   */
  it("tells the three credential failures apart the way the merchant acts on them", () => {
    expect(stateForError(new PulseApiError("x", 503, "credential_vault_unavailable")))
      .toBe("CREDENTIAL_STORAGE_UNAVAILABLE");
    expect(stateForError(new PulseApiError("x", 409, "credential_unusable")))
      .toBe("SUPPLIER_DISCONNECTED");
    expect(stateForError(new PulseApiError("x", 500, "credential_request_invalid")))
      .toBe("ERROR");
  });

  /**
   * The anti-vacuity half of the test above: it is the *status* that has no
   * answer, so the named code has to be what rescues it. A mapper that happened
   * to classify 409 correctly by accident would keep the previous test green.
   */
  it("does not leave a 409 to the status classes, which have no case for it", () => {
    expect(stateForError(new PulseApiError("x", 409))).toBe("ERROR");
    expect(stateForError(new PulseApiError("x", 409, "credential_unusable")))
      .not.toBe("ERROR");
  });

  /**
   * The five shop-binding refusals, each with its own next move.
   *
   * These were the whole reason the shop picker could not simply be built: every
   * refusal it can produce landed somewhere wrong. `shop_not_authorized` is a
   * 403, so it read as "you're not signed in to this store any more" — a
   * merchant with a healthy session sent to sign in again over a stale shop
   * list. The rest are 400s and 409s, which match none of the status classes at
   * the bottom of `stateForError`, so they arrived as a bare "Something went
   * wrong": no cause, and no hint that the fix is one tap away.
   *
   * `shop_binding_required` is the one most accounts will actually meet. It is
   * what an order against an unbound connection answers, and before the picker
   * existed there was no screen it could send anybody to.
   */
  it.each([
    ["shop_binding_required", 409, "SHOP_BINDING_REQUIRED"],
    ["shop_required", 400, "SHOP_BINDING_REQUIRED"],
    ["shop_not_authorized", 403, "SHOP_NOT_AUTHORIZED"],
    ["connection_binding_conflict", 409, "SHOP_BINDING_CONFLICT"],
    ["api_shop_binding_required", 409, "SHOP_CANNOT_FULFIL"],
    ["ambiguous_shop_name", 409, "SHOP_NAME_AMBIGUOUS"]
  ])("reads %s as a binding problem with its own fix", (code, status, state) => {
    expect(stateForError(new PulseApiError("no", status as number, code as string))).toBe(state);
  });

  /**
   * The anti-vacuity half: each of those statuses is wrong on its own, so the
   * named code has to be doing the work. Without this a mapper that classified
   * 403 and 409 correctly by luck would keep the test above green while the
   * merchant still read "sign in again".
   */
  it("does not let the status decide a binding problem", () => {
    expect(stateForError(new PulseApiError("x", 403))).toBe("UNAUTHORIZED");
    expect(stateForError(new PulseApiError("x", 409))).toBe("ERROR");
    expect(stateForError(new PulseApiError("x", 400))).toBe("ERROR");
  });

  it("does not classify an unknown failure as anything specific", () => {
    expect(stateForError(new Error("boom"))).toBe("ERROR");
    expect(stateForError(new PulseApiError("x", 500))).toBe("ERROR");
  });

  it("returns a retry hint only when the server gave one", () => {
    expect(retryAfterSeconds(new PulseApiError("x", 503, "provider_unavailable", { retry_after: 30 }))).toBe(30);
    expect(retryAfterSeconds(new PulseApiError("x", 503))).toBeNull();
    expect(retryAfterSeconds(new Error("boom"))).toBeNull();
  });
});

describe("connection usability is one predicate, not a guess per screen", () => {
  const connection = (status: string) => ({ status, provider: "cj" }) as any;

  it("treats a connected supplier as usable and unalarming", () => {
    expect(connectionIsUsable(connection("CONNECTED"))).toBe(true);
    expect(connectionNeedsAttention(connection("CONNECTED"))).toBe(false);
  });

  it("treats an expired credential as needing attention", () => {
    expect(connectionNeedsAttention(connection("AUTH_EXPIRED"))).toBe(true);
    expect(connectionIsUsable(connection("AUTH_EXPIRED"))).toBe(false);
  });

  it("does not call an unrecognised status usable", () => {
    // The failure this prevents: a new provider status renders as "Connected",
    // the merchant browses a catalogue, and every search fails.
    expect(connectionIsUsable(connection("SOMETHING_NEW"))).toBe(false);
  });

  /**
   * Usable and able-to-fulfil are two questions, and one predicate was
   * answering both.
   *
   * A supplier "shop" is an external storefront authorized inside the
   * merchant's supplier account. Importing, pricing and publishing need none —
   * PulseSoc is the storefront — which is why connecting without one is allowed
   * and why `connectionIsUsable` must keep saying yes here. Placing an order
   * does need one: `create_intent` refuses an unbound connection outright.
   *
   * So "CONNECTED" alone was reported to the merchant as "Connected and
   * working" over a connection that would refuse every order it ever received.
   */
  it("separates a connection that can import from one that can also fulfil", () => {
    const unbound = { status: "CONNECTED", provider: "cj", externalShopId: null } as any;
    const bound = { status: "CONNECTED", provider: "cj", externalShopId: "shop-1" } as any;

    expect(connectionIsUsable(unbound)).toBe(true);
    expect(connectionCanFulfil(unbound)).toBe(false);
    expect(connectionCanFulfil(bound)).toBe(true);
  });

  it("does not call a broken connection fulfilling just because a shop is recorded", () => {
    // A shop id outlives the credential that proved it. Reading only the shop
    // would report an expired connection as ready to ship.
    const expired = { status: "AUTH_EXPIRED", provider: "cj", externalShopId: "shop-1" } as any;
    expect(connectionCanFulfil(expired)).toBe(false);
  });

  it("treats an empty shop id as no shop, because that is how the server records none", () => {
    // The server writes "" rather than NULL, deliberately: every downstream
    // binding check compares the column as a string. A truthiness test that
    // only knew about null would call this connection ready to fulfil.
    const unbound = { status: "CONNECTED", provider: "cj", externalShopId: "" } as any;
    expect(connectionCanFulfil(unbound)).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * 4b. The shop list, and the choice made from it
 * ------------------------------------------------------------------ */

/**
 * Reading and binding are the two calls that had a route, a service and tests
 * on the server, and nothing on any client that could reach them. A merchant
 * could connect, import, publish and sell, then refuse every one of their own
 * orders with no recorded way out.
 */
describe("the fulfilment shop can be read and chosen", () => {
  it("asks the connection's own endpoint, sending nothing but the scope", async () => {
    mockPulseApi.mockResolvedValue({ data: { shops: [], external_shop_id: "" } });
    await listConnectionShops(SCOPE, "conn 1/2");

    const [url, options] = mockPulseApi.mock.calls[0];
    // Encoded, because a connection id travels in the path here.
    expect(url).toBe("/api/business-os/suppliers/cj/connections/conn%201%2F2/shops");
    expect(options.method).toBe("POST");
    // POST rather than GET so nothing about the merchant's supplier account
    // lands in an access log's query string.
    expect(bodyOf(0)).toEqual({ business_id: "biz-1", store_id: 42 });
  });

  it("keeps the server's fulfillable verdict instead of re-deriving one", async () => {
    // The client has `platform` and `status` in hand and could compute this.
    // It must not: the rule lives in the fulfilment layer, the server applies
    // it per row, and a second copy here would drift from the check the choice
    // is actually spent against.
    mockPulseApi.mockResolvedValue({
      data: {
        shops: [
          { shop_id: "s1", name: "Main", platform: "API", status: 1, fulfillable: true,
            unfulfillable_reason: "" },
          { shop_id: "s2", name: "Storefront", platform: "Shopify", status: 1, fulfillable: false,
            unfulfillable_reason: "api_shop_binding_required" }
        ],
        external_shop_id: ""
      }
    });
    const result = await listConnectionShops(SCOPE, "c1");

    expect(result.shops.map((shop) => [shop.externalShopId, shop.fulfillable])).toEqual([
      ["s1", true],
      ["s2", false]
    ]);
    expect(result.shops[0].unfulfillableReason).toBeNull();
    expect(result.shops[1].unfulfillableReason).toBe("api_shop_binding_required");
  });

  it("reads a missing or non-boolean verdict as cannot fulfil", async () => {
    // `Boolean(raw.fulfillable)` would read the string "false" as true, which
    // offers the merchant a shop the server is about to refuse. The safe
    // direction is the only one this field is allowed to fail in.
    mockPulseApi.mockResolvedValue({
      data: {
        shops: [
          { shop_id: "s1", name: "A" },
          { shop_id: "s2", name: "B", fulfillable: "false" },
          { shop_id: "s3", name: "C", fulfillable: 1 }
        ]
      }
    });
    const result = await listConnectionShops(SCOPE, "c1");
    expect(result.shops.map((shop) => shop.fulfillable)).toEqual([false, false, false]);
  });

  it("reports an account with no shops as an empty list, not as a failure", async () => {
    // The normal shape for selling here, and the state of most accounts: no
    // external storefront at all. The screen turns this into an instruction,
    // so it must arrive as data rather than as a thrown error.
    mockPulseApi.mockResolvedValue({ data: { shops: [], external_shop_id: "" } });
    const result = await listConnectionShops(SCOPE, "c1");
    expect(result.shops).toEqual([]);
    expect(result.boundShopId).toBeNull();
  });

  it("reads the empty string the server writes for 'no shop' as no shop", async () => {
    mockPulseApi.mockResolvedValue({ data: { shops: [], external_shop_id: "   " } });
    expect((await listConnectionShops(SCOPE, "c1")).boundShopId).toBeNull();
  });

  it("drops a shop with no id, because nothing could be bound to it", async () => {
    mockPulseApi.mockResolvedValue({
      data: { shops: [{ name: "Nameless", fulfillable: true }, { shop_id: "s1", fulfillable: true }] }
    });
    expect((await listConnectionShops(SCOPE, "c1")).shops.map((s) => s.externalShopId)).toEqual(["s1"]);
  });

  it("survives a server that sends no shops key at all", async () => {
    mockPulseApi.mockResolvedValue({});
    const result = await listConnectionShops(SCOPE, "c1");
    expect(result).toEqual({ shops: [], boundShopId: null });
  });

  it("sends the chosen shop to the bind endpoint", async () => {
    mockPulseApi.mockResolvedValue({ data: {} });
    await bindConnectionShop(SCOPE, "c1", "s1");

    const [url, options] = mockPulseApi.mock.calls[0];
    expect(url).toBe("/api/business-os/suppliers/cj/connections/c1/bind-shop");
    expect(options.method).toBe("POST");
    expect(bodyOf(0)).toEqual({ business_id: "biz-1", store_id: 42, external_shop_id: "s1" });
  });

  it("lets the server's refusal through rather than reporting a bind that did not happen", async () => {
    mockPulseApi.mockRejectedValue(new PulseApiError("no", 403, "shop_not_authorized"));
    await expect(bindConnectionShop(SCOPE, "c1", "s1")).rejects.toBeInstanceOf(PulseApiError);
  });
});

/* ------------------------------------------------------------------ *
 * 5. Partial success is reported per item
 * ------------------------------------------------------------------ */

describe("bulk import reports each item, not one verdict", () => {
  it("keeps every outcome the server sent", async () => {
    mockPulseApi.mockResolvedValue({
      requested: 3,
      imported: 1,
      results: [
        { item_id: "i1", outcome: "IMPORTED", listing_id: 5 },
        { item_id: "i2", outcome: "ALREADY_EXISTS", listing_id: 4 },
        { item_id: "i3", outcome: "PROVIDER_UNAVAILABLE" }
      ]
    });
    const run = await importSelected(SCOPE, "c1", { itemIds: ["i1", "i2", "i3"] });
    expect(run.results.map((r) => r.outcome)).toEqual([
      "IMPORTED",
      "ALREADY_EXISTS",
      "PROVIDER_UNAVAILABLE"
    ]);
    expect(run.imported).toBe(1);
    expect(run.requested).toBe(3);
  });

  it("flags a run that needs the merchant to look at it", async () => {
    mockPulseApi.mockResolvedValue({
      requested: 2,
      imported: 1,
      results: [{ item_id: "i1", outcome: "IMPORTED" }, { item_id: "i2", outcome: "NO_MEDIA" }]
    });
    expect(importNeedsReview(await importSelected(SCOPE, "c1", { itemIds: ["i1", "i2"] }))).toBe(true);
  });

  it("does not flag a clean run", async () => {
    mockPulseApi.mockResolvedValue({
      requested: 1,
      imported: 1,
      results: [{ item_id: "i1", outcome: "IMPORTED" }]
    });
    expect(importNeedsReview(await importSelected(SCOPE, "c1", { itemIds: ["i1"] }))).toBe(false);
  });

  it("treats a re-import of an existing product as not a failure", async () => {
    // Idempotency is the point: running the same import twice must not read as
    // a broken second run.
    mockPulseApi.mockResolvedValue({
      requested: 1,
      imported: 0,
      results: [{ item_id: "i1", outcome: "ALREADY_EXISTS", listing_id: 9 }]
    });
    expect(importNeedsReview(await importSelected(SCOPE, "c1", { itemIds: ["i1"] }))).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * 6. Layers stay distinct
 * ------------------------------------------------------------------ */

describe("the cart is its own layer", () => {
  it("reads cart lines that carry no listing id, because none exists yet", async () => {
    mockPulseApi.mockResolvedValue({
      items: [{ item_id: "i1", external_product_id: "p1", cached: { title: "T" }, cached_stale: true }],
      count: 1,
      stale_count: 1
    });
    const cart = await getImportCart(SCOPE, "c1");
    expect(cart.items[0]).not.toHaveProperty("listingId");
    expect(cart.items[0].stale).toBe(true);
    expect(cart.staleCount).toBe(1);
  });

  it("renders a cart line with no cached preview as null, not as a blank product", async () => {
    mockPulseApi.mockResolvedValue({ items: [{ item_id: "i1", external_product_id: "p1" }], count: 1 });
    const cart = await getImportCart(SCOPE, "c1");
    expect(cart.items[0].preview).toBeNull();
  });
});

describe("connections are values, not a CJ shape", () => {
  it("carries whatever provider the server named", async () => {
    mockPulseApi.mockResolvedValue({
      connections: [{ id: "c1", provider: "PRINTFUL", status: "CONNECTED", environment: "SANDBOX" }]
    });
    const rows = await listSupplierConnections(SCOPE);
    expect(rows[0].provider).toBe("printful");
  });

  it("defaults production fulfilment to off unless the server said otherwise", async () => {
    // The funding kill-switch is closed. Anything other than an explicit true
    // must read as off, including a missing key.
    mockPulseApi.mockResolvedValue({ connections: [{ id: "c1", provider: "cj", status: "CONNECTED" }] });
    const rows = await listSupplierConnections(SCOPE);
    expect(rows[0].productionFulfillmentEnabled).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * 7. Closed unions and the gap ledger
 * ------------------------------------------------------------------ */

describe("the vocabularies are closed and complete", () => {
  it("covers every import outcome the server can send", () => {
    expect([...IMPORT_OUTCOMES]).toEqual([
      "IMPORTED",
      "ALREADY_EXISTS",
      "PROVIDER_UNAVAILABLE",
      "INVALID_PRODUCT",
      "NO_VARIANTS",
      "NO_MEDIA",
      "RESTRICTED",
      "NEEDS_REVIEW"
    ]);
  });

  // This used to be `expect(PUBLISH_PROBLEMS).toHaveLength(10)` under the title
  // "covers every publish problem". A count cannot make that claim: the list was
  // ten long and three codes short, so the assertion passed for the whole time
  // the defect existed — and then failed on the fix, reporting the repair as the
  // regression. The claim it was reaching for spans Python and TypeScript and
  // cannot be made from inside this file at all; it lives in
  // `tests/dropshipping/test_publish_problem_copy.py`, which reads the codes
  // `drafts._validate` can actually emit and checks this list names each one.
  //
  // What is left here are the two things this file can honestly measure.
  it("names each publish problem once, so no entry is dead", () => {
    // A duplicate is not cosmetic: `PROBLEM_COPY` is keyed by the union, so the
    // second entry can never be reached and will be maintained anyway.
    expect([...new Set(PUBLISH_PROBLEMS)]).toHaveLength(PUBLISH_PROBLEMS.length);
  });

  it("passes a problem it has never heard of through rather than swallowing it", async () => {
    // The real "none is silently swallowed" claim, and the reason the screen
    // keeps a runtime fallback beside a total `Record`: a server ahead of this
    // build can name a code the union does not have. Dropping it here would
    // leave the merchant a refusal with an empty reason list, which reads as a
    // bug in the button rather than as something to fix about the product.
    mockPulseApi.mockResolvedValue({
      listing_id: 1,
      validation: { publishable: false, problems: ["A_CODE_FROM_A_NEWER_SERVER"] }
    });
    const draft = await getImportedProduct(SCOPE, "c1", 1);
    expect(draft.validation.problems).toEqual(["A_CODE_FROM_A_NEWER_SERVER"]);
  });

  it("counts the surfaces this app has no data for", () => {
    // Two. If someone fakes supplier orders, this number changes and the change
    // is reviewed rather than shipped quietly.
    expect(DROPSHIPPING_DATA_GAPS).toHaveLength(2);
    expect(DROPSHIPPING_DATA_GAPS.map((gap) => gap.surface)).toEqual([
      "Supplier orders list",
      "Shipment tracking"
    ]);
  });
});

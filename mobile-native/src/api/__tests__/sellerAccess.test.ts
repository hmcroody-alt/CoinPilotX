/**
 * The one seller verdict, pinned.
 *
 * Four production accounts holding a *draft* merchant application could open a
 * full Store dashboard, because every seller surface decided for itself what
 * "can sell" meant from whatever payload it happened to have loaded. The fix is
 * a single server answer and this module, which is the only thing that reads
 * it. These tests guard the three properties that make it a gate rather than a
 * suggestion:
 *
 *   1. It fails closed. The pre-answer default is denied, an unknown status is
 *      denied, and a status this build does not recognise cannot unlock
 *      anything by arriving with helpful booleans beside it.
 *   2. Access is *derived*, never trusted from the wire. A payload asserting
 *      `store_access: true` under a DRAFT status is the exact shape of the
 *      original bug.
 *   3. The two axes stay apart. Card-payment status never moves surface access,
 *      and surface access never claims card readiness.
 */

const mockPulseApi = jest.fn();
const mockReadJsonCache = jest.fn();
const mockWriteJsonCache = jest.fn();

jest.mock("../pulseApi", () => {
  const actual = jest.requireActual("../pulseApi");
  return { ...actual, pulseApi: (...args: unknown[]) => mockPulseApi(...args) };
});

jest.mock("../../core/cache", () => ({
  readJsonCache: (...args: unknown[]) => mockReadJsonCache(...args),
  writeJsonCache: (...args: unknown[]) => mockWriteJsonCache(...args)
}));

import {
  DENIED_SELLER_ACCESS,
  canManageExistingOrders,
  canOpenStore,
  canSellOnMarketplace,
  fetchSellerAccessState,
  isSellerAccessUnsupported,
  loadCachedSellerAccessState,
  parseSellerAccessState,
  sellerAccessDestination,
  type SellerAccessDestination,
  type SellerApplicationAccess
} from "../sellerAccess";

const ALL_APPLICATION_STATUSES: readonly SellerApplicationAccess[] = [
  "NO_APPLICATION",
  "DRAFT",
  "SUBMITTED",
  "UNDER_REVIEW",
  "MORE_INFORMATION_REQUIRED",
  "APPROVED",
  "DECLINED",
  "SUSPENDED"
];

beforeEach(() => {
  mockPulseApi.mockReset();
  mockReadJsonCache.mockReset();
  mockWriteJsonCache.mockReset();
  mockWriteJsonCache.mockResolvedValue(undefined);
});

/* ------------------------------------------------------------------ *
 * Failing closed
 * ------------------------------------------------------------------ */

describe("the default before the server answers", () => {
  it("denies every seller surface", () => {
    expect(canOpenStore(DENIED_SELLER_ACCESS)).toBe(false);
    expect(canSellOnMarketplace(DENIED_SELLER_ACCESS)).toBe(false);
    expect(canManageExistingOrders(DENIED_SELLER_ACCESS)).toBe(false);
    expect(DENIED_SELLER_ACCESS.seller_approved).toBe(false);
  });

  it("denies for a missing state as well as a denying one", () => {
    // A screen reaching for access before the hook has produced anything must
    // get "no", not `undefined` coerced into a truthy render path.
    for (const absent of [null, undefined]) {
      expect(canOpenStore(absent)).toBe(false);
      expect(canSellOnMarketplace(absent)).toBe(false);
      expect(canManageExistingOrders(absent)).toBe(false);
    }
  });
});

describe("parseSellerAccessState", () => {
  it("opens the seller surfaces only for APPROVED", () => {
    for (const status of ALL_APPLICATION_STATUSES) {
      const state = parseSellerAccessState({ seller_application_status: status });
      const expected = status === "APPROVED";
      expect(state.seller_approved).toBe(expected);
      expect(canOpenStore(state)).toBe(expected);
      expect(canSellOnMarketplace(state)).toBe(expected);
    }
  });

  it("re-derives access instead of believing the wire", () => {
    // This payload is the original bug written down: a draft application
    // arriving with the booleans of an approved one. Trusting them is how a
    // half-finished application opened a full Store dashboard.
    const state = parseSellerAccessState({
      seller_application_status: "DRAFT",
      seller_approved: true,
      store_access: true,
      marketplace_selling_access: true
    });
    expect(state.seller_approved).toBe(false);
    expect(canOpenStore(state)).toBe(false);
    expect(canSellOnMarketplace(state)).toBe(false);
  });

  it("reads a status this build does not know as no application", () => {
    // A server that grows a ninth status must not unlock anything on an old
    // client by being unrecognised.
    const state = parseSellerAccessState({ seller_application_status: "PLATINUM_SELLER" });
    expect(state.seller_application_status).toBe("NO_APPLICATION");
    expect(canOpenStore(state)).toBe(false);
  });

  it("accepts the server's casing and whitespace, and nothing else", () => {
    expect(parseSellerAccessState({ seller_application_status: " approved " }).seller_approved).toBe(
      true
    );
    expect(parseSellerAccessState({ seller_application_status: 7 }).seller_application_status).toBe(
      "NO_APPLICATION"
    );
    expect(parseSellerAccessState(null).seller_application_status).toBe("NO_APPLICATION");
  });

  it("keeps a suspended seller's obligations to their buyers", () => {
    // Fulfilment, refunds and dispute responses are owed to people who already
    // paid. Suspension removes privileges, not debts.
    const suspended = parseSellerAccessState({ seller_application_status: "SUSPENDED" });
    expect(canOpenStore(suspended)).toBe(false);
    expect(canSellOnMarketplace(suspended)).toBe(false);
    expect(canManageExistingOrders(suspended)).toBe(true);
  });

  it("gives order access to nobody else who is blocked", () => {
    for (const status of ["NO_APPLICATION", "DRAFT", "SUBMITTED", "DECLINED"] as const) {
      expect(canManageExistingOrders(parseSellerAccessState({ seller_application_status: status })))
        .toBe(false);
    }
  });
});

/* ------------------------------------------------------------------ *
 * The two axes
 * ------------------------------------------------------------------ */

describe("seller approval and card payments are separate axes", () => {
  it("lets an approved seller with no Stripe account keep their store", () => {
    // This is the whole point of not merging the two. Collapsing them would
    // turn an unfinished payment setup into a locked storefront.
    const state = parseSellerAccessState({
      seller_application_status: "APPROVED",
      card_payment_status: "SETUP_REQUIRED"
    });
    expect(canOpenStore(state)).toBe(true);
    expect(canSellOnMarketplace(state)).toBe(true);
    expect(state.card_payment_status).toBe("SETUP_REQUIRED");
  });

  it("does not let a READY card status open a surface approval has not", () => {
    const state = parseSellerAccessState({
      seller_application_status: "DRAFT",
      card_payment_status: "READY"
    });
    expect(canOpenStore(state)).toBe(false);
    expect(state.card_payment_status).toBe("READY");
  });

  it("falls back to the legacy stripe field, and to setup-required beyond that", () => {
    expect(parseSellerAccessState({ stripe_connect_status: "RESTRICTED" }).card_payment_status).toBe(
      "RESTRICTED"
    );
    expect(parseSellerAccessState({ card_payment_status: "MYSTERY" }).card_payment_status).toBe(
      "SETUP_REQUIRED"
    );
    // The two names are one value; a screen reading either must see the same
    // thing, or the Store and the Payments hub can disagree about one account.
    const state = parseSellerAccessState({ card_payment_status: "UNDER_REVIEW" });
    expect(state.stripe_connect_status).toBe(state.card_payment_status);
  });

  it("marks only the states a seller can act on as actionable", () => {
    for (const card of ["SETUP_REQUIRED", "SETUP_IN_PROGRESS", "ACTION_REQUIRED"] as const) {
      expect(parseSellerAccessState({ card_payment_status: card }).card_setup_actionable).toBe(true);
    }
    for (const card of ["UNDER_REVIEW", "READY", "RESTRICTED", "UNAVAILABLE"] as const) {
      expect(parseSellerAccessState({ card_payment_status: card }).card_setup_actionable).toBe(
        false
      );
    }
  });
});

/* ------------------------------------------------------------------ *
 * Where a blocked seller is sent
 * ------------------------------------------------------------------ */

describe("sellerAccessDestination", () => {
  it("names a destination for every status", () => {
    // The mapping being total is what stops a new status from silently
    // rendering "Apply to sell" at a seller who already has a store.
    for (const status of ALL_APPLICATION_STATUSES) {
      const destination = sellerAccessDestination(
        parseSellerAccessState({ seller_application_status: status })
      );
      expect(typeof destination).toBe("string");
      expect(destination.length).toBeGreaterThan(0);
    }
  });

  it("routes each blocked status to the screen that can unblock it", () => {
    const expected: Record<SellerApplicationAccess, SellerAccessDestination> = {
      NO_APPLICATION: "SELLER_APPLICATION",
      DRAFT: "RESUME_APPLICATION",
      SUBMITTED: "APPLICATION_STATUS",
      UNDER_REVIEW: "APPLICATION_STATUS",
      MORE_INFORMATION_REQUIRED: "COMPLETE_REQUESTED_CHANGES",
      APPROVED: "SELLER_TOOLS",
      DECLINED: "APPLICATION_STATUS",
      SUSPENDED: "RESTRICTED"
    };
    for (const status of ALL_APPLICATION_STATUSES) {
      expect(
        sellerAccessDestination(parseSellerAccessState({ seller_application_status: status }))
      ).toBe(expected[status]);
    }
  });

  it("never offers a suspended seller the application form", () => {
    // "Apply to sell" shown to someone who has been suspended reads as though
    // the platform has forgotten them, and the form cannot lift a suspension.
    const suspended = parseSellerAccessState({ seller_application_status: "SUSPENDED" });
    expect(sellerAccessDestination(suspended)).not.toBe("SELLER_APPLICATION");
  });

  it("sends an absent state to the application rather than to the tools", () => {
    expect(sellerAccessDestination(null)).toBe("SELLER_APPLICATION");
    expect(sellerAccessDestination(undefined)).toBe("SELLER_APPLICATION");
  });
});

/* ------------------------------------------------------------------ *
 * Transport and cache
 * ------------------------------------------------------------------ */

describe("fetchSellerAccessState", () => {
  it("reads the one endpoint and caches the derived answer", async () => {
    mockPulseApi.mockResolvedValue({
      ok: true,
      seller_access: { seller_application_status: "APPROVED", card_payment_status: "READY" }
    });
    const state = await fetchSellerAccessState();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/seller/access-state", { method: "GET" });
    expect(canOpenStore(state)).toBe(true);
    // What is cached is the derived state, not the raw payload, so a later read
    // cannot reach booleans that were never trusted in the first place.
    const [, cached] = mockWriteJsonCache.mock.calls[0];
    expect(cached.store_access).toBe(true);
  });

  it("does not let a cache write failure lose the answer", async () => {
    mockPulseApi.mockResolvedValue({ seller_access: { seller_application_status: "APPROVED" } });
    mockWriteJsonCache.mockRejectedValue(new Error("disk full"));
    await expect(fetchSellerAccessState()).resolves.toMatchObject({ seller_approved: true });
  });

  it("lets a transport failure through so the caller can show a retry", async () => {
    // Swallowing it here would be indistinguishable from a denial, and telling
    // an approved seller they have no store because the network hiccuped is a
    // worse lie than an honest "we couldn't check".
    mockPulseApi.mockRejectedValue(new Error("offline"));
    await expect(fetchSellerAccessState()).rejects.toThrow("offline");
  });
});

describe("the cached verdict and the account it belongs to", () => {
  it("does not survive a sign-out", async () => {
    // Account A's "approved" painted onto account B's cold launch is a seller
    // dashboard opened for someone who has never applied — the original bug,
    // arrived at from the other direction. The sweep in `core/storageScope` is
    // an inverted list (everything under the namespace goes unless it is named
    // as the handset's), so this holds by default — but "by default" is worth
    // one assertion, because the cost of a future keep-list entry matching this
    // prefix is a privacy leak that nothing reports.
    mockPulseApi.mockResolvedValue({ seller_access: { seller_application_status: "APPROVED" } });
    await fetchSellerAccessState();
    const [key] = mockWriteJsonCache.mock.calls[0];

    const { accountScopedKeys, isDeviceScopedKey } = jest.requireActual("../../core/storageScope");
    expect(isDeviceScopedKey(key)).toBe(false);
    expect(accountScopedKeys([key])).toEqual([key]);
  });
});

describe("loadCachedSellerAccessState", () => {
  it("re-derives a cached payload under today's rules", async () => {
    // The normalizer is handed to the cache reader rather than applied after,
    // so a payload written by an older build — with a status this build no
    // longer honours, or booleans it no longer trusts — is re-judged on the way
    // out instead of being read back as whatever was stored beside it.
    mockReadJsonCache.mockImplementation((_key: string, normalize: (v: unknown) => unknown) =>
      Promise.resolve(normalize({ seller_application_status: "LEGACY_TIER", store_access: true }))
    );
    const state = await loadCachedSellerAccessState();
    expect(state?.seller_application_status).toBe("NO_APPLICATION");
    expect(canOpenStore(state)).toBe(false);
  });

  it("answers null rather than throwing when the cache cannot be read", async () => {
    mockReadJsonCache.mockRejectedValue(new Error("unreadable"));
    await expect(loadCachedSellerAccessState()).resolves.toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * "There is no gate here" vs "the gate could not be read"
 * ------------------------------------------------------------------ */

describe("isSellerAccessUnsupported", () => {
  // The real class, not a stand-in. The mock at the top of this file spreads
  // `requireActual`, so this is the same constructor `pulseApi` throws with —
  // and a predicate that reads `error.status` is only worth as much as the
  // guarantee that the status on the wire arrives intact.
  const { PulseApiError } = jest.requireActual("../pulseApi");

  it("recognises a 404 as a server that predates the route", () => {
    // Observed in production: the app build shipped ahead of the backend, the
    // route 404'd on every focus, and the hook reported a failed *check*. That
    // put "We couldn't check your seller status" in front of every seller with
    // a retry that could never succeed. A 404 on this path is a provisioning
    // fact, not a verdict, and not a failure.
    expect(isSellerAccessUnsupported(new PulseApiError("Not Found", 404, "not_found"))).toBe(true);
  });

  it("does not mistake a real failure for a missing route", () => {
    // Each of these means the gate exists and could not be read. Falling open
    // on any of them would turn a network blip into an unguarded store — the
    // whole reason the predicate is a status equality and not a status range.
    for (const status of [400, 401, 403, 408, 429, 500, 502, 503, 504]) {
      expect(isSellerAccessUnsupported(new PulseApiError("nope", status))).toBe(false);
    }
  });

  it("does not fall open on an unreachable server", () => {
    // `pulseApi` reports a dead connection as 503 `request_unreachable` and a
    // blown budget as 504 `request_timeout`. Offline is the single most common
    // way this call fails, so it is the single most important thing that must
    // not read as "this deployment has no seller gate".
    expect(isSellerAccessUnsupported(new PulseApiError("unreachable", 503, "request_unreachable"))).toBe(false);
    expect(isSellerAccessUnsupported(new PulseApiError("slow", 504, "request_timeout"))).toBe(false);
  });

  it("does not fall open on an error that never came from the API", () => {
    // A thrown `TypeError`, a rejected promise carrying a string, a null — none
    // of these carry a status, and a duck-typed check would read `undefined`
    // and could be spoofed by any object with the right shape.
    expect(isSellerAccessUnsupported(new TypeError("Network request failed"))).toBe(false);
    expect(isSellerAccessUnsupported({ status: 404 })).toBe(false);
    expect(isSellerAccessUnsupported("404")).toBe(false);
    expect(isSellerAccessUnsupported(null)).toBe(false);
    expect(isSellerAccessUnsupported(undefined)).toBe(false);
  });

  it("classifies the error fetchSellerAccessState actually throws", () => {
    // The predicate and the thrower, joined. `fetchSellerAccessState` does not
    // catch, so whatever `pulseApi` raises reaches the hook unchanged — and it
    // is the hook's classification of that object that decides whether a
    // seller sees their store.
    const raised = new PulseApiError("Not Found", 404, "not_found");
    mockPulseApi.mockRejectedValue(raised);
    return fetchSellerAccessState().then(
      () => {
        throw new Error("expected a rejection");
      },
      (error) => {
        expect(isSellerAccessUnsupported(error)).toBe(true);
        // And nothing was cached: there is no verdict to remember.
        expect(mockWriteJsonCache).not.toHaveBeenCalled();
      }
    );
  });
});

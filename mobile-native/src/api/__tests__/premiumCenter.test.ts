/**
 * The client is not an entitlement authority.
 *
 * Every test below is a variation on one rule: membership arrives decided from
 * `/api/premium/status-center`, and nothing in the app may re-derive it, widen
 * it, or remember it as fact. The two failures worth spending a test file on
 * are granting access nobody paid for, and telling a paying member they are
 * free — so the matrix leans on malformed payloads and on the cold-start moment
 * where the truthful answer is "no answer yet".
 */

const mockPulseApi = jest.fn();
const mockReadJsonCache = jest.fn();
const mockWriteJsonCache = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("../../core/cache", () => ({
  readJsonCache: (...args: unknown[]) => mockReadJsonCache(...args),
  writeJsonCache: (...args: unknown[]) => mockWriteJsonCache(...args)
}));

import {
  PREMIUM_CACHE_CONTRACT,
  getPremiumCenter,
  loadCachedPremiumCenter,
  normalizePremiumCenter,
  premiumExperience,
  premiumTileState,
  type PremiumCenter
} from "../premiumCenter";

function center(overrides: Partial<PremiumCenter> = {}): PremiumCenter {
  return normalizePremiumCenter({
    ok: true,
    membership: {
      is_premium: true,
      usable_now: true,
      mode: "active",
      decided_by: "canonical",
      on_hold: false,
      account_status: "active",
      reason: "ACTIVE_SUBSCRIPTION",
      lifetime: false
    },
    ...overrides
  });
}

beforeEach(() => {
  mockPulseApi.mockReset();
  mockReadJsonCache.mockReset();
  mockWriteJsonCache.mockReset();
});

describe("normalizePremiumCenter", () => {
  it("reads an empty payload as not premium", () => {
    const payload = normalizePremiumCenter(undefined);
    expect(payload.membership.is_premium).toBe(false);
    expect(payload.membership.mode).toBe("none");
    expect(payload.founder.is_founder).toBe(false);
    expect(payload.subscription).toBeNull();
    expect(payload.benefits).toEqual([]);
  });

  it("does not let a truthy-looking string forge membership", () => {
    // A proxy, a stale cache or a tampered response can put anything in these
    // fields. Coercion is deliberate and one-directional: only a real boolean
    // `true` from the server reads as premium.
    const payload = normalizePremiumCenter({
      membership: { is_premium: "yes", usable_now: 1, mode: "active" }
    } as never);
    expect(payload.membership.is_premium).toBe(true);
    // `mode` is carried through for display, but `premiumExperience` is what
    // decides the layout and it reads `is_premium`, not a free-text mode.
    expect(payload.membership.mode).toBe("active");
  });

  it("drops a non-array benefit list rather than rendering it", () => {
    const payload = normalizePremiumCenter({ benefits: "everything" } as never);
    expect(payload.benefits).toEqual([]);
    expect(payload.not_yet).toEqual([]);
    expect(payload.notices).toEqual([]);
  });

  it("keeps only the safe billing columns the server sent", () => {
    const payload = normalizePremiumCenter({
      subscription: {
        provider: "apple_iap",
        plan_key: "premium_annual",
        billing_period: "annual",
        status: "active",
        current_period_end: "2027-01-01T00:00:00Z",
        cancel_at_period_end: false,
        // Fields the server does not send. If one ever appeared, it must not
        // survive normalization into a screen.
        provider_subscription_id: "1000000999888777",
        raw_json: "{...}"
      }
    } as never);
    expect(Object.keys(payload.subscription || {}).sort()).toEqual([
      "auto_renew",
      "billing_period",
      "cancel_at_period_end",
      "current_period_end",
      "expires_at",
      "original_purchase_at",
      "plan_key",
      "product_id",
      "provider",
      "renews_at",
      "state",
      "status"
    ]);
  });

  it("treats an unrecognised subscription state as unknown, never as active", () => {
    // A payload this build does not understand must produce a cautious card. The
    // failure that costs a member nothing is a screen that says "unavailable";
    // the failure that costs the company money is one that asserts a membership.
    const payload = normalizePremiumCenter({
      subscription: { provider: "apple_app_store", state: "something_new" }
    } as never);
    expect(payload.subscription?.state).toBe("unknown");
  });

  it("derives auto-renew from the cancel flag when an older server omits it", () => {
    const payload = normalizePremiumCenter({
      subscription: { provider: "apple_app_store", state: "canceled", cancel_at_period_end: true }
    } as never);
    expect(payload.subscription?.auto_renew).toBe(false);
  });

  it("never sets both a renewal date and an end date", () => {
    // The same instant means "you will be charged" or "your access stops". A
    // payload asserting both would let the card render the wrong verb next to
    // the right date, which is the most damaging thing it could say.
    const renewing = normalizePremiumCenter({
      subscription: {
        provider: "apple_app_store", state: "active", cancel_at_period_end: false,
        current_period_end: "2099-01-01T00:00:00Z",
        renews_at: "2099-01-01T00:00:00Z", expires_at: "2099-01-01T00:00:00Z"
      }
    } as never);
    expect(renewing.subscription?.renews_at).toBe("2099-01-01T00:00:00Z");
    expect(renewing.subscription?.expires_at).toBeNull();

    const ending = normalizePremiumCenter({
      subscription: {
        provider: "apple_app_store", state: "canceled", cancel_at_period_end: true,
        current_period_end: "2099-01-01T00:00:00Z",
        renews_at: "2099-01-01T00:00:00Z", expires_at: "2099-01-01T00:00:00Z"
      }
    } as never);
    expect(ending.subscription?.renews_at).toBeNull();
    expect(ending.subscription?.expires_at).toBe("2099-01-01T00:00:00Z");
  });

  it("reports an expired subscription as ending, never as renewing", () => {
    const payload = normalizePremiumCenter({
      subscription: {
        provider: "apple_app_store", state: "expired", cancel_at_period_end: false,
        current_period_end: "2020-01-01T00:00:00Z"
      }
    } as never);
    expect(payload.subscription?.renews_at).toBeNull();
    expect(payload.subscription?.expires_at).toBe("2020-01-01T00:00:00Z");
  });

  it("keeps a missing original purchase date absent rather than inventing one", () => {
    const payload = normalizePremiumCenter({
      subscription: { provider: "apple_app_store", state: "active" }
    } as never);
    expect(payload.subscription?.original_purchase_at).toBeNull();
    expect(payload.subscription?.product_id).toBeNull();
  });
});

describe("premiumExperience", () => {
  it("has no answer before the first read", () => {
    expect(premiumExperience(null)).toBe("none");
  });

  it("keeps a founder a founder even with a paid subscription attached", () => {
    const payload = center({
      founder: { is_founder: true, founder_number: 42, price_cents: 4999 },
      subscription: {
        provider: "apple_iap",
        plan_key: "premium_monthly",
        billing_period: "monthly",
        status: "active",
        current_period_end: null,
        cancel_at_period_end: false
      }
    } as never);
    // A founder shown a plain "Active" screen would read as having lost the
    // status, and converting one to standard premium is forbidden outright.
    expect(premiumExperience(payload)).toBe("founder");
  });

  it("treats a grandfathered mode as founder even without the founder flag", () => {
    expect(premiumExperience(center({ membership: { mode: "grandfathered" } } as never))).toBe("founder");
  });

  it("reports an account hold ahead of active", () => {
    const payload = center({
      membership: { is_premium: true, usable_now: false, mode: "active", on_hold: true }
    } as never);
    expect(premiumExperience(payload)).toBe("hold");
  });

  it("separates a lapsed member from someone who never subscribed", () => {
    const lapsed = center({
      membership: { is_premium: false, mode: "inactive" },
      subscription: {
        provider: "apple_iap",
        plan_key: "premium_annual",
        billing_period: "annual",
        status: "expired",
        current_period_end: "2025-01-01T00:00:00Z",
        cancel_at_period_end: true
      }
    } as never);
    expect(premiumExperience(lapsed)).toBe("expired");
    expect(premiumExperience(center({ membership: { is_premium: false, mode: "none" } } as never))).toBe("none");
  });

  it("keeps grace a distinct state from active", () => {
    // Access is still on, but the screen must say the renewal is being retried
    // rather than pretend nothing is happening.
    expect(premiumExperience(center({ membership: { is_premium: true, mode: "grace" } } as never))).toBe("grace");
  });

  it("reads a lifetime membership as lifetime even with a dead subscription row behind it", () => {
    // The whole reason `lifetime` is a server fact rather than something the
    // screen deduces. This payload is byte-for-byte the "lapsed member" fixture
    // above except that the server says the membership is permanent — and the
    // subscription row is the same expired, cancel-at-period-end apple_iap row
    // that makes the lapsed case read "expired". If the client inferred instead
    // of being told, these two would be indistinguishable and the owner would be
    // shown a Renew button for a membership that cannot end.
    const permanent = center({
      membership: { is_premium: true, mode: "owner_lifetime", reason: "OWNER_LIFETIME", lifetime: true },
      subscription: {
        provider: "apple_iap",
        plan_key: "premium_annual",
        billing_period: "annual",
        status: "expired",
        current_period_end: "2025-01-01T00:00:00Z",
        cancel_at_period_end: true
      }
    } as never);
    expect(premiumExperience(permanent)).toBe("lifetime");
    expect(premiumExperience(permanent)).not.toBe("expired");
  });

  it("lets an account hold and a founder number still outrank lifetime", () => {
    // Lifetime is a billing fact, not a security verdict: a hold must still be
    // able to say so. And a founder who is also permanent keeps the founder
    // layout, because the founder number is the rarer thing to show.
    const held = center({
      membership: { is_premium: true, usable_now: false, mode: "owner_lifetime", lifetime: true, on_hold: true }
    } as never);
    expect(premiumExperience(held)).toBe("hold");

    const founder = center({
      membership: { is_premium: true, mode: "owner_lifetime", lifetime: true },
      founder: { is_founder: true, founder_number: 1, price_cents: 4999 }
    } as never);
    expect(premiumExperience(founder)).toBe("founder");
  });

  it("never reports lifetime for a membership the server did not mark permanent", () => {
    // `lifetime` alone is not enough — it is paired with `is_premium` so a
    // stale or malformed flag cannot light up the permanent layout for someone
    // who has no access at all.
    expect(premiumExperience(center({
      membership: { is_premium: false, mode: "inactive", lifetime: true }
    } as never))).not.toBe("lifetime");
    expect(premiumExperience(center({
      membership: { is_premium: true, mode: "active", lifetime: false }
    } as never))).toBe("active");
  });
});

describe("premiumTileState", () => {
  it("says nothing at all before the first answer", () => {
    // The flicker rule: absence of a badge asserts nothing, while the word
    // "Free" would assert something wrong about someone who paid.
    expect(premiumTileState(null)).toBeNull();
  });

  it.each([
    ["none", { membership: { is_premium: false, mode: "none" } }],
    ["expired", { membership: { is_premium: false, mode: "inactive" }, subscription: { provider: "apple_iap" } }],
    ["hold", { membership: { is_premium: true, mode: "active", on_hold: true } }]
  ])("shows no micro-status for %s", (_label, overrides) => {
    expect(premiumTileState(center(overrides as never))).toBeNull();
  });

  it("labels the three states that are safe to assert", () => {
    expect(premiumTileState(center())).toBe("active");
    expect(premiumTileState(center({ membership: { is_premium: true, mode: "grace" } } as never))).toBe("grace");
    expect(premiumTileState(center({ founder: { is_founder: true, founder_number: 1 } } as never))).toBe("founder");
  });

  it("gives a lifetime member the ordinary active badge", () => {
    // The tile is a micro-status, not a place to advertise permanence. What
    // matters here is only that it is not null and not a lapsed state: a
    // permanent member must never see the tile go quiet the way an expired one
    // does.
    expect(premiumTileState(center({
      membership: { is_premium: true, mode: "owner_lifetime", lifetime: true }
    } as never))).toBe("active");
  });
});

describe("transport", () => {
  it("reads the canonical Status Center endpoint and nothing else", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, membership: { is_premium: true, mode: "active" } });
    await getPremiumCenter();
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/premium/status-center");
  });

  it("caches the live answer for the tile", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, membership: { is_premium: true, mode: "active" } });
    const payload = await getPremiumCenter();
    expect(mockWriteJsonCache).toHaveBeenCalledWith("pulsesoc.native.premium.center", payload);
  });

  it("still returns the live answer when the cache write fails", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, membership: { is_premium: true, mode: "active" } });
    mockWriteJsonCache.mockRejectedValue(new Error("disk full"));
    await expect(getPremiumCenter()).resolves.toMatchObject({ membership: { is_premium: true } });
  });

  it.each([401, 500])("preserves a %s membership transport failure for retry UX", async (status) => {
    const failure = Object.assign(new Error("membership request failed"), { status });
    mockPulseApi.mockRejectedValue(failure);
    await expect(getPremiumCenter()).rejects.toBe(failure);
  });

  it("preserves an offline membership failure for retry UX", async () => {
    const failure = new TypeError("Network request failed");
    mockPulseApi.mockRejectedValue(failure);
    await expect(getPremiumCenter()).rejects.toBe(failure);
  });

  it("normalizes whatever the cache returns before anyone reads it", async () => {
    mockReadJsonCache.mockImplementation(async (_key: string, normalize: (value: unknown) => unknown) =>
      normalize({ membership: { mode: "active" } })
    );
    const cached = await loadCachedPremiumCenter();
    // Cached "mode: active" with no `is_premium` is not membership. A cache
    // written by an older build must not read as a grant.
    expect(cached?.membership.is_premium).toBe(false);
  });

  it("declares the cache display-only", () => {
    // Asserted so that a future change gating a purchase, a restore or a
    // capability on cached state has to delete this line first.
    expect(PREMIUM_CACHE_CONTRACT).toBe("display-only");
  });
});

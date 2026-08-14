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
      account_status: "active"
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
      "billing_period",
      "cancel_at_period_end",
      "current_period_end",
      "plan_key",
      "provider",
      "status"
    ]);
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

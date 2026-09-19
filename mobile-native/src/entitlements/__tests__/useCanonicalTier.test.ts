/**
 * The shared entitlement cache, pinned at the moment it is reset.
 *
 * Every premium surface in the app reads this one cache — `PremiumFeatureGate`
 * wraps all of them — so an answer written here is not one screen's opinion,
 * it is the whole product's. That makes the interesting cases the ones where a
 * reply arrives for a question that is no longer being asked: a resolve issued
 * before a purchase, before a sign-out, or on behalf of an account that is no
 * longer signed in.
 *
 * Almost every assertion below is about a reply that must be IGNORED. Those are
 * the failures nobody sees coming: the request succeeded, the network was fine,
 * no error was logged, and the member is simply locked out of something they
 * paid for until the next foreground.
 */
import { UNKNOWN_TIER } from "../canonicalTier";
import type { TierAnswer } from "../canonicalTier";
import { loadCanonicalTier, resetCanonicalTier } from "../useCanonicalTier";

jest.mock("../canonicalTier", () => {
  const actual = jest.requireActual("../canonicalTier");
  return { ...actual, fetchCanonicalTier: jest.fn() };
});

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { fetchCanonicalTier } = require("../canonicalTier") as {
  fetchCanonicalTier: jest.Mock<Promise<TierAnswer>, []>;
};

function answer(tier: TierAnswer["effectiveTier"], source = "test"): TierAnswer {
  return {
    state: "resolved",
    effectiveTier: tier,
    status: "active",
    source,
    expiresAt: null,
    features: {},
    verifiedAt: "2026-09-19T00:00:00Z"
  };
}

/** A request whose reply we release by hand, so the race is deterministic. */
function deferred() {
  let release!: (value: TierAnswer) => void;
  const promise = new Promise<TierAnswer>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

beforeEach(() => {
  fetchCanonicalTier.mockReset();
  // Return the module to a known era and a known cache. `resetCanonicalTier`
  // is itself under test, so this is deliberately the only place it is trusted.
  resetCanonicalTier();
});

describe("a reply that arrives after a reset", () => {
  /**
   * THE PURCHASE BUG, reduced to its mechanism.
   *
   * Buying opens the App Store sheet. Returning from it fires the foreground
   * listener every premium gate installs, which issues a resolve the server
   * answers before the receipt is verified — FREE. The purchase screen then
   * resets and re-reads, and gets PREMIUM. Both are in flight at once, and the
   * pre-purchase reply is the older request, so it is the likelier one to land
   * last.
   *
   * If it is allowed to publish, a member who has just paid is written back to
   * FREE across every gate in the app, with nothing to re-ask until the next
   * foreground.
   */
  it("does not let a pre-purchase FREE overwrite the post-purchase PREMIUM", async () => {
    const prePurchase = deferred();
    const postPurchase = deferred();
    fetchCanonicalTier.mockReturnValueOnce(prePurchase.promise).mockReturnValueOnce(postPurchase.promise);

    const stale = loadCanonicalTier(); // foreground resolve, pre-receipt

    resetCanonicalTier(); // purchase completed
    const fresh = loadCanonicalTier();

    postPurchase.release(answer("PREMIUM", "app_store"));
    await fresh;

    prePurchase.release(answer("FREE", "stale"));
    await stale;

    // The cache is read through a fresh load; make it return the held answer.
    fetchCanonicalTier.mockResolvedValueOnce(answer("PREMIUM", "app_store"));
    await expect(loadCanonicalTier()).resolves.toMatchObject({ effectiveTier: "PREMIUM" });

    // And the superseded caller was handed the CURRENT truth, not its own
    // stale reply — a caller that awaits the load must not act on FREE either.
    await expect(stale).resolves.toMatchObject({ effectiveTier: "PREMIUM" });
  });

  /**
   * The same defect pointed the other way, which is the one that leaks access
   * rather than withholding it: a reply computed for the member who just
   * signed out, landing after somebody else has signed in on the same device.
   */
  it("does not hand the previous account's PREMIUM to the next member", async () => {
    const previousAccount = deferred();
    fetchCanonicalTier.mockReturnValueOnce(previousAccount.promise);

    const stale = loadCanonicalTier();
    resetCanonicalTier(); // sign-out

    previousAccount.release(answer("PRIVATE_OFFICE", "previous_member"));
    await stale;

    // Nothing has been asked on behalf of the new member yet, so the honest
    // answer is "we do not know" — never the previous member's tier.
    await expect(stale).resolves.toEqual(UNKNOWN_TIER);
  });

  /**
   * A superseded reply must not drag the NEW request's de-duplication down
   * with it. `loadCanonicalTier` shares one in-flight promise; if the stale
   * reply clears that handle on its way out, the next caller starts a third
   * request beside the one already running, and the two can land out of order
   * all over again.
   */
  it("leaves the newer request's de-duplication intact when it settles", async () => {
    const stale = deferred();
    const current = deferred();
    fetchCanonicalTier.mockReturnValueOnce(stale.promise).mockReturnValueOnce(current.promise);

    const first = loadCanonicalTier();
    resetCanonicalTier();
    const second = loadCanonicalTier();

    stale.release(answer("FREE"));
    await first;

    // Still one live request for the current era, so a joiner must not add another.
    const joiner = loadCanonicalTier();
    expect(fetchCanonicalTier).toHaveBeenCalledTimes(2);

    current.release(answer("PREMIUM"));
    await Promise.all([second, joiner]);
  });
});

describe("the ordinary path still works", () => {
  it("publishes a reply issued in the current era", async () => {
    fetchCanonicalTier.mockResolvedValueOnce(answer("PREMIUM"));
    await expect(loadCanonicalTier()).resolves.toMatchObject({ effectiveTier: "PREMIUM" });
  });

  it("shares one request between concurrent callers", async () => {
    const pending = deferred();
    fetchCanonicalTier.mockReturnValueOnce(pending.promise);

    const a = loadCanonicalTier();
    const b = loadCanonicalTier();
    expect(fetchCanonicalTier).toHaveBeenCalledTimes(1);

    pending.release(answer("PREMIUM"));
    await Promise.all([a, b]);
  });

  it("starts a new request once the previous one has settled", async () => {
    fetchCanonicalTier.mockResolvedValueOnce(answer("FREE")).mockResolvedValueOnce(answer("PREMIUM"));
    await loadCanonicalTier();
    await expect(loadCanonicalTier()).resolves.toMatchObject({ effectiveTier: "PREMIUM" });
    expect(fetchCanonicalTier).toHaveBeenCalledTimes(2);
  });

  /**
   * A reset does not fetch. Sign-out has no session to ask with, and a request
   * fired into a torn-down session fails and renders as "we can't confirm your
   * membership" — an outage message for something that is not an outage.
   */
  it("does not fetch on reset", () => {
    resetCanonicalTier();
    expect(fetchCanonicalTier).not.toHaveBeenCalled();
  });
});

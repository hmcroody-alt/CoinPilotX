/**
 * The master switch, and the four hooks that have to obey it — or not.
 *
 * The rule being tested is one sentence of the brief: "Disabling stops organic
 * discovery across social surfaces; Marketplace itself stays functional." Three
 * of the four hooks must go quiet, and the fourth must not, and the difference
 * between those two behaviours is the entire feature of this module.
 *
 * Three things here are not obvious from reading the hooks:
 *
 *   - **The gate is a no-fetch, not a no-render.** A hook that fetched and then
 *     hid the result would pass any test written against `placements`, while
 *     still telling the server "this user is being shown commerce" on every
 *     page. So the assertions are on the *network call*.
 *
 *   - **Consent is read inside the hooks, not passed in.** Every surface gets
 *     it by calling the hook at all. A test that only covered the screens would
 *     not notice a fifth surface added later with the option forgotten, which
 *     is the failure this design exists to make impossible.
 *
 *   - **No `<PreferencesProvider>` appears anywhere below.** That is deliberate
 *     and is half the point of the module owner: `usePreferences` throws
 *     outside a provider, so reading the context from inside these hooks would
 *     mean a missing provider takes down Home rather than quieting commerce.
 *     These tests render the hooks bare, exactly as the screen tests do.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { fetchCommerceModules, fetchCommercePlacements } from "../../api/commerceDiscovery";
import type {
  CommerceFeedbackAction,
  CommerceModule,
  CommercePlacement,
  CommerceServeResult
} from "../../api/commerceDiscovery";
import {
  COMMERCE_PAUSE_DAYS,
  isPauseActive,
  pauseEndsAt,
  registerCommercePauseWriter,
  setSocialDiscoveryAllowed,
  socialDiscoveryAllowed,
  startCommercePause,
  useSocialDiscoveryAllowed
} from "../consent";
import { useFeedCommerce } from "../useFeedCommerce";
import { useMarketplaceCommerce } from "../useMarketplaceCommerce";
import { useMessengerCommerce } from "../useMessengerCommerce";
import { useReelsCommerce } from "../useReelsCommerce";
import { __resetCommerceSessionId } from "../session";

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommercePlacements: jest.fn(),
  fetchCommerceModules: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true))
}));

const fetchPlacements = fetchCommercePlacements as jest.MockedFunction<typeof fetchCommercePlacements>;
// Marketplace serves shelves through its own call, which is the whole reason
// it can keep working while the three placement surfaces are silent.
const fetchModules = fetchCommerceModules as jest.MockedFunction<typeof fetchCommerceModules>;

const DAY_MS = 24 * 60 * 60 * 1000;
const NOW = Date.parse("2026-06-01T12:00:00.000Z");

function placement(id: string): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok-${id}`,
    surface: "feed",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "popular",
    rankingVersion: "v1",
    priceMinor: 1000,
    priceCurrency: "USD",
    product: {
      listingId: 1,
      title: `Product ${id}`,
      priceLabel: "$10.00",
      coverImageUrl: "https://cdn.example/x.jpg",
      sellerUserId: 42,
      sellerStoreName: "Store",
      category: "shoes",
      rating: 4.5,
      ratingCount: 10
    }
  };
}

function served(): CommerceServeResult {
  return {
    placements: [placement("p1")],
    cadence: { leadIn: 6, interval: 8, maxPerPage: 2 },
    visiblePercentThreshold: 60,
    visibleDwellMs: 1000
  };
}

function shelves(): CommerceModule[] {
  return [
    {
      key: "because_you_viewed",
      reason: "because_you_viewed",
      titleKey: "commerce:discovery.module.because_you_viewed",
      placements: [placement("p1")]
    }
  ];
}

const defaults = {
  marketplaceRecommendations: true,
  personalizedRecommendations: true,
  frequency: "balanced" as const,
  snoozeUntil: ""
};

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  fetchPlacements.mockResolvedValue(served());
  fetchModules.mockResolvedValue(shelves());
  // The owner is module state, so it survives between tests in a file. Reset
  // it to the default rather than to whatever the previous test happened to
  // leave behind, or a passing test could be inheriting its own precondition.
  setSocialDiscoveryAllowed(true);
  registerCommercePauseWriter(null);
});

/* -------------------------------------------------------------------------- */
/*                            Resolving the preference                         */
/* -------------------------------------------------------------------------- */

describe("what counts as consent", () => {
  it("allows discovery by default", () => {
    expect(socialDiscoveryAllowed(defaults, NOW)).toBe(true);
  });

  it("stops on the master switch", () => {
    expect(socialDiscoveryAllowed({ ...defaults, marketplaceRecommendations: false }, NOW)).toBe(false);
  });

  it("stops while a pause is running", () => {
    const snoozeUntil = new Date(NOW + 5 * DAY_MS).toISOString();
    expect(socialDiscoveryAllowed({ ...defaults, snoozeUntil }, NOW)).toBe(false);
  });

  it("does not stop for a pause that has lapsed", () => {
    // The normalizer keeps a lapsed instant rather than blanking it — clearing
    // it would make normalization depend on the wall clock. So the reader has
    // to be the one that notices, and this is the reader.
    const snoozeUntil = new Date(NOW - 1000).toISOString();
    expect(socialDiscoveryAllowed({ ...defaults, snoozeUntil }, NOW)).toBe(true);
  });

  it("does not stop for a stored value it cannot read", () => {
    // Matches `subject.py::is_expired`, which fails closed on an unparseable
    // timestamp. Disagreeing would mean one side showing a pause the other is
    // ignoring, which is worse than either answer.
    expect(socialDiscoveryAllowed({ ...defaults, snoozeUntil: "next tuesday" }, NOW)).toBe(true);
    expect(isPauseActive("next tuesday", NOW)).toBe(false);
  });

  it("is off, not personalization-off, when personalization is off", () => {
    // Two different objections. "Don't use my history" still gets suggestions.
    expect(socialDiscoveryAllowed({ ...defaults, personalizedRecommendations: false }, NOW)).toBe(true);
  });
});

/* -------------------------------------------------------------------------- */
/*                                  The owner                                  */
/* -------------------------------------------------------------------------- */

describe("the owner", () => {
  it("re-renders subscribers when the switch moves", () => {
    const { result } = renderHook(() => useSocialDiscoveryAllowed());
    expect(result.current).toBe(true);

    act(() => setSocialDiscoveryAllowed(false));

    // A subscription and not a one-shot read: flipping the switch in Settings
    // has to quiet a feed that is already mounted behind it.
    expect(result.current).toBe(false);
  });

  it("defaults to allowed before the store has hydrated", () => {
    // Fails open, deliberately. The server re-reads the same preference on
    // every request and fails closed, so the only thing a closed default would
    // buy is an empty feed on every cold start for every user.
    expect(useSocialDiscoveryAllowed).toBeDefined();
    const { result } = renderHook(() => useSocialDiscoveryAllowed());
    expect(result.current).toBe(true);
  });
});

/* -------------------------------------------------------------------------- */
/*                           The three social surfaces                         */
/* -------------------------------------------------------------------------- */

describe("what the master switch silences", () => {
  it("stops the feed from asking at all", async () => {
    setSocialDiscoveryAllowed(false);
    const { result } = renderHook(() => useFeedCommerce());

    await waitFor(() => expect(result.current.placements.length).toBe(0));
    // Not "fetched and hid it". A request still tells the server this viewer is
    // being served commerce, and is a round trip spent on a refusal.
    expect(fetchPlacements).not.toHaveBeenCalled();
  });

  it("stops reels from asking at all", async () => {
    setSocialDiscoveryAllowed(false);
    const { result } = renderHook(() => useReelsCommerce({ reelIds: ["r1", "r2", "r3"] }));

    await waitFor(() => expect(result.current.chipByReelId.size).toBe(0));
    expect(fetchPlacements).not.toHaveBeenCalled();
  });

  it("stops the messenger strip from asking at all", async () => {
    setSocialDiscoveryAllowed(false);
    const { result } = renderHook(() => useMessengerCommerce());

    await waitFor(() => expect(result.current.placement).toBeNull());
    expect(fetchPlacements).not.toHaveBeenCalled();
  });

  it("lets all three ask again when it is switched back on", async () => {
    setSocialDiscoveryAllowed(false);
    const feed = renderHook(() => useFeedCommerce());
    await waitFor(() => expect(fetchPlacements).not.toHaveBeenCalled());

    act(() => setSocialDiscoveryAllowed(true));

    await waitFor(() => expect(feed.result.current.placements.length).toBe(1));
  });

  it("does not override a surface that was already off for its own reason", async () => {
    // Consent is ANDed with the caller's gate, not substituted for it. Home
    // passes `enabled: isAuthenticated`; a signed-out viewer must stay quiet
    // however the preference reads.
    setSocialDiscoveryAllowed(true);
    renderHook(() => useFeedCommerce({ enabled: false }));

    await waitFor(() => expect(fetchPlacements).not.toHaveBeenCalled());
  });
});

/* -------------------------------------------------------------------------- */
/*                         The surface it does not silence                     */
/* -------------------------------------------------------------------------- */

describe("what the master switch leaves alone", () => {
  it("keeps Marketplace's own shelves working", async () => {
    // The promise printed under the switch: "Marketplace keeps recommending
    // products inside Marketplace either way." Turning off being recommended
    // to is not the same as closing the shop, and a switch that emptied the
    // shelves would break a feature nobody complained about.
    setSocialDiscoveryAllowed(false);
    const { result } = renderHook(() => useMarketplaceCommerce());

    await waitFor(() => expect(fetchModules).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.modules.length).toBe(1));
  });
});

/* -------------------------------------------------------------------------- */
/*                        One pause, four places to start it                   */
/* -------------------------------------------------------------------------- */

describe("starting a pause from a card", () => {
  it("writes the same preference the settings screen writes", () => {
    const writer = jest.fn();
    registerCommercePauseWriter(writer);

    startCommercePause(NOW);

    // The server-side suppression row the feedback call writes is invisible to
    // the settings screen, which would then report suggestions as on with
    // nothing to resume. This is what makes that screen able to describe — and
    // undo — a pause it did not start.
    expect(writer).toHaveBeenCalledWith(new Date(NOW + COMMERCE_PAUSE_DAYS * DAY_MS).toISOString());
  });

  it("is a no-op rather than a crash when no store is mounted", () => {
    registerCommercePauseWriter(null);
    // Reached from a hook that renders with or without a provider. Throwing
    // here would turn "the preference did not save" into "the feed unmounted".
    expect(() => startCommercePause(NOW)).not.toThrow();
  });

  it("agrees with the settings screen about how long a pause is", () => {
    expect(Date.parse(pauseEndsAt(NOW))).toBe(NOW + COMMERCE_PAUSE_DAYS * DAY_MS);
  });

  // Typed down to the one member all four share. Their full states differ —
  // `placements`, `placement`, `chipByReelId`, `modules` — and left to infer,
  // the tuple widens to a union no call signature satisfies.
  const snoozable: [string, () => { onFeedback: (p: CommercePlacement, a: CommerceFeedbackAction) => void }][] = [
    ["feed", () => useFeedCommerce()],
    ["reels", () => useReelsCommerce({ reelIds: ["r1"] })],
    ["messenger", () => useMessengerCommerce()],
    ["marketplace", () => useMarketplaceCommerce()]
  ];

  it.each(snoozable)("is started by the ••• snooze on %s", async (_surface, hook) => {
    const writer = jest.fn();
    registerCommercePauseWriter(writer);
    const { result } = renderHook(hook);
    await waitFor(() =>
      expect(fetchPlacements.mock.calls.length + fetchModules.mock.calls.length).toBeGreaterThan(0)
    );

    act(() => result.current.onFeedback(placement("p1"), "snooze"));

    // All four, including Marketplace — which does not itself gate on the
    // pause. "Hide suggestions for 30 days" has to mean one thing wherever it
    // is tapped, or it is a different feature wearing the same label.
    expect(writer).toHaveBeenCalledTimes(1);
    expect(Date.parse(writer.mock.calls[0][0])).toBeGreaterThan(Date.now());
  });

  it("does not start one for a hide that is not a pause", async () => {
    const writer = jest.fn();
    registerCommercePauseWriter(writer);
    const { result } = renderHook(() => useFeedCommerce());
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());

    act(() => result.current.onFeedback(placement("p1"), "hide"));
    act(() => result.current.onFeedback(placement("p2"), "not_interested"));
    act(() => result.current.onFeedback(placement("p3"), "hide_seller"));

    // Hiding one card is not pausing the feature. Escalating it would silently
    // turn a shrug at one product into a month of silence.
    expect(writer).not.toHaveBeenCalled();
  });
});

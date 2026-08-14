/**
 * The money layers, guarded from the outside.
 *
 * Two failure modes are being pinned here, and neither one is visible to a unit
 * render of the screens:
 *
 * 1. **A tap that goes nowhere.** The whole reason these layers exist is that the
 *    Payments hub had five figures a seller could tap and none of them moved.
 *    A `navigate("MoneyLayer")` against an unregistered route is silent in
 *    production — no crash, no log, just a control that does nothing. So the
 *    navigator source is read and every destination these screens name is
 *    checked against it.
 *
 * 2. **A key with no sentence behind it.** Most of this copy is addressed
 *    dynamically — `payout.stage.${stage}`, `payout.action${Action}`,
 *    `kind.${entry.kind}` — which means a missing catalog entry is not a
 *    typecheck error and not a validate-i18n error either (that tool compares
 *    locales to English; it cannot know English is short a key). It surfaces as
 *    a raw token like `commerce:money.kind.refund` rendered at a seller on a
 *    money row, which reads as a bug in their accounts. So every id the code can
 *    produce is enumerated from the exported constants and resolved against the
 *    catalog.
 *
 * Both tests read source and data rather than mounting anything, so they stay
 * true regardless of how the layers are rendered.
 */

import { readFileSync } from "fs";
import { join } from "path";

import extendedCatalog from "../../i18n/catalogs/en/extended.json";
import {
  ACTIVITY_FILTERS,
  MONEY_LAYER_IDS,
  MONEY_LEDGER_KINDS,
  payoutOnboardingFailure,
  payoutOnboardingOutcome,
  payoutReadiness,
  processingExplainer,
  type ActivityFilterId,
  type MoneyLayerId
} from "../../money/moneyLayers";

const NAVIGATION_DIR = join(__dirname, "..");
const SCREENS_DIR = join(NAVIGATION_DIR, "..", "screens");

const appNavigatorSource = readFileSync(join(NAVIGATION_DIR, "AppNavigator.tsx"), "utf8");
const typesSource = readFileSync(join(NAVIGATION_DIR, "types.ts"), "utf8");
const layerSource = readFileSync(join(SCREENS_DIR, "MoneyLayerScreen.tsx"), "utf8");
const detailSource = readFileSync(join(SCREENS_DIR, "MoneyDetailScreen.tsx"), "utf8");
const hubSource = readFileSync(join(SCREENS_DIR, "BusinessOsPaymentsScreen.tsx"), "utf8");

const money = (extendedCatalog as { commerce: { money: Record<string, unknown> } }).commerce.money;

/** Resolve a dotted suffix under `commerce:money`, or undefined. */
function copy(suffix: string): unknown {
  return suffix
    .split(".")
    .reduce<unknown>(
      (node, key) => (node && typeof node === "object" ? (node as Record<string, unknown>)[key] : undefined),
      money
    );
}

function expectSentence(suffix: string) {
  const value = copy(suffix);
  expect(typeof value).toBe("string");
  expect(String(value).trim().length).toBeGreaterThan(0);
}

function registeredStackRoutes(): Set<string> {
  return new Set(Array.from(appNavigatorSource.matchAll(/<Stack\.Screen\s+name="([^"]+)"/g)).map((m) => m[1]));
}

/**
 * Every route named in a navigation call in the given source.
 *
 * Anchored on `navigation` rather than on the method name: `push` is also
 * `Array.prototype.push`, and the hub builds a list of card ids with it
 * (`list.push("escrow")`), which a looser pattern reads as a navigation target
 * and reports as an unregistered route.
 */
function destinationsIn(source: string): string[] {
  return Array.from(
    source.matchAll(/navigation\??\.\s*(?:navigate|push)\??\.?\(\s*"([A-Za-z]+)"/g)
  ).map((m) => m[1]);
}

/* ------------------------------------------------------------------ *
 * Registration
 * ------------------------------------------------------------------ */

describe("money layer registration", () => {
  it("finds the sources it is guarding", () => {
    expect(registeredStackRoutes().size).toBeGreaterThan(20);
    expect(layerSource.length).toBeGreaterThan(1000);
    expect(detailSource.length).toBeGreaterThan(1000);
  });

  it("registers both money routes", () => {
    const registered = registeredStackRoutes();
    expect(registered.has("MoneyLayer")).toBe(true);
    expect(registered.has("MoneyDetail")).toBe(true);
  });

  it("declares both money routes in the param list", () => {
    expect(typesSource).toMatch(/^\s*MoneyLayer:/m);
    expect(typesSource).toMatch(/^\s*MoneyDetail:/m);
  });

  /**
   * Both screens draw their own `MoneyHeader`. Registered without
   * `headerShown: false` they render two headers — two titles, two back
   * chevrons, stacked. That is the regression Payments already shipped once,
   * and no render of the screen in isolation can catch it because the second
   * header comes from the navigator.
   */
  it("hides the navigator header on screens that draw their own", () => {
    ["MoneyLayer", "MoneyDetail"].forEach((route) => {
      const block = appNavigatorSource.split(`name="${route}"`)[1]?.slice(0, 400) || "";
      expect(block).toContain("headerShown: false");
    });
  });
});

/* ------------------------------------------------------------------ *
 * No dead taps
 * ------------------------------------------------------------------ */

describe("money layer destinations", () => {
  it("only navigates to routes the navigator registers", () => {
    const registered = registeredStackRoutes();
    const named = [...destinationsIn(layerSource), ...destinationsIn(detailSource)];
    // Guard against the regex silently matching nothing and passing vacuously.
    expect(named.length).toBeGreaterThan(2);
    expect(named.filter((route) => !registered.has(route))).toEqual([]);
  });

  it("routes the hub's new taps to registered screens too", () => {
    const registered = registeredStackRoutes();
    const named = destinationsIn(hubSource);
    expect(named).toContain("MoneyLayer");
    expect(named).toContain("MoneyDetail");
    expect(named.filter((route) => !registered.has(route))).toEqual([]);
  });

  /**
   * The five figures that used to do nothing. Each is named here by the layer
   * it must open, so deleting a handler fails a test rather than quietly
   * restoring the dead tap this work exists to remove.
   */
  it("opens a layer from every figure the hub shows", () => {
    ["payout_overview", "processing", "payout_history", "activity"].forEach((layer) => {
      expect(hubSource).toContain(`openMoneyLayer("${layer}")`);
    });
    // Ledger rows and payout rows open the detail screen.
    expect(hubSource).toContain("onPressEntry={openEntryDetail}");
    expect(hubSource).toContain("openPayoutDetail(payout)");
  });

  /**
   * A regression that shipped: every "Set up payouts" control on the hub opened
   * the Verification Center. That screen collects identity documents and never
   * touches payouts, so the one control whose entire job is to unblock getting
   * paid could not unblock it — a seller uploaded an ID and still had no
   * account for money to land in. Nothing failed: the route exists, the tap
   * navigates, the screen renders. Only the destination was wrong, which is
   * why this is pinned by name rather than left to a render test.
   */
  it("sends payout setup to the onboarding layer, not to identity verification", () => {
    expect(hubSource).toContain('openMoneyLayer("payout_onboarding")');
    expect(hubSource).not.toContain("VerificationCenter");
    // The layer's own "set up" / "resume" / "manage" actions route the same way.
    expect(layerSource).toContain('onLayer("payout_onboarding")');
    expect(layerSource).not.toContain("VerificationCenter");
  });

  it("gives every layer a title key that resolves", () => {
    MONEY_LAYER_IDS.forEach((layer: MoneyLayerId) => {
      expect(layerSource).toContain(`${layer}:`);
    });
    // Derived from the id list rather than written out. The hand-listed version
    // of this assertion stayed green when a sixth layer was added, because a
    // list of five keys cannot notice a sixth one is missing — the title would
    // have shipped as a raw `commerce:money.layer.payoutOnboarding` token in
    // the header.
    MONEY_LAYER_IDS.forEach((layer: MoneyLayerId) => {
      const key = layer.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
      expectSentence(`layer.${key}`);
    });
  });
});

/* ------------------------------------------------------------------ *
 * Copy coverage for everything addressed dynamically
 * ------------------------------------------------------------------ */

describe("money layer copy", () => {
  it("has a word for every ledger kind the server can return", () => {
    MONEY_LEDGER_KINDS.forEach((kind) => expectSentence(`kind.${kind}`));
  });

  it("has a label for every activity filter chip", () => {
    ACTIVITY_FILTERS.forEach((id: ActivityFilterId) => expectSentence(`activity.filter.${id}`));
  });

  it("has a chip, a reason and an action for every reachable payout stage", () => {
    // Produced by running the resolver rather than by listing the union, so a
    // new stage with no copy fails here instead of shipping as a raw token.
    const cases = [
      payoutReadiness(null, null),
      payoutReadiness({ connected: false, payouts_enabled: false, state: null }, null),
      payoutReadiness(
        {
          connected: true,
          payouts_enabled: false,
          state: {
            connected_account_id: "acct_1",
            charges_enabled: true,
            details_submitted: false,
            disabled_reason: "",
            last_synced_at: ""
          }
        },
        null
      ),
      payoutReadiness(
        {
          connected: true,
          payouts_enabled: false,
          state: {
            connected_account_id: "acct_1",
            charges_enabled: true,
            details_submitted: true,
            disabled_reason: "",
            last_synced_at: ""
          }
        },
        null
      ),
      payoutReadiness(
        {
          connected: true,
          payouts_enabled: false,
          state: {
            connected_account_id: "acct_1",
            charges_enabled: true,
            details_submitted: true,
            disabled_reason: "rejected.fraud",
            last_synced_at: ""
          }
        },
        null
      ),
      payoutReadiness({ connected: true, payouts_enabled: true, state: null }, null)
    ];

    expect(new Set(cases.map((c) => c.stage)).size).toBe(6);

    cases.forEach((readiness) => {
      expectSentence(`payout.stage.${readiness.stage}`);
      expectSentence(`payout.${readiness.reasonKey}`);
      const suffix = readiness.action
        .split("_")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join("");
      expectSentence(`payout.action${suffix}`);
    });
  });

  it("has copy for every blocker vocabulary word the resolver maps", () => {
    const reasons = [
      "requirements.past_due",
      "requirements.pending_verification",
      "under_review",
      "listed",
      "platform_paused",
      "rejected.fraud",
      "rejected.terms_of_service",
      "rejected.listed",
      "rejected.other",
      "action_required.requested_capabilities",
      "a_word_stripe_has_not_invented_yet"
    ];
    reasons.forEach((disabled_reason) => {
      const readiness = payoutReadiness(
        {
          connected: true,
          payouts_enabled: false,
          state: {
            connected_account_id: "acct_1",
            charges_enabled: true,
            details_submitted: true,
            disabled_reason,
            last_synced_at: ""
          }
        },
        null
      );
      expectSentence(`payout.${readiness.reasonKey}`);
    });
  });

  it("has an explanation for every processing outcome", () => {
    const overviewFor = (patch: Record<string, unknown>) =>
      ({
        seller_user_id: 1,
        currency: "USD",
        as_of: "",
        available_cents: 0,
        processing_cents: 0,
        lifetime_fees_cents: 0,
        lifetime_earnings_cents: 0,
        wallets: [],
        reconciled: true,
        has_wallet: true,
        payout_method: null,
        payout_in_flight: null,
        last_failed_payout: null,
        recent_payouts: [],
        release_path: "none_in_product",
        payout_initiation: "",
        instant_payout: "",
        statements: "",
        tax_documents: "",
        escrow: { supported: false, reason: "" },
        ad_wallet_source: "",
        ...patch
      }) as Parameters<typeof processingExplainer>[0];

    const keys = [
      processingExplainer(null).key,
      processingExplainer(overviewFor({ has_wallet: false })).key,
      processingExplainer(overviewFor({ processing_cents: 0 })).key,
      processingExplainer(overviewFor({ processing_cents: 100, release_path: "none_in_product" })).key,
      processingExplainer(overviewFor({ processing_cents: 100, release_path: "payout_request" })).key
    ];
    expect(new Set(keys).size).toBe(4);
    keys.forEach((key) => expectSentence(`processing.${key}`));
  });

  /**
   * The onboarding layer is the only screen in this family that speaks about an
   * outcome it did not read — it reports what happened to a request it just
   * made. Three of the five outcomes come back success-shaped from the server
   * (notably `ok: true` with no link, which is what an unconfigured Stripe key
   * returns), so each one needs its own sentence. A missing key here would
   * render a raw token at the exact moment a seller is asking "did that work?".
   */
  it("has a sentence for every onboarding outcome the resolvers can produce", () => {
    const outcomes = [
      payoutOnboardingOutcome({ ok: true, onboarding_url: "https://connect.stripe.com/setup/x" }),
      payoutOnboardingOutcome({ ok: true, message: "Stripe is not configured." }),
      payoutOnboardingOutcome(null),
      payoutOnboardingFailure(403, "Approved seller status is required."),
      payoutOnboardingFailure(401, ""),
      payoutOnboardingFailure(500, "boom"),
      payoutOnboardingFailure(0, "")
    ];
    // Five distinct kinds, each with its own message key — asserted so that
    // collapsing two outcomes onto one sentence fails here.
    expect(new Set(outcomes.map((o) => o.kind)).size).toBe(5);
    expect(new Set(outcomes.map((o) => o.messageKey)).size).toBe(5);
    outcomes.forEach((outcome) => expectSentence(`onboarding.${outcome.messageKey}`));
  });

  it("has the standing copy the onboarding layer renders before any request", () => {
    ["whyTitle", "whyBody", "providerNote", "stepsTitle", "step1", "step2", "step3", "start", "starting", "openSeller"].forEach(
      (key) => expectSentence(`onboarding.${key}`)
    );
  });

  it("has both activity empty states and the scope note that bounds the feed", () => {
    ["emptyFeed", "emptyFeedNote", "emptyFilter", "scopeNote"].forEach((key) =>
      expectSentence(`activity.${key}`)
    );
  });

  it("has the truthful notes that stand in for the figures no endpoint reports", () => {
    // Each of these is a declared gap rendered as a sentence. If one loses its
    // copy the layer shows nothing at all, which reads as an omission rather
    // than a limitation.
    expectSentence("overview.paidOutUnavailable");
    expectSentence("payouts.noArrival");
    expectSentence("payouts.relatedNote");
    expectSentence("activity.scopeNote");
  });

  it("has a failure sentence for a payout the provider did not explain", () => {
    expectSentence("payouts.failureGeneric");
    expectSentence("payouts.failureLabel");
  });

  it("has the loading, error and missing-subject states", () => {
    ["states.errorTitle", "states.errorBody", "detail.missingTitle", "detail.missingBody"].forEach(
      expectSentence
    );
  });

  it("keeps the accessibility hints translatable", () => {
    ["a11y.openLayer", "a11y.openPayout", "a11y.openEntry"].forEach(expectSentence);
    // The layer name is interpolated, never concatenated.
    expect(copy("a11y.openLayer")).toContain("{{layer}}");
  });
});

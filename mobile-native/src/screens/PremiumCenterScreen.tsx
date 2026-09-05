/**
 * Premium — one route, six experiences.
 *
 * This screen replaces the old sales-only `PremiumScreen` behind the existing
 * `Premium` route rather than sitting beside it as a second surface. That
 * matters: `Premium` is already the destination for the `/pulse/premium` deep
 * link, premium-lock notifications, dashboard routing and the Intelligence
 * Center. A new route would have upgraded the Profile OS tile and left every
 * one of those entry points pointing at a page that tells a paying member to
 * subscribe.
 *
 * Which layout renders is decided by `premiumExperience()` from canonical
 * server state — founder, active, grace, hold, expired, none. The client never
 * inspects a receipt or a date to work it out.
 *
 * Three rules this file follows without exception:
 *
 * 1. **No cache on this screen.** The tile is allowed to paint a cached label
 *    so a paying member never sees "free" flash on a cold start. This screen is
 *    not: it offers purchase, restore and management, and every one of those
 *    must act on live server state. A spinner after a tap is the honest cost.
 *
 * 2. **Nothing is advertised that the server did not advertise.** `benefits[]`
 *    is rendered verbatim and never appended to; an allowance bar is drawn only
 *    where the server attached a real `allowance` object. There is no code path
 *    here that can produce "1,246 / 2,000 requests" or "10 GB of 10 GB" — not
 *    because the numbers are correct, but because the numbers do not exist
 *    unless canonical metering produced them.
 *
 * 3. **No local cancel.** Cancelling is Apple's to perform. A button here could
 *    only mutate our copy of the state, leaving the member billed while this
 *    screen claimed otherwise. `openManageSubscriptions` deep-links out.
 *
 * After any purchase or restore the screen re-reads `getPremiumCenter()`, so
 * what the member ends up looking at is always the server's answer and never
 * this screen's optimism about what just happened.
 */

import { Ionicons } from "@expo/vector-icons";
import { useIsFocused } from "@react-navigation/native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator, AppState, type AppStateStatus, type LayoutChangeEvent,
  Platform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View
} from "react-native";
import {
  getPremiumCenter,
  getPremiumUsageCenter,
  premiumExperience,
  type PremiumBenefit,
  type PremiumCenter,
  type PremiumExperience,
  type PremiumSubscription,
  type PremiumUsageCenter
} from "../api/premiumCenter";
import {
  UNKNOWN_OFFICE,
  getPrivateOfficeOverview,
  type PrivateOfficeProductState
} from "../api/privateOffice";
import { tierSatisfies } from "../entitlements/canonicalTier";
import { loadCanonicalTier, resetCanonicalTier, useCanonicalTier } from "../entitlements/useCanonicalTier";
import { useFormatters, useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { openDashboardRoute } from "../navigation/dashboardRouting";
import {
  annualSavings,
  getAppleSubscriptionSnapshot,
  getPremiumOffers,
  openManageSubscriptions,
  purchasePremium,
  restorePremiumPurchases,
  type AppleSubscriptionSnapshot,
  type PremiumOffers,
  type PremiumPlan,
  type PremiumPlanOffer
} from "../payments/appleIapPremium";
import { trackPremium } from "../payments/premiumAnalytics";
import { PRIVATE_CONTENT_MESSAGE, resolveRouteProfileContext } from "../profile/profileContext";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { premiumTheme } from "../theme/premiumTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "Premium">;

/** Transient banner. Machine result codes become copy here, never in payments. */
type Flash = { tone: "good" | "warn" | "info"; text: string };

/**
 * Server benefit key → catalog key.
 *
 * `premium.profile.customization` becomes `premium:benefits.profile.customization`.
 * The server's English label rides along as `defaultValue`, so a capability
 * added server-side renders in English on day one instead of rendering its own
 * key at the member.
 */
function benefitCatalogKey(serverKey: string): string {
  const key = String(serverKey || "");
  return key.startsWith("premium.") ? `premium:benefits.${key.slice("premium.".length)}` : `premium:benefits.${key}`;
}

export function PremiumCenterScreen({ route, navigation }: Props) {
  const { authState } = useAuth();
  const { t } = useTranslation();
  const fmt = useFormatters();
  const routeContext = resolveRouteProfileContext(route?.params, authState.user?.user_id);

  const [center, setCenter] = useState<PremiumCenter | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const [offers, setOffers] = useState<PremiumOffers | null>(null);
  const [offersLoading, setOffersLoading] = useState(false);
  const [plan, setPlan] = useState<PremiumPlan>("annual");

  const [busy, setBusy] = useState<"" | "purchasing" | "restoring">("");
  const [flash, setFlash] = useState<Flash | null>(null);

  /**
   * "Your Premium this month" — live counts, never cached (rule 1 applies:
   * a stale usage number is a small lie). Fetched only for members, re-fetched
   * after every successful status load so it tracks the same freshness. On
   * failure it stays `null` and the Command Center modules render as absent —
   * absence is the honest fallback, not zeros.
   */
  const [usageCenter, setUsageCenter] = useState<PremiumUsageCenter | null>(null);
  useEffect(() => {
    if (!center?.membership.is_premium) {
      setUsageCenter(null);
      return;
    }
    let cancelled = false;
    getPremiumUsageCenter()
      .then((next) => { if (!cancelled) setUsageCenter(next.ok ? next : null); })
      .catch(() => { if (!cancelled) setUsageCenter(null); });
    return () => { cancelled = true; };
  }, [center]);

  /** `premium_plan_viewed` is a funnel step, not a render count. Fire it once. */
  const planViewed = useRef(false);
  const offerRequest = useRef(0);
  const offerRequestActive = useRef(false);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    trackPremium("premium_status_load_started", { mode });
    setError("");
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const next = await getPremiumCenter();
      setCenter(next);
      const nextExperience = premiumExperience(next);
      trackPremium("premium_status_load_success", { mode, state: nextExperience });
      trackPremium(
        nextExperience === "none" || nextExperience === "expired"
          ? "premium_status_state_free"
          : "premium_status_state_active",
        { state: nextExperience }
      );
    } catch (loadError) {
      trackPremium("premium_status_load_failure", { mode });
      setError(loadError instanceof Error ? loadError.message : t("premium:loadError"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [t]);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    load("initial");
  }, [load, routeContext.isOwnProfile]);

  /**
   * Re-read entitlements when the member comes back to this screen.
   *
   * Cancelling and resubscribing both happen in Apple's UI, not ours, and Apple
   * tells us by webhook rather than by returning a result. Without this, a member
   * who taps Manage Subscription, cancels, and swipes back is looking at a screen
   * that still says their subscription renews — and the only way to correct it is
   * a pull-to-refresh they have no reason to perform.
   *
   * `isFocused` covers the return from Apple's sheet and any in-app navigation.
   * The AppState listener covers backgrounding out to the Settings app and back.
   * Both are re-entry signals, so neither shows the full-screen loading state.
   */
  const isFocused = useIsFocused();
  const hasLoadedOnce = useRef(false);
  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    if (!isFocused) return;
    if (!hasLoadedOnce.current) {
      hasLoadedOnce.current = true;
      return; // the mount effect above is already fetching
    }
    void load("refresh");
  }, [isFocused, load, routeContext.isOwnProfile]);

  useEffect(() => {
    if (!routeContext.isOwnProfile) return;
    // Read through a ref rather than module scope: `AppState.currentState` is
    // seeded during app launch and reads "inactive" forever at module level.
    const previous = { current: AppState.currentState };
    const subscription = AppState.addEventListener("change", (next: AppStateStatus) => {
      const returning = previous.current.match(/inactive|background/) && next === "active";
      previous.current = next;
      if (returning) void load("refresh");
    });
    return () => subscription.remove();
  }, [load, routeContext.isOwnProfile]);

  const experience: PremiumExperience = premiumExperience(center);
  // Only surfaces that can actually sell need Apple's catalog. A Founder or an
  // active member is not shown prices, so their screen never touches StoreKit.
  const sells = Boolean(center) && (experience === "none" || experience === "expired");
  /**
   * A member who already pays still needs Apple's catalog — for their own price.
   *
   * Only the App Store knows what this Apple ID was actually charged, in their
   * currency, after any regional pricing. Storing a price server-side would mean
   * showing a US dollar figure to someone billed in yen. So the subscription card
   * asks StoreKit for the product the server says they hold, and shows nothing at
   * all if StoreKit does not answer.
   */
  const subscription = center?.subscription ?? null;
  const needsSubscriptionPrice = Boolean(subscription?.product_id);

  /**
   * Apple's own answer, for the one gap the server leaves.
   *
   * Queried only when the server says this member pays but returned no billing
   * row — an entitlement projected before the subscription landed, or a fresh
   * reinstall. Telling a paying member their billing details do not exist is
   * the failure this closes. Founders are excluded: they hold Premium with no
   * provider subscription, so "no billing record" is the truthful answer there
   * and StoreKit is never consulted.
   */
  const [appleBilling, setAppleBilling] = useState<AppleSubscriptionSnapshot | null>(null);
  const needsAppleBilling =
    Boolean(center) && !subscription &&
    (experience === "active" || experience === "grace" || experience === "hold");
  useEffect(() => {
    if (!needsAppleBilling) {
      setAppleBilling(null);
      return;
    }
    let cancelled = false;
    getAppleSubscriptionSnapshot()
      .then((snapshot) => { if (!cancelled) setAppleBilling(snapshot); })
      .catch(() => { if (!cancelled) setAppleBilling(null); });
    return () => { cancelled = true; };
  }, [needsAppleBilling]);

  const loadOffers = useCallback(async () => {
    if (offerRequestActive.current) return;
    offerRequestActive.current = true;
    const request = ++offerRequest.current;
    setOffersLoading(true);
    setOffers(null);
    trackPremium("premium_product_fetch_started");
    try {
      const next = await getPremiumOffers();
      if (request !== offerRequest.current) return;
      setOffers(next);
      if (next.status === "success") {
        trackPremium("premium_product_fetch_success", { plans: next.plans.length });
      } else if (next.status === "empty") {
        trackPremium("premium_product_fetch_empty");
      } else {
        // The status alone cannot tell an operator whether Apple was even asked.
        // The diagnostics carry that, and every field of them is safe to emit.
        trackPremium("premium_product_fetch_failed", {
          reason: next.status,
          error_code: next.diagnostics.errorCode,
          requested: next.diagnostics.requestedProductIds.length,
          returned: next.diagnostics.productCount
        });
      }
      next.missingPlans.forEach((missing) => {
        trackPremium(missing === "monthly" ? "premium_product_missing_monthly" : "premium_product_missing_annual");
      });
      setPlan((current) => next.plans.some((offer) => offer.plan === current) ? current : (next.plans[0]?.plan || current));
      if (next.plans.length && !planViewed.current) {
        planViewed.current = true;
        trackPremium("premium_plan_viewed", {
          plans: next.plans.length,
          savings_percent: next.annualSavingsPercent
        });
      }
    } catch {
      if (request === offerRequest.current) {
        trackPremium("premium_product_fetch_failed", { reason: "unexpected" });
        setOffers({
          plans: [],
          annualSavingsPercent: null,
          status: "failed",
          missingPlans: ["monthly", "annual"],
          diagnostics: {
            requestedProductIds: [],
            requestType: "subs",
            productCount: 0,
            returnedProductIds: [],
            errorCode: "screen_unexpected",
            environment: `${Platform.OS}/unknown`
          }
        });
      }
    } finally {
      if (request === offerRequest.current) {
        offerRequestActive.current = false;
        setOffersLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if ((sells || needsSubscriptionPrice) && !offers) void loadOffers();
  }, [sells, needsSubscriptionPrice, offers, loadOffers]);

  /**
   * The member's own localized price, or `null`.
   *
   * Matched on the verified product identifier rather than on the plan period,
   * so a member grandfathered onto a retired product is never shown the price of
   * the product we sell today. `null` means the row is omitted — a subscription
   * card missing one row is honest, a card showing someone else's price is not.
   */
  const subscriptionPrice =
    offers?.plans.find((offer) => offer.productId === subscription?.product_id)?.displayPrice ?? null;
  /** Only a subscription that is still being billed gets to show a price. */
  const showSubscriptionPrice =
    needsSubscriptionPrice &&
    subscription?.state !== "expired" &&
    subscription?.state !== "revoked";

  const choosePlan = useCallback((next: PremiumPlan) => {
    setPlan(next);
    trackPremium(next === "annual" ? "premium_annual_selected" : "premium_monthly_selected", { plan: next });
  }, []);

  const onPurchase = useCallback(async () => {
    if (busy) return;
    setBusy("purchasing");
    setFlash(null);
    trackPremium("premium_purchase_started", { plan });
    let result;
    try {
      result = await purchasePremium(plan);
    } catch {
      // `purchasePremium` already funnels its own failures into result codes;
      // this only catches a transport throw on the intent call.
      result = { status: "verification_pending" } as const;
    }
    if (result.status === "cancelled") {
      // Deliberately not an error tone. The member chose this.
      trackPremium("premium_purchase_cancelled", { plan });
      setFlash({ tone: "info", text: t("premium:purchase.cancelled") });
    } else if (result.status === "verified") {
      trackPremium("premium_purchase_verified", { plan });
      setFlash({ tone: "good", text: t("premium:purchase.verified") });
    } else if (result.status === "unavailable") {
      setFlash({ tone: "warn", text: t("premium:purchase.unavailable") });
    } else {
      setFlash({ tone: "info", text: t("premium:purchase.pending") });
    }
    setBusy("");
    // Always re-read. A "verified" that the server later disagrees with must
    // lose to the server, and a "pending" that in fact landed must be believed.
    // The shared canonical answer is dropped for the same reason: every gate in
    // the app must re-ask the server rather than keep the pre-purchase tier.
    resetCanonicalTier();
    void loadCanonicalTier();
    await load("refresh");
  }, [busy, load, plan, t]);

  const onRestore = useCallback(async () => {
    if (busy) return;
    setBusy("restoring");
    setFlash(null);
    trackPremium("premium_restore_started");
    const result = await restorePremiumPurchases().catch(() => ({ status: "failed" } as const));
    trackPremium("premium_restore_completed", { result: result.status });
    if (result.status === "restored") {
      setFlash({ tone: "good", text: t("premium:restore.restored", { count: result.count }) });
    } else if (result.status === "empty") {
      setFlash({ tone: "info", text: t("premium:restore.empty") });
    } else if (result.status === "unavailable") {
      setFlash({ tone: "warn", text: t("premium:restore.unavailable") });
    } else {
      setFlash({ tone: "warn", text: t("premium:restore.failed") });
    }
    setBusy("");
    resetCanonicalTier();
    void loadCanonicalTier();
    await load("refresh");
  }, [busy, load, t]);

  /**
   * Where a locked crypto row sends the member.
   *
   * This screen *is* the reactivation experience — the plans a lapsed member can
   * buy and the restore/manage actions for one whose receipt lives on another
   * device both already render above. Navigating away to a second paywall would
   * mean two selling surfaces to keep in agreement, so the locked row scrolls to
   * the one that is already here instead. The offset is measured rather than
   * guessed because the block above it changes height: a member with a lapsed
   * subscription gets a billing row that a never-subscribed visitor does not.
   *
   * The fallback is the top of the screen, not a no-op. If layout has not been
   * measured yet the member still moves toward the plans rather than tapping a
   * row that appears to do nothing.
   */
  const scrollRef = useRef<ScrollView>(null);
  const upgradeOffset = useRef(0);
  const onUpgradeOffset = useCallback((event: LayoutChangeEvent) => {
    upgradeOffset.current = event.nativeEvent.layout.y;
  }, []);
  const onUpgrade = useCallback(() => {
    scrollRef.current?.scrollTo({ y: Math.max(upgradeOffset.current - 12, 0), animated: true });
  }, []);

  const onManage = useCallback(async () => {
    trackPremium("premium_manage_opened", { mode: experience });
    const opened = await openManageSubscriptions();
    if (!opened) setFlash({ tone: "warn", text: t("premium:manage.failed") });
  }, [experience, t]);

  // Visitor route with no visitor variant. Billing is nobody else's business,
  // and the server accepts no target user, so there is nothing to render.
  if (!routeContext.isOwnProfile) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>{PRIVATE_CONTENT_MESSAGE}</Text>
      </View>
    );
  }

  if (loading && !center) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={premiumTheme.gold} />
        <Text style={styles.centerText}>{t("premium:loading")}</Text>
      </View>
    );
  }

  if (error && !center) {
    return (
      <View style={styles.center}>
        <Text style={styles.centerText}>{t("premium:loadError")}</Text>
        <Pressable accessibilityRole="button" style={styles.retry} onPress={() => load("initial")}>
          <Text style={styles.retryText}>{t("premium:retry")}</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <ScrollView
      ref={scrollRef}
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} tintColor={premiumTheme.gold} />
      }
    >
      <HeaderSection center={center} experience={experience} />

      {flash ? <FlashBanner flash={flash} /> : null}

      <NoticesSection center={center} />

      {/*
        A lapsed member sees both, in this order: what they had, then what they
        can buy. Showing only the plans would greet someone who paid for a year
        exactly as it greets someone who has never subscribed, and it would hide
        the one fact they came here for — the date access actually stopped.
        Someone who never subscribed has no row, so they see the plans alone.
      */}
      {subscription || !sells ? (
        <BillingSection
          subscription={subscription}
          apple={appleBilling}
          experience={experience}
          // A lapsed subscription's price belongs to a charge that is no longer
          // happening. Sitting above live plan prices it would read as a current
          // bill, so the row is dropped rather than shown as history.
          price={showSubscriptionPrice ? subscriptionPrice : null}
          priceLoading={showSubscriptionPrice && offersLoading}
        />
      ) : null}

      {/*
        Plans and actions are measured together as one block: this pair is the
        whole answer to "how do I get access back", and which half applies
        depends on why the member lapsed. A locked crypto row scrolls here.
      */}
      <View onLayout={onUpgradeOffset}>
        {sells ? (
          <PlansSection
            offers={offers}
            loading={offersLoading}
            plan={plan}
            onPlan={choosePlan}
            busy={busy === "purchasing"}
            disabled={Boolean(busy)}
            onPurchase={onPurchase}
            onRetry={loadOffers}
            expired={experience === "expired"}
          />
        ) : null}

        {/*
          Directly under the plans, not at the foot of the screen. Restore is the
          remedy for the member who already paid — on this device or another — and
          burying it below the benefits list is how someone ends up buying a
          second subscription. Apple expects it discoverable too.
        */}
        <ActionsSection
          experience={experience}
          busy={busy}
          onManage={onManage}
          onRestore={onRestore}
        />
      </View>

      <BenefitsSection benefits={center?.benefits || []} held={Boolean(center?.membership.is_premium)} />

      <CryptoIntelligenceSection navigation={navigation} onUpgrade={onUpgrade} />

      <PrivateOfficeEntrySection navigation={navigation} />

      <NotYetSection items={center?.not_yet || []} />

      <CommandCenterSection
        experience={experience}
        held={Boolean(center?.membership.is_premium)}
        usage={usageCenter}
        navigation={navigation}
      />

      <FreeCoreSection />

      <Text style={styles.footnote}>
        {center?.not_verification || t("premium:notVerification")}
      </Text>
      <Text style={styles.footnote}>{t("premium:privateNote")}</Text>
    </ScrollView>
  );
}

/* -------------------------------------------------------------------------- *
 * Header
 * -------------------------------------------------------------------------- */

function HeaderSection({ center, experience }: { center: PremiumCenter | null; experience: PremiumExperience }) {
  const { t } = useTranslation();
  const founderNumber = center?.founder.founder_number || 0;
  // `lifetime` borrows the founder tone: gold is this screen's colour for a
  // membership that does not run out, and that is exactly what it is.
  const tone = premiumTheme.state[
    experience === "founder" || experience === "active" ? experience
      : experience === "lifetime" ? "founder"
        : experience === "grace" ? "grace"
          : experience === "hold" ? "hold"
            : "none"
  ];

  const heading =
    experience === "founder"
      ? t("premium:header.founder")
      // Not `header.founder`: a permanent membership is not a Founder number,
      // and borrowing that word would claim a status this account was never
      // assigned. It is a Premium member whose membership does not end.
      : experience === "lifetime" || experience === "active" || experience === "grace" || experience === "hold"
        ? t("premium:header.member")
        : t("premium:header.free");

  return (
    <View style={[styles.hero, (experience === "founder" || experience === "lifetime" || experience === "active") && styles.heroGold]}>
      <View style={styles.heroTop}>
        <View style={styles.heroCrest}>
          {/* Filled for the two permanent states, outlined for the ones that
              depend on a renewal going through. */}
          <Ionicons name={experience === "founder" || experience === "lifetime" ? "diamond" : "diamond-outline"} size={26} color={premiumTheme.gold} />
        </View>
        <View style={styles.heroBody}>
          <Text style={styles.heroTitle}>{heading}</Text>
          <Text style={[styles.heroStatus, { color: tone }]}>{t(`premium:status.${experience}`)}</Text>
        </View>
      </View>
      {/*
        The Founder number is identity, not billing, and it is the member's own
        screen — but the locked price is never printed next to it. What someone
        pays is between them and Apple.
      */}
      {experience === "founder" && founderNumber > 0 ? (
        <Text style={styles.heroFounder}>{t("premium:header.founderNumber", { number: founderNumber })}</Text>
      ) : null}
      <Text style={styles.heroCaption}>{t(`premium:blurb.${experience}`)}</Text>
    </View>
  );
}

function FlashBanner({ flash }: { flash: Flash }) {
  const tint = flash.tone === "good" ? premiumTheme.gold : flash.tone === "warn" ? colors.warning : colors.muted;
  return (
    <View accessibilityLiveRegion="polite" style={[styles.flash, { borderColor: `${tint}66` }]}>
      <Text style={[styles.flashText, { color: tint }]}>{flash.text}</Text>
    </View>
  );
}

/**
 * Server notices, rendered by code.
 *
 * The server ships a stable `code` and an English `message`. The code selects
 * translated copy; the message is the fallback so a notice added server-side is
 * never silently dropped.
 */
function NoticesSection({ center }: { center: PremiumCenter | null }) {
  const { t } = useTranslation();
  const notices = center?.notices || [];
  if (!notices.length) return null;
  return (
    <View style={styles.section}>
      {notices.map((notice) => (
        <View key={notice.code} style={styles.noticeRow}>
          <Ionicons name="information-circle-outline" size={16} color={premiumTheme.gold} />
          <Text style={styles.noticeText}>
            {t(`premium:notices.${notice.code}`, { defaultValue: notice.message })}
          </Text>
        </View>
      ))}
    </View>
  );
}

/* -------------------------------------------------------------------------- *
 * Plans (discovery / purchase)
 * -------------------------------------------------------------------------- */

/**
 * Why the plan list is empty, in the member's words.
 *
 * The four zero states have four different remedies and used to share one
 * sentence. "The App Store didn't return any products" is true only of `empty`;
 * saying it when the request never left the device sends the member to check a
 * connection that was never the problem, and sends support to Apple instead of
 * to the catalog. `success` maps here only because an exhaustive record must,
 * and a successful fetch with no plans is indistinguishable from `empty`.
 */
const ZERO_STATE_BODY: Record<PremiumOffers["status"], string> = {
  unavailable: "premium:plans.unavailableOffer",
  empty: "premium:plans.unavailableBody",
  failed: "premium:plans.unavailableFailed",
  timeout: "premium:plans.unavailableTimeout",
  success: "premium:plans.unavailableBody"
};

/**
 * A short code a member can read out to support.
 *
 * Built from the status and the already-sanitized StoreKit error token — both
 * safe to print. It is the only thing on this screen that survives a screenshot
 * sent to support, and it is what turns "it just says plans aren't available"
 * into an answerable report.
 */
function zeroStateReference(offers: PremiumOffers | null): string {
  const status = (offers?.status ?? "unavailable").toUpperCase();
  const code = offers?.diagnostics?.errorCode;
  return code && code !== "unknown" ? `${status}-${code}` : status;
}

export function PlansSection({
  offers, loading, plan, onPlan, busy, disabled, onPurchase, onRetry, expired
}: {
  offers: PremiumOffers | null;
  loading: boolean;
  plan: PremiumPlan;
  onPlan: (plan: PremiumPlan) => void;
  busy: boolean;
  disabled: boolean;
  onPurchase: () => void;
  onRetry: () => void;
  expired: boolean;
}) {
  const { t } = useTranslation();
  const plans = offers?.plans || [];
  const savings = offers?.annualSavingsPercent ?? null;

  if (loading && !offers) {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t("premium:plans.heading")}</Text>
        <View style={styles.inlineLoading}>
          <ActivityIndicator color={premiumTheme.gold} />
          <Text style={styles.note}>{t("premium:plans.loading")}</Text>
        </View>
      </View>
    );
  }

  // StoreKit is unreachable, or the server withdrew the catalog. The surface
  // stays — this is still where a member manages membership — but it must not
  // fall back to a remembered price, so it offers restore instead of a price.
  if (!plans.length) {
    const status = offers?.status ?? "unavailable";
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t("premium:plans.heading")}</Text>
        <Text style={styles.body}>{t("premium:plans.unavailable")}</Text>
        <Text style={styles.note}>{t(ZERO_STATE_BODY[status])}</Text>
        <Text style={styles.note}>
          {t("premium:plans.reference", { code: zeroStateReference(offers) })}
        </Text>
        <Pressable accessibilityRole="button" disabled={loading} style={styles.retry} onPress={onRetry}>
          <Text style={styles.retryText}>{t("premium:retry")}</Text>
        </Pressable>
      </View>
    );
  }

  const selected = plans.find((offer) => offer.plan === plan) || plans[0];

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t(expired ? "premium:plans.headingAgain" : "premium:plans.heading")}</Text>
      <View accessibilityRole="radiogroup" style={styles.planRow}>
        {plans.map((offer) => (
          <PlanCard
            key={offer.productId}
            offer={offer}
            selected={offer.plan === selected.plan}
            savings={offer.plan === "annual" ? savings : null}
            onPress={() => onPlan(offer.plan)}
          />
        ))}
      </View>
      {offers?.missingPlans.includes("monthly") ? <Text style={styles.note}>{t("premium:plans.missingMonthly", { defaultValue: "The monthly plan is temporarily unavailable." })}</Text> : null}
      {offers?.missingPlans.includes("annual") ? <Text style={styles.note}>{t("premium:plans.missingAnnual", { defaultValue: "The annual plan is temporarily unavailable." })}</Text> : null}

      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: disabled || busy, busy }}
        accessibilityLabel={t("premium:purchase.start")}
        disabled={disabled || busy}
        style={({ pressed }) => [styles.primaryAction, (disabled || busy) && styles.dimmed, pressed && styles.pressed]}
        onPress={onPurchase}
      >
        {busy ? <ActivityIndicator color={colors.background} /> : null}
        <Text style={styles.primaryActionText}>
          {busy ? t("premium:purchase.working") : t("premium:purchase.start")}
        </Text>
      </Pressable>
      {/* Who takes the money, stated next to the button that starts it. The app
          never sees a card number and this is the only place that can say so. */}
      <View style={styles.secureRow}>
        <Ionicons name="lock-closed" size={12} color={colors.muted} />
        <Text style={styles.note}>{t("premium:plans.secure")}</Text>
      </View>
      <Text style={styles.note}>{t("premium:plans.terms")}</Text>
    </View>
  );
}

/**
 * One plan.
 *
 * `displayPrice` is Apple's own formatted string in the member's storefront
 * currency, printed rather than re-formatted — re-formatting it locally is how
 * a screen ends up showing "$9.99" to someone Apple charges ¥1,500.
 */
function PlanCard({
  offer, selected, savings, onPress
}: { offer: PremiumPlanOffer; selected: boolean; savings: number | null; onPress: () => void }) {
  const { t } = useTranslation();
  const label = t(`premium:plans.${offer.plan}`);
  const period = t(`premium:plans.per.${offer.plan}`);
  // Both the ribbon and the percentage hang off the same computed figure. When
  // `annualSavings` cannot state a saving honestly — one plan missing, mixed
  // currencies, annual not actually cheaper — the card carries no claim at all.
  const savingsLabel = savings !== null ? t("premium:plans.save", { percent: savings }) : "";
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ selected }}
      accessibilityLabel={[label, offer.displayPrice, period, savingsLabel].filter(Boolean).join(", ")}
      style={({ pressed }) => [styles.plan, selected && styles.planSelected, pressed && styles.pressed]}
      onPress={onPress}
    >
      {savings !== null ? (
        <View style={styles.planRibbon}>
          <Text style={styles.planRibbonText}>{t("premium:plans.bestValue")}</Text>
        </View>
      ) : null}
      <View style={styles.planHead}>
        {/* The selection is a state, not a colour. Rendering the radio means it
            survives a member who cannot tell the gold border from the plain one. */}
        <Ionicons
          name={selected ? "radio-button-on" : "radio-button-off"}
          size={16}
          color={selected ? premiumTheme.gold : colors.muted}
        />
        <Text style={[styles.planName, selected && styles.planNameSelected]} numberOfLines={1}>
          {label}
        </Text>
      </View>
      <Text style={styles.planPrice}>{offer.displayPrice}</Text>
      <Text style={styles.planPeriod}>{period}</Text>
      {/* Computed from the two localized prices actually returned. Absent when
          there is no honest figure — never a hardcoded "SAVE 17%". */}
      {savings !== null ? (
        <View style={styles.planSave}>
          <Text style={styles.planSaveText}>{savingsLabel}</Text>
        </View>
      ) : null}
    </Pressable>
  );
}

/* -------------------------------------------------------------------------- *
 * Billing (control center)
 * -------------------------------------------------------------------------- */

/**
 * Safe billing facts only.
 *
 * Provider, plan, period, status and the period end. No subscription id, no
 * transaction id, no receipt — the server does not send them, and this is the
 * screen that would have leaked them if it did.
 */
export function BillingSection({
  subscription, apple, experience, price, priceLoading
}: {
  subscription: PremiumSubscription | null;
  apple?: AppleSubscriptionSnapshot | null;
  experience: PremiumExperience;
  price: string | null;
  priceLoading: boolean;
}) {
  const { t } = useTranslation();
  const fmt = useFormatters();

  // A permanent membership has no billing to show and, more importantly, no
  // date that means anything. Checked ahead of `!subscription` rather than
  // inside it, because the case that matters is the one where a subscription
  // row DOES exist: an owner who once subscribed and let it lapse still has
  // that history, and every branch below would read it as the current state and
  // print "Ends" over a date in the past. The membership did not end. Like the
  // Founder branch this is also enforced here rather than only at the call
  // site, because the component is exported and rendered directly by tests.
  if (experience === "lifetime") {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t("premium:billing.heading")}</Text>
        <Text style={styles.body}>{t("premium:billing.lifetimeNone")}</Text>
      </View>
    );
  }

  if (!subscription) {
    // The server has no billing row but Apple can prove one. Show Apple's own
    // signed facts rather than telling a paying member their details do not
    // exist. A field Apple did not supply is omitted, never invented — and if
    // Apple cannot prove a subscription either, the honest copy below stands.
    // Founders are excluded here as well as at the call site: this component is
    // exported and rendered directly by tests and by any future caller, and a
    // Founder must never be shown a billing card for a subscription they do not
    // have.
    if (apple && experience !== "founder") {
      return (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t("premium:billing.heading")}</Text>
          <Fact
            icon="star-outline"
            label={t("premium:billing.plan")}
            value={apple.plan
              ? t("premium:billing.planValue", { period: t(`premium:period.${apple.plan}`) })
              : t("premium:billing.planValueUnknown")}
          />
          {apple.displayPrice ? (
            <Fact icon="pricetag-outline" label={t("premium:billing.price")} value={apple.displayPrice} />
          ) : null}
          <Fact
            icon="ellipse"
            tone={statusTone(apple.status)}
            label={t("premium:billing.status")}
            value={t(`premium:subState.${apple.status}`)}
          />
          <Fact
            icon={apple.status === "active" ? "refresh-outline" : "calendar-outline"}
            label={apple.status === "active" ? t("premium:billing.renewsOn") : t("premium:billing.expiresOn")}
            value={fmt.date(apple.expiresAt)}
          />
          <Fact
            icon="logo-apple"
            label={t("premium:billing.provider")}
            value={t("premium:provider.apple_app_store")}
          />
          {apple.originalPurchaseAt ? (
            <Fact icon="time-outline" label={t("premium:billing.since")} value={fmt.date(apple.originalPurchaseAt)} />
          ) : null}
        </View>
      );
    }
    if (experience === "founder") {
      return (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t("premium:billing.heading")}</Text>
          <Text style={styles.body}>{t("premium:billing.founderNone")}</Text>
        </View>
      );
    }

    // The entitlement service can still verify usable Premium while an Apple
    // provider row is delayed. In that narrow state, show the one fact the
    // server did verify and omit every unavailable billing field. Never replace
    // a valid active membership with a vague "no billing details" paragraph.
    if (experience === "active" || experience === "grace" || experience === "hold") {
      const state: PremiumSubscription["state"] =
        experience === "grace" ? "grace" : experience === "hold" ? "paused" : "active";
      return (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>{t("premium:billing.heading")}</Text>
          <Fact
            icon="ellipse"
            tone={statusTone(state)}
            label={t("premium:billing.status")}
            value={t(`premium:subState.${state}`)}
          />
        </View>
      );
    }

    return null;
  }

  const period = subscription.billing_period;
  // "PulseSoc Premium — Monthly". The product name is ours and the period comes
  // from the plan key the server derived from a verified Apple product id, so
  // neither half is inferred from a price.
  const planValue = period
    ? t("premium:billing.planValue", {
        period: t(`premium:period.${period}`, { defaultValue: period })
      })
    : t("premium:billing.planValueUnknown");

  const renewsAt = subscription.renews_at ? fmt.date(subscription.renews_at) : "";
  // Enforced here rather than assumed. `normalizeSubscription` already
  // guarantees the two are mutually exclusive, but a component that renders
  // whatever it is handed will one day be handed both — and the failure mode is
  // a member reading "Renews on" and "Expires on" above the same date.
  const expiresAt = !renewsAt && subscription.expires_at ? fmt.date(subscription.expires_at) : "";
  const since = subscription.original_purchase_at ? fmt.date(subscription.original_purchase_at) : "";

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t("premium:billing.heading")}</Text>

      <Fact icon="star-outline" label={t("premium:billing.plan")} value={planValue} />

      {/* Apple's own formatted string, in the member's currency. While StoreKit
          is still answering the row is a skeleton rather than a stale or
          invented figure, and if StoreKit never answers the row is dropped. */}
      {price ? (
        <Fact icon="pricetag-outline" label={t("premium:billing.price")} value={price} />
      ) : priceLoading ? (
        <FactSkeleton label={t("premium:billing.price")} />
      ) : null}

      <Fact
        icon="ellipse"
        tone={statusTone(subscription.state)}
        label={t("premium:billing.status")}
        value={t(`premium:subState.${subscription.state}`)}
      />

      {/* Exactly one of these can be set — the server decides which, so the verb
          and the date can never disagree. Being told a cancelled subscription
          "renews" is the single most damaging thing this card could say. */}
      {renewsAt ? (
        <Fact icon="refresh-outline" label={t("premium:billing.renewsOn")} value={renewsAt} />
      ) : null}
      {expiresAt ? (
        <Fact icon="calendar-outline" label={t("premium:billing.expiresOn")} value={expiresAt} />
      ) : null}

      {/* Always Apple here. This card is only reachable for an App Store
          subscription, and naming the wrong biller would send a member hunting
          for a card statement that does not exist. */}
      <Fact
        icon="logo-apple"
        label={t("premium:billing.provider")}
        value={t(`premium:provider.${
          subscription.provider === "apple_iap" ? "apple_app_store" : (subscription.provider || "unknown")
        }`, {
          defaultValue: subscription.provider || "—"
        })}
      />

      {/* Omitted rather than guessed. We only know this for subscriptions whose
          stored Apple payload carried a signed original purchase date. */}
      {since ? (
        <Fact icon="time-outline" label={t("premium:billing.since")} value={since} />
      ) : null}

      {subscription.state === "canceled" ? (
        <Text style={styles.note}>{t("premium:billing.cancelPending")}</Text>
      ) : null}
      {subscription.state === "grace" ? (
        <Text style={styles.note}>{t("premium:billing.graceNote")}</Text>
      ) : null}
      {subscription.state === "billing_retry" ? (
        <Text style={styles.note}>{t("premium:billing.retryNote")}</Text>
      ) : null}
    </View>
  );
}

/**
 * The accent colour for a status dot.
 *
 * Colour is decoration only — every state also renders its own word beside the
 * dot, so nothing here is the sole carrier of meaning for a member who cannot
 * distinguish these hues.
 */
function statusTone(state: PremiumSubscription["state"]): string {
  // Mint for a healthy membership, amber for something the member can still
  // fix, muted for something already over. Amber rather than red for grace and
  // billing retry on purpose — access is still live, and colouring a recoverable
  // billing hiccup as an error tells them they lost something they still have.
  if (state === "active" || state === "trialing") return colors.accent;
  if (state === "grace" || state === "billing_retry" || state === "canceled") return colors.warning;
  return colors.muted;
}

function Fact({
  label, value, icon, tone
}: {
  label: string;
  value: string;
  icon?: keyof typeof Ionicons.glyphMap;
  tone?: string;
}) {
  return (
    <View style={styles.factRow} accessibilityLabel={`${label}: ${value}`} accessible>
      <View style={styles.factLabelGroup}>
        {icon ? (
          <Ionicons
            name={icon}
            size={icon === "ellipse" ? 10 : 15}
            color={tone || colors.muted}
            // The label already says what this is; a second announcement of the
            // icon would only make the row longer to listen to.
            accessibilityElementsHidden
            importantForAccessibility="no"
          />
        ) : null}
        <Text style={styles.factLabel}>{label}</Text>
      </View>
      <Text style={styles.factValue}>{value}</Text>
    </View>
  );
}

/** A row whose value is still loading. Never a dash, which reads as "none". */
function FactSkeleton({ label }: { label: string }) {
  const { t } = useTranslation();
  return (
    <View style={styles.factRow} accessibilityLabel={`${label}: ${t("premium:billing.loadingValue")}`} accessible>
      <View style={styles.factLabelGroup}>
        <Ionicons
          name="pricetag-outline"
          size={15}
          color={colors.muted}
          accessibilityElementsHidden
          importantForAccessibility="no"
        />
        <Text style={styles.factLabel}>{label}</Text>
      </View>
      <View style={styles.factSkeleton} />
    </View>
  );
}

/* -------------------------------------------------------------------------- *
 * Benefits
 * -------------------------------------------------------------------------- */

function BenefitsSection({ benefits, held }: { benefits: PremiumBenefit[]; held: boolean }) {
  const { t } = useTranslation();
  if (!benefits.length) {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t("premium:benefits.heading")}</Text>
        <Text style={styles.note}>{t("premium:benefits.empty")}</Text>
      </View>
    );
  }
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t("premium:benefits.heading")}</Text>
      {benefits.map((benefit) => (
        <BenefitRow key={benefit.key} benefit={benefit} held={held} />
      ))}
    </View>
  );
}

function BenefitRow({ benefit, held }: { benefit: PremiumBenefit; held: boolean }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const active = Boolean(benefit.active_now);
  const allowance = benefit.allowance;
  // A bar is drawn only where a real metered grant exists. `limit` absent means
  // unmetered, and an unmetered capability gets no invented ceiling.
  const metered = Boolean(allowance && Number(allowance.limit) > 0 && allowance.used !== null && allowance.used !== undefined);
  const percent = metered && allowance
    ? Math.max(0, Math.min(100, Math.round((Number(allowance.used) / Number(allowance.limit)) * 100)))
    : 0;

  return (
    <View style={styles.benefitRow}>
      <Ionicons
        name={active ? "checkmark-circle" : held ? "ellipse-outline" : "lock-closed-outline"}
        size={18}
        color={active ? premiumTheme.gold : colors.muted}
      />
      <View style={styles.benefitBody}>
        <View style={styles.benefitHead}>
          <Text style={[styles.benefitLabel, !active && styles.benefitLabelIdle]} numberOfLines={2}>
            {t(benefitCatalogKey(benefit.key), { defaultValue: benefit.label })}
          </Text>
          {benefit.beta ? (
            <View style={styles.betaChip}>
              <Text style={styles.betaChipText}>{t("premium:benefits.beta")}</Text>
            </View>
          ) : null}
        </View>
        {metered && allowance ? (
          <View style={styles.allowance}>
            <View style={styles.barTrack}>
              <View style={[styles.barFill, { backgroundColor: premiumTheme.gold, width: `${percent}%` }]} />
            </View>
            <Text style={styles.note}>
              {t("premium:benefits.allowance", {
                used: fmt.number(Number(allowance.used)),
                limit: fmt.number(Number(allowance.limit))
              })}
            </Text>
          </View>
        ) : null}
      </View>
    </View>
  );
}

/**
 * Granted but not yet enforced.
 *
 * Listing these as benefits would be a lie and hiding them would make the grant
 * look like one. They are shown, labelled "not yet", and never counted as
 * something the member is getting today.
 */
function NotYetSection({ items }: { items: Array<{ key: string; label: string; status: string }> }) {
  const { t } = useTranslation();
  if (!items.length) return null;
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t("premium:notYet.heading")}</Text>
      {items.map((item) => (
        <View key={item.key} style={styles.benefitRow}>
          <Ionicons name="time-outline" size={18} color={colors.muted} />
          <Text style={[styles.benefitLabel, styles.benefitLabelIdle]} numberOfLines={2}>
            {t(benefitCatalogKey(item.key), { defaultValue: item.label })}
          </Text>
        </View>
      ))}
      <Text style={styles.note}>{t("premium:notYet.note")}</Text>
    </View>
  );
}

/* -------------------------------------------------------------------------- *
 * Crypto Intelligence
 * -------------------------------------------------------------------------- */

/**
 * The premium crypto capabilities, named on the surface that sells them.
 *
 * This section used to be presentation only, on the premise that "both existing
 * plans already unlock every capability listed here". That premise silently
 * assumed the reader still had a plan. A member whose Premium had lapsed was
 * shown five rows with five forward chevrons — an invitation into capabilities
 * the server would then refuse — so the screen that exists to tell you what your
 * membership is was the one place that could not tell you it had ended.
 *
 * The rows now render from the same canonical answer every other gate reads
 * (`useCanonicalTier` → `tierSatisfies`), which is the server's resolver and
 * nothing else: not `is_premium` off this screen's own payload, not a stored
 * subscription row, not "was a member once". A locked row wears a closed lock
 * instead of a chevron and its tap goes to the plans above rather than into the
 * feature — the tap always does something, and what it does is honest.
 *
 * Deliberately not the whole story: the lock here is presentation. The capability
 * itself is held server-side (`services.crypto_premium_gate`), the destination
 * screens each mount `PremiumFeatureGate`, and the UNDX crypto tools resolve
 * entitlement before they execute. This row cannot be the only thing standing
 * between a lapsed member and a premium read, and it isn't.
 *
 * Copy lives in the `discovery:crypto` catalog namespace alongside the crypto
 * screens it describes; the locked note reuses `premium:gate.lockedBody`, the
 * same sentence the feature screens' own upsell shows, so the two surfaces
 * cannot drift into describing different products.
 */
type CryptoIntelligenceFeature = {
  key: "alerts" | "portfolio" | "watchlists" | "undx" | "marketPulse";
  icon: keyof typeof Ionicons.glyphMap;
  go?: (navigation: Props["navigation"]) => void;
  /**
   * Whether an inactive membership closes this row.
   *
   * Not every row in a Premium section is a Premium capability, and a padlock
   * on something a free member can already use is the same lie as a chevron on
   * something they cannot. So the flag is per row and mirrors what the server
   * actually refuses: the destinations marked `true` sit behind
   * `premium.crypto.intelligence` and render nothing without it, while the one
   * marked `false` is free up to a ceiling and owns its own upsell at the point
   * the ceiling bites.
   */
  premium: boolean;
};

const CRYPTO_INTELLIGENCE_FEATURES: readonly CryptoIntelligenceFeature[] = [
  { key: "alerts", premium: true, icon: "pulse-outline", go: (nav) => openDashboardRoute(nav, "/dashboard/crypto/alerts") },
  // The one row here that is not a Premium capability. `PortfolioScreen` gives
  // free and Premium members the identical valuation, prices and rows; Premium
  // only lifts a three-holding ceiling, and the server refuses the fourth add
  // rather than the screen. Locking this row would take away something a lapsed
  // member still has, and hide holdings they entered themselves.
  { key: "portfolio", premium: false, icon: "pie-chart-outline", go: (nav) => openDashboardRoute(nav, "/pulse/portfolio") },
  // Watchlists ships as `WatchlistsScreen` and is the thing alerts point at, so
  // leaving it off this list made the section describe a workflow it could not
  // start: a member could reach alerts and portfolio from here, but had to know
  // to go elsewhere for the lists those alerts watch.
  { key: "watchlists", premium: true, icon: "list-outline", go: (nav) => openDashboardRoute(nav, "/dashboard/crypto/watchlists") },
  // `UndxCapabilities` renders the server-authoritative capability registry, and
  // the crypto domain is registered in it — so this row advertises only what
  // UNDX can actually do right now, and can never claim a capability the server
  // has not published. The Command Center below opens the same destination.
  // Locked with the rest when the membership lapses, and the lock is honest
  // even though the destination is a general registry: what this row names is
  // UNDX *crypto* intelligence, and that is refused server-side by
  // `services/crypto_premium_gate` in both the tool path and the grounding
  // path. The registry screen itself stays open to direct navigation on
  // purpose — general UNDX is not what expired here.
  { key: "undx", premium: true, icon: "sparkles-outline", go: (nav) => nav.navigate("UndxCapabilities") },
  // Market Pulse is the one row here that opens live market data rather than a
  // member's own saved state, so it navigates directly instead of through the
  // dashboard resolver: there is no legacy web spelling of this screen to
  // reconcile, and routing a brand-new native screen through a string matcher
  // would only invent a way for it to miss. The screen it opens reads the same
  // canonical market service the rest of the product already polls.
  { key: "marketPulse", premium: true, icon: "stats-chart-outline", go: (nav) => nav.navigate("MarketPulse") }
];

export function CryptoIntelligenceSection({
  navigation, onUpgrade
}: { navigation: Props["navigation"]; onUpgrade: () => void }) {
  const { t } = useTranslation();
  const answer = useCanonicalTier();

  // Re-ask on foreground for the same reason `PremiumFeatureGate` does: an
  // expiry that happened while the app was backgrounded has to be discovered
  // when it comes back, not on the next cold start. The first read is already
  // `useCanonicalTier`'s job, so only the return trip is added here, and it
  // joins the same shared in-flight request every other gate uses.
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") void loadCanonicalTier();
    });
    return () => subscription.remove();
  }, []);

  // Only a *resolved* "no" locks a row. An unavailable answer is not a denial —
  // showing a paying member a padlock because a request failed is the failure
  // `canonicalTier` exists to prevent — so during an outage the rows stay
  // navigable and the destination's own gate renders the honest
  // "we couldn't confirm your membership" panel instead.
  const lapsed = answer.state === "resolved" && !tierSatisfies(answer, "PREMIUM");

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t("discovery:crypto.intelligence.heading")}</Text>
      {/* The standing subhead says these tools are "included with every plan",
          which is true of a plan and false of a lapsed one. Swapping the whole
          note is what keeps the heading from contradicting the padlocks under
          it. */}
      <Text style={styles.note}>
        {lapsed ? t("premium:gate.lockedBody") : t("discovery:crypto.intelligence.subhead")}
      </Text>
      {CRYPTO_INTELLIGENCE_FEATURES.map((feature) => (
        <CryptoIntelligenceRow
          key={feature.key}
          feature={feature}
          navigation={navigation}
          locked={lapsed && feature.premium}
          onUpgrade={onUpgrade}
        />
      ))}
    </View>
  );
}

function CryptoIntelligenceRow({
  feature, navigation, locked, onUpgrade
}: {
  feature: CryptoIntelligenceFeature;
  navigation: Props["navigation"];
  locked: boolean;
  onUpgrade: () => void;
}) {
  const { t } = useTranslation();
  const label = t(`discovery:crypto.intelligence.${feature.key}.label`);
  const hint = t(`discovery:crypto.intelligence.${feature.key}.hint`);

  const body = (
    <>
      <Ionicons
        name={locked ? "lock-closed" : feature.icon}
        size={18}
        color={locked ? colors.muted : premiumTheme.gold}
      />
      <View style={styles.benefitBody}>
        <View style={styles.benefitHead}>
          <Text
            style={[styles.benefitLabel, locked && styles.benefitLabelIdle]}
            numberOfLines={2}
          >
            {label}
          </Text>
          {/* A forward chevron is a promise that the tap opens the thing named
              beside it. Locked, that promise is false, so the affordance is the
              lock — the row is still pressable, but it says where it goes. */}
          {feature.go && !locked ? (
            <Ionicons name="chevron-forward" size={14} color={colors.muted} />
          ) : null}
        </View>
        <Text style={styles.note} numberOfLines={3}>{hint}</Text>
      </View>
    </>
  );

  if (locked) {
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityState={{ disabled: true }}
        accessibilityHint={t("premium:gate.lockedBody")}
        style={({ pressed }) => [styles.benefitRow, pressed && styles.pressed]}
        onPress={onUpgrade}
      >
        {body}
      </Pressable>
    );
  }

  if (feature.go) {
    const go = feature.go;
    return (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityHint={hint}
        style={({ pressed }) => [styles.benefitRow, pressed && styles.pressed]}
        onPress={() => go(navigation)}
      >
        {body}
      </Pressable>
    );
  }
  return <View style={styles.benefitRow}>{body}</View>;
}

/* -------------------------------------------------------------------------- *
 * Private Office entry
 * -------------------------------------------------------------------------- */

/**
 * The one link from Premium into Private Office.
 *
 * Unlike the crypto section above — which is presentation over features every
 * member already has — this row is gated, and the gate is not here. It renders
 * `/api/private-office/overview`'s `state`, which
 * `services/private_office/office.product_state` derived from the canonical
 * feature matrix:
 *
 *   ENTRY_AVAILABLE         something inside is built and reachable → tappable.
 *   ENTRY_UPGRADE_REQUIRED  something is built and out of reach → names the
 *                           tier, and still opens, because the destination
 *                           explains the situation better than a dead row.
 *   ENTRY_UNAVAILABLE       nothing inside is built → no row at all.
 *   ENTRY_UNKNOWN           we could not confirm → no row, and no claim.
 *
 * The screen does not know the tier ladder and must not learn it: the hierarchy
 * lives in `services/private_office/tiers.py`, and a copy here would be a second
 * authority that diverges the first time a capability changes rung.
 */
function PrivateOfficeEntrySection({ navigation }: { navigation: Props["navigation"] }) {
  const { t } = useTranslation();
  const [office, setOffice] = useState<PrivateOfficeProductState>(UNKNOWN_OFFICE);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const overview = await getPrivateOfficeOverview();
      if (!cancelled) setOffice(overview.office);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Absent, not disabled. An entry that cannot lead anywhere is worse than no
  // entry: it advertises a room and then refuses to open the door.
  if (office.state !== "ENTRY_AVAILABLE" && office.state !== "ENTRY_UPGRADE_REQUIRED") {
    return null;
  }

  const locked = office.state === "ENTRY_UPGRADE_REQUIRED";
  const label = t("premium:privateOffice.title");
  const hint = locked && office.upgradeTier
    ? t("premium:privateOffice.entry.lockedHint", { tier: office.upgradeTier })
    : t("premium:privateOffice.entry.hint");

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t("premium:privateOffice.entry.heading")}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        accessibilityHint={hint}
        style={({ pressed }) => [styles.benefitRow, pressed && styles.pressed]}
        onPress={() => navigation.navigate("PrivateOffice")}
      >
        <Ionicons
          name={locked ? "lock-closed-outline" : "briefcase-outline"}
          size={18}
          color={locked ? colors.muted : premiumTheme.gold}
        />
        <View style={styles.benefitBody}>
          <View style={styles.benefitHead}>
            <Text style={styles.benefitLabel} numberOfLines={2}>{label}</Text>
            <Ionicons name="chevron-forward" size={14} color={colors.muted} />
          </View>
          <Text style={styles.note} numberOfLines={3}>{hint}</Text>
        </View>
      </Pressable>
    </View>
  );
}

/* -------------------------------------------------------------------------- *
 * Actions
 * -------------------------------------------------------------------------- */

function ActionsSection({
  experience, busy, onManage, onRestore
}: {
  experience: PremiumExperience;
  busy: "" | "purchasing" | "restoring";
  onManage: () => void;
  onRestore: () => void;
}) {
  const { t } = useTranslation();
  // Manage is offered wherever a subscription may exist to manage — including
  // grace and hold, which are precisely the states where a member most needs to
  // reach Apple's billing screen.
  const canManage = experience !== "none";
  return (
    <View style={styles.section}>
      {canManage ? (
        <>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("premium:manage.action")}
            accessibilityHint={t("premium:manage.hint")}
            style={({ pressed }) => [styles.secondaryAction, pressed && styles.pressed]}
            onPress={onManage}
          >
            <Ionicons name="open-outline" size={16} color={premiumTheme.gold} />
            <Text style={styles.secondaryActionText}>{t("premium:manage.action")}</Text>
          </Pressable>
          <Text style={styles.note}>{t("premium:manage.note")}</Text>
        </>
      ) : null}

      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: Boolean(busy), busy: busy === "restoring" }}
        accessibilityLabel={t("premium:restore.action")}
        accessibilityHint={t("premium:restore.hint")}
        disabled={Boolean(busy)}
        style={({ pressed }) => [styles.secondaryAction, Boolean(busy) && styles.dimmed, pressed && styles.pressed]}
        onPress={onRestore}
      >
        {busy === "restoring" ? (
          <ActivityIndicator color={premiumTheme.gold} />
        ) : (
          <Ionicons name="refresh-outline" size={16} color={premiumTheme.gold} />
        )}
        <Text style={styles.secondaryActionText}>
          {busy === "restoring" ? t("premium:restore.working") : t("premium:restore.action")}
        </Text>
      </Pressable>
    </View>
  );
}

/* -------------------------------------------------------------------------- *
 * Command Center (member headquarters)
 * -------------------------------------------------------------------------- */

/**
 * Headquarters modules that are planned but not yet built. `usage` and
 * `recommended` left this list when the usage-center backend shipped — they now
 * render live from `GET /api/premium/usage-center` (see `UsageModule` /
 * `RecommendedModule`). The rest stay here, inert, until a backend measures them.
 */
const COMMAND_MODULES = ["activity", "valueRecap", "achievements", "unlocked"] as const;

/**
 * A Premium space tile.
 *
 * `open` is the honesty switch: a space is `open` ONLY when tapping it lands on a
 * real, already-shipped destination route. Everything else is `comingNext` and
 * is deliberately not pressable — there is no screen behind it yet, so making it
 * look tappable would be a lie. `founderOnly` tiles render for founders alone.
 *
 * The `go` navigator reuses routes that already exist in the app. Verified
 * Identity opens the EXISTING verification flow with the identity track; it does
 * not, and cannot, grant the verified check from here — that stays with review.
 */
type SpaceKey =
  | "verified" | "identity" | "media" | "undx" | "creator" | "founder" | "storage" | "support" | "labs";

type CommandSpace = {
  key: SpaceKey;
  icon: keyof typeof Ionicons.glyphMap;
  open: boolean;
  founderOnly?: boolean;
  go?: (navigation: Props["navigation"]) => void;
};

const COMMAND_SPACES: readonly CommandSpace[] = [
  // Open — each lands on a route that already ships in the app today.
  { key: "verified", icon: "shield-checkmark-outline", open: true,
    go: (nav) => nav.navigate("VerificationCenter", { track: "identity" }) },
  { key: "undx", icon: "sparkles-outline", open: true,
    go: (nav) => nav.navigate("UndxCapabilities") },
  { key: "creator", icon: "color-wand-outline", open: true,
    go: (nav) => nav.navigate("CreatorStudio") },
  { key: "support", icon: "help-buoy-outline", open: true,
    go: (nav) => nav.navigate("TrustSafetyHelp") },
  // Coming next — no destination exists yet, so these are not pressable.
  { key: "identity", icon: "person-circle-outline", open: false },
  { key: "media", icon: "film-outline", open: false },
  { key: "storage", icon: "cloud-upload-outline", open: false },
  { key: "founder", icon: "diamond-outline", open: false, founderOnly: true },
  { key: "labs", icon: "flask-outline", open: false },
];

/**
 * The member's Premium headquarters.
 *
 * Two kinds of thing live here. The `modules` block is a roadmap — planned
 * headquarters widgets that carry a NEXT chip and nothing else, because the
 * backend measures none of them yet. The `spaces` grid is a mix: tiles marked
 * Open lead to real, shipped screens and are pressable; tiles marked Next have
 * no destination and stay inert. Nothing here fabricates a number, streak or
 * metric, and nothing tappable leads anywhere that does not already exist.
 *
 * Shown to members only. On the sales surface (`none`/`expired`) the plans are
 * the story; a roadmap of member-only rooms would just be noise before purchase.
 */
function CommandCenterSection({
  experience,
  held,
  usage,
  navigation
}: {
  experience: PremiumExperience;
  held: boolean;
  usage: PremiumUsageCenter | null;
  navigation: Props["navigation"];
}) {
  const { t } = useTranslation();
  if (!held && experience !== "founder") return null;
  const spaces = COMMAND_SPACES.filter((space) => !space.founderOnly || experience === "founder");
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t("premium:commandCenter.heading")}</Text>
      <Text style={styles.note}>{t("premium:commandCenter.subhead")}</Text>

      <UsageModule usage={usage} />
      <RecommendedModule usage={usage} />

      {COMMAND_MODULES.map((key) => (
        <View key={key} style={styles.benefitRow}>
          <Ionicons name="sparkles-outline" size={18} color={colors.muted} />
          <View style={styles.benefitBody}>
            <View style={styles.benefitHead}>
              <Text style={[styles.benefitLabel, styles.benefitLabelIdle]} numberOfLines={1}>
                {t(`premium:commandCenter.modules.${key}.label`)}
              </Text>
              <View style={styles.nextChip}>
                <Text style={styles.nextChipText}>{t("premium:commandCenter.comingChip")}</Text>
              </View>
            </View>
            <Text style={styles.note} numberOfLines={2}>
              {t(`premium:commandCenter.modules.${key}.hint`)}
            </Text>
          </View>
        </View>
      ))}

      <Text style={[styles.sectionTitle, styles.commandSubheading]}>{t("premium:commandCenter.spacesHeading")}</Text>
      <Text style={styles.note}>{t("premium:commandCenter.spacesSubhead")}</Text>
      <View style={styles.spacesGrid}>
        {spaces.map((space) => (
          <SpaceCard key={space.key} space={space} navigation={navigation} />
        ))}
      </View>

      <Text style={styles.note}>{t("premium:commandCenter.note")}</Text>
    </View>
  );
}

/**
 * "Your Premium this month" — the live usage module.
 *
 * Renders ONLY what the server measured. Each row is a signal whose value was
 * counted from the owning domain table at request time (`provenance:
 * "live_counts"`); a source the server could not measure never appears. While
 * the fetch is in flight or failed, the module renders nothing at all — no
 * skeleton with fake zeros, no NEXT chip pretending it is unbuilt.
 */
function UsageModule({ usage }: { usage: PremiumUsageCenter | null }) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  if (!usage || !usage.usage.signals.length) return null;
  return (
    <View style={styles.benefitRow}>
      <Ionicons name="pulse-outline" size={18} color={premiumTheme.gold} />
      <View style={styles.benefitBody}>
        <View style={styles.benefitHead}>
          <Text style={styles.benefitLabel} numberOfLines={1}>
            {t("premium:commandCenter.usage.monthTitle")}
          </Text>
          <View style={styles.activeChip}>
            <Text style={styles.activeChipText}>{t("premium:commandCenter.usage.liveChip")}</Text>
          </View>
        </View>
        {usage.usage.signals.map((signal) => (
          <Text key={signal.key} style={styles.note} numberOfLines={1}>
            {t(`premium:commandCenter.usage.signals.${signal.key}`, { defaultValue: signal.label })}
            {"  ·  "}
            {signal.kind === "count"
              ? fmt.number(Number(signal.value || 0)) +
                (typeof signal.free_limit === "number" && !signal.beyond_free_limit
                  ? ` / ${fmt.number(signal.free_limit)}`
                  : "")
              : signal.in_use && typeof signal.value === "string"
                ? signal.value
                : t("premium:commandCenter.usage.notSet")}
          </Text>
        ))}
        {usage.usage.omitted.length ? (
          <Text style={styles.note} numberOfLines={2}>
            {t("premium:commandCenter.usage.omittedNote")}
          </Text>
        ) : null}
      </View>
    </View>
  );
}

/**
 * Unused-benefit discovery. Every row was derived server-side from a signal the
 * member can see in the usage module above — never speculative, members only.
 * A member using everything sees an "all used" line rather than invented tips.
 */
function RecommendedModule({ usage }: { usage: PremiumUsageCenter | null }) {
  const { t } = useTranslation();
  if (!usage) return null;
  const recommendations = usage.usage.recommendations;
  return (
    <View style={styles.benefitRow}>
      <Ionicons name="compass-outline" size={18} color={premiumTheme.gold} />
      <View style={styles.benefitBody}>
        <View style={styles.benefitHead}>
          <Text style={styles.benefitLabel} numberOfLines={1}>
            {t("premium:commandCenter.modules.recommended.label")}
          </Text>
        </View>
        {recommendations.length ? (
          recommendations.map((rec) => (
            <View key={`${rec.capability}:${rec.reason}`} style={styles.recommendationRow}>
              <Text style={styles.benefitLabel} numberOfLines={1}>
                {t(`premium:commandCenter.usage.reasons.${rec.reason}.title`, { defaultValue: rec.title })}
              </Text>
              <Text style={styles.note} numberOfLines={2}>
                {t(`premium:commandCenter.usage.reasons.${rec.reason}.body`, { defaultValue: rec.body })}
              </Text>
            </View>
          ))
        ) : (
          <Text style={styles.note}>{t("premium:commandCenter.usage.allUsed")}</Text>
        )}
      </View>
    </View>
  );
}

/**
 * One space tile. Pressable only when the space is Open AND has a destination;
 * otherwise a plain, inert card with a Next chip. The readiness chip text comes
 * from the catalog (Open / Next), never from a hardcoded status here.
 */
function SpaceCard({ space, navigation }: { space: CommandSpace; navigation: Props["navigation"] }) {
  const { t } = useTranslation();
  const label = t(`premium:commandCenter.spaces.${space.key}.label`);
  const hint = t(`premium:commandCenter.spaces.${space.key}.hint`);
  const chipLabel = space.open
    ? t("premium:commandCenter.readiness.active")
    : t("premium:commandCenter.readiness.comingNext");

  const body = (
    <>
      <View style={styles.spaceCardHead}>
        <Ionicons name={space.icon} size={18} color={space.open ? premiumTheme.gold : colors.muted} />
        <Text style={[styles.spaceCardTitle, !space.open && styles.benefitLabelIdle]} numberOfLines={1}>
          {label}
        </Text>
        <View style={space.open ? styles.activeChip : styles.nextChip}>
          <Text style={space.open ? styles.activeChipText : styles.nextChipText}>{chipLabel}</Text>
        </View>
      </View>
      <Text style={styles.note} numberOfLines={3}>{hint}</Text>
    </>
  );

  if (space.open && space.go) {
    const go = space.go;
    return (
      <Pressable
        style={({ pressed }) => [styles.spaceCard, styles.spaceCardOpen, pressed && styles.pressed]}
        onPress={() => go(navigation)}
        accessibilityRole="button"
        accessibilityLabel={label}
      >
        {body}
      </Pressable>
    );
  }
  return <View style={styles.spaceCard}>{body}</View>;
}

/**
 * What Premium is not.
 *
 * Stated on the paid surface on purpose: the free product is the product, and a
 * member deciding whether to pay is owed the list of things they already have.
 */
function FreeCoreSection() {
  const { t } = useTranslation();
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t("premium:free.heading")}</Text>
      <Text style={styles.body}>{t("premium:free.body")}</Text>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  screen: { backgroundColor: colors.background, flex: 1 },
  content: { gap: 14, padding: 14, paddingBottom: 40 },
  center: { alignItems: "center", backgroundColor: colors.background, flex: 1, gap: 10, justifyContent: "center", padding: 24 },
  centerText: { color: colors.muted, fontSize: 14, textAlign: "center" },
  retry: {
    borderColor: premiumTheme.goldBorder,
    borderRadius: premiumTheme.radius.chip,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    minHeight: premiumTheme.tapTarget,
    paddingHorizontal: 18
  },
  retryText: { color: premiumTheme.gold, fontSize: 14, fontWeight: "600" },

  hero: {
    backgroundColor: premiumTheme.surface,
    borderColor: colors.border,
    borderRadius: premiumTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 16
  },
  heroGold: { backgroundColor: premiumTheme.goldSoft, borderColor: premiumTheme.goldBorder },
  heroTop: { alignItems: "center", flexDirection: "row", gap: 12 },
  heroCrest: {
    alignItems: "center",
    backgroundColor: premiumTheme.goldSoft,
    borderColor: premiumTheme.goldBorder,
    borderRadius: premiumTheme.radius.tile,
    borderWidth: StyleSheet.hairlineWidth,
    height: 46,
    justifyContent: "center",
    width: 46
  },
  heroBody: { flex: 1, gap: 3 },
  heroTitle: { color: colors.text, fontSize: 20, fontWeight: "700" },
  heroStatus: { fontSize: 12, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase" },
  heroFounder: { color: premiumTheme.gold, fontSize: 13, fontWeight: "700" },
  heroCaption: { color: colors.muted, fontSize: 13, lineHeight: 19 },

  flash: {
    borderRadius: premiumTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    padding: 12
  },
  flashText: { fontSize: 13, fontWeight: "600" },

  section: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: premiumTheme.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    padding: 14
  },
  sectionTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  inlineLoading: { alignItems: "center", flexDirection: "row", gap: 10 },

  noticeRow: { alignItems: "flex-start", flexDirection: "row", gap: 8 },
  noticeText: { color: colors.text, flex: 1, fontSize: 13, lineHeight: 19 },

  planRow: { alignItems: "stretch", flexDirection: "row", gap: 10 },
  plan: {
    borderColor: colors.border,
    borderRadius: premiumTheme.radius.tile,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    gap: 3,
    minHeight: 96,
    padding: 12
  },
  planSelected: { backgroundColor: premiumTheme.goldSoft, borderColor: premiumTheme.goldBorder, borderWidth: 1 },
  planHead: { alignItems: "center", flexDirection: "row", gap: 6 },
  planRibbon: {
    alignSelf: "flex-start",
    backgroundColor: premiumTheme.gold,
    borderRadius: premiumTheme.radius.chip,
    marginBottom: 2,
    paddingHorizontal: 8,
    paddingVertical: 2
  },
  planRibbonText: {
    color: colors.background,
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.5,
    textTransform: "uppercase"
  },
  planName: { color: colors.muted, flex: 1, fontSize: 12, fontWeight: "700", letterSpacing: 0.4, textTransform: "uppercase" },
  planNameSelected: { color: premiumTheme.gold },
  planPrice: { color: colors.text, fontSize: 20, fontWeight: "700" },
  planPeriod: { color: colors.muted, fontSize: 12 },
  planSave: {
    alignSelf: "flex-start",
    backgroundColor: premiumTheme.goldSoft,
    borderColor: premiumTheme.goldBorder,
    borderRadius: premiumTheme.radius.chip,
    borderWidth: StyleSheet.hairlineWidth,
    marginTop: 4,
    paddingHorizontal: 8,
    paddingVertical: 2
  },
  planSaveText: { color: premiumTheme.gold, fontSize: 11, fontWeight: "700" },

  factRow: { alignItems: "center", flexDirection: "row", gap: 10, justifyContent: "space-between" },
  // No fixed width or height anywhere in this row: at the largest Dynamic Type
  // sizes the label and value need to be free to grow and wrap rather than clip.
  factLabelGroup: { alignItems: "center", flexDirection: "row", flexShrink: 1, gap: 7 },
  factLabel: { color: colors.muted, fontSize: 13, flexShrink: 1 },
  factValue: { color: colors.text, flex: 1, fontSize: 13, fontWeight: "600", textAlign: "right" },
  /** Placeholder while StoreKit answers. Sized in ems so it tracks type size. */
  factSkeleton: {
    backgroundColor: colors.border, borderRadius: 4, height: 12, opacity: 0.5, width: 62
  },

  benefitRow: { alignItems: "flex-start", flexDirection: "row", gap: 10, paddingVertical: 4 },
  benefitBody: { flex: 1, gap: 6 },
  benefitHead: { alignItems: "center", flexDirection: "row", gap: 8 },
  benefitLabel: { color: colors.text, flex: 1, fontSize: 13, fontWeight: "600" },
  benefitLabelIdle: { color: colors.muted, fontWeight: "400" },
  betaChip: {
    borderColor: colors.border,
    borderRadius: premiumTheme.radius.chip,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 7,
    paddingVertical: 1
  },
  betaChipText: { color: colors.muted, fontSize: 10, fontWeight: "700", letterSpacing: 0.4, textTransform: "uppercase" },
  nextChip: {
    backgroundColor: premiumTheme.goldSoft,
    borderColor: premiumTheme.goldBorder,
    borderRadius: premiumTheme.radius.chip,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 7,
    paddingVertical: 1
  },
  nextChipText: { color: premiumTheme.gold, fontSize: 10, fontWeight: "700", letterSpacing: 0.4, textTransform: "uppercase" },
  commandSubheading: { marginTop: 6 },
  spacesGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  spaceCard: {
    borderColor: colors.border,
    borderRadius: premiumTheme.radius.tile,
    borderWidth: StyleSheet.hairlineWidth,
    flexBasis: "47%",
    flexGrow: 1,
    gap: 6,
    minWidth: "47%",
    padding: 12
  },
  spaceCardOpen: { backgroundColor: premiumTheme.goldSoft, borderColor: premiumTheme.goldBorder },
  spaceCardHead: { alignItems: "center", flexDirection: "row", gap: 8 },
  spaceCardTitle: { color: colors.text, flex: 1, fontSize: 13, fontWeight: "700" },
  activeChip: {
    backgroundColor: premiumTheme.gold,
    borderRadius: premiumTheme.radius.chip,
    paddingHorizontal: 7,
    paddingVertical: 1
  },
  activeChipText: { color: colors.background, fontSize: 10, fontWeight: "700", letterSpacing: 0.4, textTransform: "uppercase" },
  allowance: { gap: 4 },
  recommendationRow: { gap: 2, marginTop: 4 },
  barTrack: { backgroundColor: colors.surfaceRaised, borderRadius: premiumTheme.radius.chip, height: 6, overflow: "hidden", width: "100%" },
  barFill: { borderRadius: premiumTheme.radius.chip, height: 6 },

  primaryAction: {
    alignItems: "center",
    backgroundColor: premiumTheme.gold,
    borderRadius: premiumTheme.radius.chip,
    flexDirection: "row",
    gap: 8,
    justifyContent: "center",
    minHeight: premiumTheme.tapTarget,
    paddingHorizontal: 16
  },
  primaryActionText: { color: colors.background, fontSize: 14, fontWeight: "700" },
  secureRow: { alignItems: "center", flexDirection: "row", gap: 6, justifyContent: "center" },
  secondaryAction: {
    alignItems: "center",
    borderColor: premiumTheme.goldBorder,
    borderRadius: premiumTheme.radius.chip,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 8,
    justifyContent: "center",
    minHeight: premiumTheme.tapTarget,
    paddingHorizontal: 16
  },
  secondaryActionText: { color: premiumTheme.gold, fontSize: 13, fontWeight: "700" },
  dimmed: { opacity: 0.5 },
  pressed: { opacity: 0.7 },

  body: { color: colors.text, fontSize: 13, lineHeight: 19 },
  note: { color: colors.muted, fontSize: 11, lineHeight: 16 },
  footnote: { color: colors.muted, fontSize: 11, paddingHorizontal: 4, textAlign: "center" }
}));

export default PremiumCenterScreen;

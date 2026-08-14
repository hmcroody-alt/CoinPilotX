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
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import {
  getPremiumCenter,
  premiumExperience,
  type PremiumBenefit,
  type PremiumCenter,
  type PremiumExperience,
  type PremiumSubscription
} from "../api/premiumCenter";
import { useFormatters, useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import {
  annualSavings,
  getPremiumOffers,
  openManageSubscriptions,
  purchasePremium,
  restorePremiumPurchases,
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

export function PremiumCenterScreen({ route }: Props) {
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

  /** `premium_plan_viewed` is a funnel step, not a render count. Fire it once. */
  const planViewed = useRef(false);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    setError("");
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      setCenter(await getPremiumCenter());
    } catch (loadError) {
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

  const experience: PremiumExperience = premiumExperience(center);
  // Only surfaces that can actually sell need Apple's catalog. A Founder or an
  // active member is not shown prices, so their screen never touches StoreKit.
  const sells = experience === "none" || experience === "expired";

  useEffect(() => {
    if (!sells || offers || offersLoading) return;
    let cancelled = false;
    setOffersLoading(true);
    getPremiumOffers()
      .then((next) => {
        if (cancelled) return;
        setOffers(next);
        if (next.plans.length && !planViewed.current) {
          planViewed.current = true;
          trackPremium("premium_plan_viewed", {
            plans: next.plans.length,
            savings_percent: next.annualSavingsPercent
          });
        }
      })
      .catch(() => { if (!cancelled) setOffers({ plans: [], annualSavingsPercent: null }); })
      .finally(() => { if (!cancelled) setOffersLoading(false); });
    return () => { cancelled = true; };
  }, [sells, offers, offersLoading]);

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
    await load("refresh");
  }, [busy, load, t]);

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
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} tintColor={premiumTheme.gold} />
      }
    >
      <HeaderSection center={center} experience={experience} />

      {flash ? <FlashBanner flash={flash} /> : null}

      <NoticesSection center={center} />

      {sells ? (
        <PlansSection
          offers={offers}
          loading={offersLoading}
          plan={plan}
          onPlan={choosePlan}
          busy={busy === "purchasing"}
          disabled={Boolean(busy)}
          onPurchase={onPurchase}
          expired={experience === "expired"}
        />
      ) : (
        <BillingSection subscription={center?.subscription ?? null} experience={experience} />
      )}

      <BenefitsSection benefits={center?.benefits || []} held={Boolean(center?.membership.is_premium)} />

      <NotYetSection items={center?.not_yet || []} />

      <ActionsSection
        experience={experience}
        busy={busy}
        onManage={onManage}
        onRestore={onRestore}
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
  const tone = premiumTheme.state[experience === "founder" || experience === "active" ? experience : experience === "grace" ? "grace" : experience === "hold" ? "hold" : "none"];

  const heading =
    experience === "founder"
      ? t("premium:header.founder")
      : experience === "active" || experience === "grace" || experience === "hold"
        ? t("premium:header.member")
        : t("premium:header.free");

  return (
    <View style={[styles.hero, (experience === "founder" || experience === "active") && styles.heroGold]}>
      <View style={styles.heroTop}>
        <View style={styles.heroCrest}>
          <Ionicons name={experience === "founder" ? "diamond" : "diamond-outline"} size={26} color={premiumTheme.gold} />
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

function PlansSection({
  offers, loading, plan, onPlan, busy, disabled, onPurchase, expired
}: {
  offers: PremiumOffers | null;
  loading: boolean;
  plan: PremiumPlan;
  onPlan: (plan: PremiumPlan) => void;
  busy: boolean;
  disabled: boolean;
  onPurchase: () => void;
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
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t("premium:plans.heading")}</Text>
        <Text style={styles.body}>{t("premium:plans.unavailable")}</Text>
        <Text style={styles.note}>{t("premium:plans.unavailableBody")}</Text>
      </View>
    );
  }

  const selected = plans.find((offer) => offer.plan === plan) || plans[0];

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t(expired ? "premium:plans.headingAgain" : "premium:plans.heading")}</Text>
      <View style={styles.planRow}>
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
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ selected }}
      accessibilityLabel={`${label}, ${offer.displayPrice}`}
      style={({ pressed }) => [styles.plan, selected && styles.planSelected, pressed && styles.pressed]}
      onPress={onPress}
    >
      <Text style={[styles.planName, selected && styles.planNameSelected]}>{label}</Text>
      <Text style={styles.planPrice}>{offer.displayPrice}</Text>
      <Text style={styles.planPeriod}>{t(`premium:plans.per.${offer.plan}`)}</Text>
      {/* Computed from the two localized prices actually returned. Absent when
          there is no honest figure — never a hardcoded "SAVE 17%". */}
      {savings !== null ? (
        <View style={styles.planSave}>
          <Text style={styles.planSaveText}>{t("premium:plans.save", { percent: savings })}</Text>
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
function BillingSection({
  subscription, experience
}: { subscription: PremiumSubscription | null; experience: PremiumExperience }) {
  const { t } = useTranslation();
  const fmt = useFormatters();

  if (!subscription) {
    return (
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>{t("premium:billing.heading")}</Text>
        {/* A Founder holds canonical Premium with no provider subscription
            behind it. "No billing record" is the truthful answer, not an error. */}
        <Text style={styles.body}>
          {experience === "founder" ? t("premium:billing.founderNone") : t("premium:billing.none")}
        </Text>
      </View>
    );
  }

  const endDate = subscription.current_period_end ? fmt.date(subscription.current_period_end) : "";
  const endLabel = subscription.cancel_at_period_end ? t("premium:billing.expires") : t("premium:billing.renews");

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{t("premium:billing.heading")}</Text>
      <Fact
        label={t("premium:billing.plan")}
        value={t(`premium:period.${subscription.billing_period || "unknown"}`, {
          defaultValue: subscription.billing_period || "—"
        })}
      />
      <Fact
        label={t("premium:billing.provider")}
        value={t(`premium:provider.${subscription.provider || "unknown"}`, {
          defaultValue: subscription.provider || "—"
        })}
      />
      <Fact
        label={t("premium:billing.status")}
        value={t(`premium:subStatus.${subscription.status || "unknown"}`, {
          defaultValue: subscription.status || "—"
        })}
      />
      {endDate ? <Fact label={endLabel} value={endDate} /> : null}
      {subscription.cancel_at_period_end ? (
        <Text style={styles.note}>{t("premium:billing.cancelPending")}</Text>
      ) : null}
    </View>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.factRow} accessibilityLabel={`${label}: ${value}`}>
      <Text style={styles.factLabel}>{label}</Text>
      <Text style={styles.factValue}>{value}</Text>
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

  planRow: { flexDirection: "row", gap: 10 },
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
  planName: { color: colors.muted, fontSize: 12, fontWeight: "700", letterSpacing: 0.4, textTransform: "uppercase" },
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
  factLabel: { color: colors.muted, fontSize: 13 },
  factValue: { color: colors.text, flex: 1, fontSize: 13, fontWeight: "600", textAlign: "right" },

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
  allowance: { gap: 4 },
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

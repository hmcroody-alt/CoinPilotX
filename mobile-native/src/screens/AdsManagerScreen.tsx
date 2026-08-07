/**
 * Advertising — the two-sided ads manager.
 *
 * One screen, two ad products, switched by the header ModeToggle without a
 * navigation push:
 *
 *   • MARKETPLACE ADS (gold = money) is fully backed. Ad accounts, campaigns,
 *     analytics and the wallet come from the live `/api/pulse/ads/*` surface via
 *     `loadAdsMarketplace`. Every money figure is read from the server and
 *     formatted for the locale — nothing is computed here and presented as
 *     truth, and no new payment path is created: "Add funds" opens the existing
 *     `BusinessOsPayments` wallet screen.
 *
 *   • POST ADS (violet = content promotion) is an unbacked preview behind
 *     `EXPO_PUBLIC_ADS_POST_MODE`. With the flag off the mode says the product
 *     is coming rather than inventing promotions; with it on every figure is
 *     tagged MOCK-DATA and visibly labelled Preview. It never shows a
 *     fabricated balance — the one real wallet chip funds both modes.
 *
 * Both panes stay mounted (the inactive one is `display: "none"`) so each keeps
 * its own scroll position across a swap, and the wallet chip lives on the header
 * so it is rendered once from one balance object and cannot disagree with
 * itself between modes.
 *
 * The screen computes almost nothing. Phases, tabs, budgets, KPI inputs and —
 * critically — whether a pause switch may be pressed all come from pure
 * functions in `api/adsDashboard`, so "a switch that silently no-ops" is
 * prevented in one testable place rather than in each call site.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Animated, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  AdCampaign,
  AdCampaignAction,
  availableAdCampaignActions,
  formatObjective,
  requestAdAccountVerification,
  runAdCampaignAction
} from "../api/businessOs";
import {
  AdsMarketplaceModel,
  AdsMode,
  CampaignTabKey,
  adAccountDisplay,
  adAccountStanding,
  adCampaignDisplay,
  adsKpis,
  adsPostModeEnabled,
  blockedCampaigns,
  campaignBudget,
  campaignMetricsAreLive,
  campaignPhase,
  campaignPhaseLabel,
  campaignPhaseTone,
  campaignSpendCents,
  campaignTabs,
  deliverySwitchState,
  filterCampaigns,
  loadAdsMarketplace,
  loadMockPostKpis,
  loadMockPostPromotions,
  loadMockRecentPosts,
  loadMockSuggestion,
  promotionPhaseLabel,
  promotionPhaseTone,
  promotionSwitchState,
  spendChartWeekdays
} from "../api/adsDashboard";
import {
  AdsCampaignSkeleton,
  AdsChartSkeleton,
  AdsEmpty,
  AdsHeader,
  AdsKpiSkeleton,
  AdsOfflineNote,
  AdsPreviewNote,
  AdsPromotionSkeleton,
  AdsSectionError,
  AdsTabBar,
  AdsVerificationBanner,
  AdsWalletUnavailable,
  AdsZeroBalanceBanner,
  CampaignCard,
  type CampaignCardAction,
  type CampaignCardMetric,
  PromoteRail,
  type PromoteRailItem,
  PromotedPostCard,
  ACCOUNT_SPEND_TITLE,
  SEVEN_DAY_SPEND_TITLE,
  SpendBarChart,
  SuggestionCard
} from "../components/ads";
import { policyCenterModel } from "../api/adsPolicy";
import { creativeLibraryModel } from "../api/adsCreatives";
import {
  accountVerificationState,
  attributionNote,
  deliveryState,
  deliveryStateDetail,
  deliveryStateLabel,
  deliveryStateTone,
  resumeCheck,
  walletAuthority
} from "../api/adsDelivery";
import type { AdsPortal } from "../api/adsPortal";
import { StoreKpiCard, StoreQuickLinkGrid } from "../components/store";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { registerSyncInvalidation } from "../core/eventSync";
import { useFormatters } from "../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { adsLight } from "../theme/adsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance, STORE_STAGGER_MS } from "../theme/storeMotion";
import { absentValueText, type SurfaceState } from "../api/stateLanguage";

const MODE_CACHE_KEY = "ads.lastMode.v1";

const ACTION_LABELS: Record<AdCampaignAction, string> = {
  pause: "Pause",
  resume: "Resume",
  archive: "Archive",
  duplicate: "Duplicate",
  submit: "Submit for review",
  complete: "Mark complete"
};

/**
 * Entrance slots in the order the design choreographs them. Named so a section
 * cannot silently animate out of order when one is inserted.
 */
const SLOT = {
  header: 0,
  banners: 1,
  kpis: 2,
  chart: 3,
  tabs: 4,
  list: 5,
  tools: 6,
  cta: 7
} as const;
const SECTION_COUNT = Object.keys(SLOT).length;

type Props = {
  route?: { params?: RootStackParamList["BusinessOsAdvertising"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function AdsManagerScreen({ route, navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const entrance = useStoreEntrance(SECTION_COUNT, reducedMotion);
  const postEnabled = useMemo(() => adsPostModeEnabled(), []);

  const [model, setModel] = useState<AdsMarketplaceModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<AdsMode>("marketplace");
  const [tab, setTab] = useState<CampaignTabKey>("active");
  const [busyKey, setBusyKey] = useState("");
  const [message, setMessage] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [suggestionDismissed, setSuggestionDismissed] = useState(false);

  /**
   * Guards the delivery switch against a double tap. A second press while the
   * first request is in flight must not send a second action — the backend
   * transitions are idempotent, but two rapid pause/resume calls would race and
   * leave the switch showing the loser.
   */
  const inFlight = useRef<Set<number>>(new Set());

  const load = useCallback(async (kind: "initial" | "refresh" = "initial") => {
    if (kind === "initial") setLoading(true);
    try {
      setModel(await loadAdsMarketplace());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const refresh = () => load("refresh").catch(() => undefined);
    // Verification approval is the one external event that changes what this
    // screen can do (a blocked campaign becomes deliverable); marketplace
    // covers listing boosts, which are campaigns in the same ledger.
    const unregister = [
      registerSyncInvalidation("verification", refresh),
      registerSyncInvalidation("marketplace", refresh)
    ];
    return () => unregister.forEach((fn) => fn());
  }, [load]);

  // Restore the last mode. Post is only restored when the preview flag is on, so
  // a build without the flag never lands the advertiser on an empty product.
  useEffect(() => {
    let active = true;
    readJsonCache<{ mode: AdsMode }>(MODE_CACHE_KEY, (value) => value)
      .then((cached) => {
        if (!active || !cached) return;
        if (cached.mode === "post" && !postEnabled) return;
        if (cached.mode === "post" || cached.mode === "marketplace") setMode(cached.mode);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [postEnabled]);

  const changeMode = useCallback((next: AdsMode) => {
    setMode(next);
    setMessage("");
    writeJsonCache(MODE_CACHE_KEY, { mode: next }).catch(() => undefined);
  }, []);

  /* -------------------------------------------------------------- *
   * Navigation — every money destination is an existing screen
   * -------------------------------------------------------------- */

  const openWallet = useCallback(() => {
    const accountId = model?.wallet?.accountId ?? model?.primaryAccount?.id;
    navigation?.navigate("BusinessOsPayments", { title: "Ad wallet", accountId });
  }, [navigation, model]);

  /**
   * Ask for the *ad account* to be reviewed.
   *
   * This used to navigate to the Verification Center, which posts to
   * `/api/dashboard/account/verification/request` and decides a profile badge.
   * `select_ads` reads `pulse_ad_accounts.status`, which that flow never
   * touches — so the advertiser could complete everything the Verification
   * Center asked for, wait for it to be approved, and still not deliver a
   * single impression. The button was live, it navigated somewhere real, and
   * finishing what it asked changed nothing about the thing it was named after.
   *
   * `requestAdAccountVerification` writes the record the selector actually
   * reads. Its refusals — not the owner, already verified, already in review —
   * are the server's sentences, shown as they come back.
   */
  const requestVerification = useCallback(async () => {
    const accountId = model?.primaryAccount?.id;
    if (!accountId) return;
    if (model?.offline) {
      setMessage("You're offline. Reconnect to request verification.");
      return;
    }
    if (verifying) return;
    setVerifying(true);
    setMessage("");
    try {
      await requestAdAccountVerification(accountId);
      setMessage("Verification requested. We'll tell you as soon as it's decided.");
      await load("refresh");
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Verification couldn't be requested. Try again."
      );
    } finally {
      setVerifying(false);
    }
  }, [load, model, verifying]);

  const openReports = useCallback(() => {
    navigation?.navigate("BusinessOsInsights", { title: "Ad reports" });
  }, [navigation]);

  /**
   * Campaign creation, the ad-account form and the objective/budget editor all
   * live in the classic Advertising screen. Routing there reuses them instead of
   * building a second creation path that could diverge from the first.
   */
  const openClassic = useCallback(
    (title: string) => {
      navigation?.navigate("BusinessOsAdvertising", { title, mode: "classic" });
    },
    [navigation]
  );

  /**
   * The sub-pages. Same route name, different `mode` — see `AdvertisingRoute`.
   * Audiences and Creative library were locked tiles that could not be opened;
   * Account details is where the ad account number lives now that it is out of
   * the header.
   */
  const openAudiences = useCallback(() => {
    navigation?.navigate("BusinessOsAdvertising", { title: "Audiences", mode: "audiences" });
  }, [navigation]);

  const openCreatives = useCallback(() => {
    navigation?.navigate("BusinessOsAdvertising", {
      title: "Creative library",
      mode: "creatives"
    });
  }, [navigation]);

  const openPolicy = useCallback(() => {
    navigation?.navigate("BusinessOsAdvertising", { title: "Policy Center", mode: "policy" });
  }, [navigation]);

  const openAccountDetails = useCallback(
    (accountId?: number) => {
      navigation?.navigate("BusinessOsAdvertising", {
        title: "Account details",
        mode: "account",
        accountId
      });
    },
    [navigation]
  );

  /* -------------------------------------------------------------- *
   * Actions
   * -------------------------------------------------------------- */

  const applyAction = useCallback(
    async (campaign: AdCampaign, action: AdCampaignAction) => {
      if (model?.offline) {
        setMessage("You're offline. Reconnect to change campaign delivery.");
        return;
      }
      if (inFlight.current.has(campaign.id)) return;
      inFlight.current.add(campaign.id);
      setBusyKey(`campaign-${campaign.id}`);
      setMessage("");
      try {
        const result = await runAdCampaignAction(campaign.id, action);
        // An unnamed campaign used to make this read "Pause applied to ." The
        // display helper is the same one the strip uses, so the sentence names
        // the campaign the same way the list does.
        setMessage(
          result.message ||
            `${ACTION_LABELS[action]} applied to ${adCampaignDisplay(campaign).name}.`
        );
        await load("refresh");
      } catch (error) {
        setMessage(
          error instanceof Error ? error.message : `${ACTION_LABELS[action]} could not be applied.`
        );
      } finally {
        inFlight.current.delete(campaign.id);
        setBusyKey("");
      }
    },
    [load, model]
  );

  /* -------------------------------------------------------------- *
   * Derived
   * -------------------------------------------------------------- */

  const campaigns = model?.campaigns || [];
  const account = model?.primaryAccount || null;
  const kpis = useMemo(
    () => adsKpis({ analytics: model?.analytics || null, campaigns }),
    [model?.analytics, campaigns]
  );
  const tabs = useMemo(() => campaignTabs(campaigns, account), [campaigns, account]);
  const blocked = useMemo(() => blockedCampaigns(campaigns, account), [campaigns, account]);
  const visible = useMemo(() => filterCampaigns(campaigns, tab), [campaigns, tab]);

  const weekdayIndices = useMemo(() => spendChartWeekdays(), []);
  const dayLabels = useMemo(
    () => weekdayIndices.map((index) => formatters.weekdayNames("narrow")[index] || ""),
    [weekdayIndices, formatters]
  );

  const currency = model?.wallet?.currency || "USD";
  const money = useCallback(
    (cents: number) => formatters.currency(cents / 100, { currency }),
    [formatters, currency]
  );

  const offline = Boolean(model?.offline);
  const walletFailed = !loading && Boolean(account) && !model?.wallet;

  /**
   * The empty state's second action, which only exists when there is something
   * for it to do. An account already in review has no move to offer, and a
   * verified account has nothing left to ask for — in both cases the old
   * unconditional "Verify your business" was a button whose only possible
   * outcome was the server explaining why it wouldn't work.
   */
  const verificationIsRequestable = useMemo(() => {
    if (!account) return false;
    const state = accountVerificationState(account);
    return state === "unverified" || state === "rejected";
  }, [account]);

  const emptyStateVerifyLabel = useMemo(() => {
    if (!model?.needsVerification || !verificationIsRequestable || !account) return null;
    return accountVerificationState(account) === "rejected"
      ? "Request review again"
      : "Request verification";
  }, [model?.needsVerification, verificationIsRequestable, account]);

  /**
   * What the empty state says about delivery.
   *
   * The verification banner only renders alongside blocked campaigns, so on an
   * account with no campaigns at all this body is the *only* thing standing
   * between the advertiser and a silent gate. It used to be one sentence —
   * "Delivery begins once PulseSoc approves your business" — printed for every
   * blocked state. That is accurate while a review is pending and untrue in the
   * other two: a declined account is waiting on the advertiser, not on us, and
   * a verified account that isn't marked active has already been approved, so
   * telling them to wait for approval sends them to wait for something that has
   * already happened. Each state now says what is actually true of it, and the
   * decline carries its recorded reason because §37 requires the policy reason
   * to be reachable and this is the only surface that renders here.
   */
  const emptyStateBody = useMemo(() => {
    const draftIsFree = "Nothing is charged while a campaign is a draft.";
    if (!model?.needsVerification) {
      return `Campaigns you create appear here with their delivery status, spend and pacing. ${draftIsFree}`;
    }
    const start = "Start a campaign now — drafts cost nothing and aren't charged.";
    const reason = String(
      (account as { verification_reason?: string } | null)?.verification_reason || ""
    ).trim();
    switch (accountVerificationState(account || {})) {
      case "pending":
        return `${start} Delivery begins once your account review is decided — nothing is charged while you wait.`;
      case "rejected":
        return reason
          ? `${start} Your account review was declined: ${reason} Update your business details, then request review again.`
          : `${start} Your account review was declined, and no reason was recorded with the decision. Check your business details, then request review again.`;
      case "verified":
        return `${start} This account is verified but isn't marked active, so nothing can deliver yet. Contact support so it can be corrected.`;
      default:
        return `${start} Delivery begins once this ad account is verified, so you can get that going in parallel.`;
    }
  }, [model?.needsVerification, account]);

  /**
   * Whether the balance on this screen is a figure the server stood behind.
   *
   * `portal_summary` wraps every `wallet_summary` call in a bare `except` and,
   * on failure, appends a hand-written row of zeroes carrying pre-formatted
   * `"$0.00"` strings, which it then sums into `metrics`
   * (services/pulse_advertiser_portal.py:440–470). `wallet_summary` begins with
   * `_owner_account`, so it raises for every non-owner — a campaign manager
   * with full write access takes the substituted path on every single request.
   *
   * The substituted row is indistinguishable from a real empty wallet inside
   * the payload; the role is the only thing that separates them. So the balance
   * is repeated for owners and replaced with "Restricted" for everyone else,
   * rather than being recomputed on the client, which §37 forbids outright.
   *
   * The doubt is scoped to the portal, and only to the portal, because
   * `model.wallet` has two origins and only one of them can lie. The portal path
   * lifts a row out of `portal.wallets` (adsDashboard.ts:747–753) — the rollup
   * described above. The fan-out path calls
   * `GET /api/pulse/ads/accounts/<id>/wallet` directly and shows no chip at all
   * when that call fails (adsDashboard.ts:825–831), so a balance surviving that
   * path is the server's own answer to a question about one account. Printing
   * "Restricted" over it would be manufacturing a doubt the payload doesn't
   * carry — the same offence as the fabricated zero, committed in the other
   * direction.
   *
   * It is scoped to the chip's own account rather than to the rollup for the
   * same reason: the chip shows one wallet, and a non-owned *sibling* account
   * says nothing about whether this one's figure is real.
   */
  const walletAccountId = Number(model?.wallet?.accountId || account?.id || 0);
  const walletTruth = model?.portal ? walletAuthority(model.portal, walletAccountId) : null;
  const balanceConfirmed = !walletTruth || walletTruth.state === "confirmed";

  /**
   * A "you're out of money" banner fired by a fabricated zero would tell a team
   * member their campaigns are about to stop when the account is fully funded.
   * It only fires on a balance the server actually computed.
   */
  const zeroBalance =
    balanceConfirmed &&
    Boolean(model?.wallet) &&
    (model?.wallet?.balanceCents || 0) <= 0 &&
    campaigns.some((campaign) => deliveryState(model?.portal ?? null, campaign) === "delivering");

  /*
   * The debt, and the reason the banner cannot wait for a delivering campaign.
   *
   * `zeroBalance` fires only while something is still trying to spend, which is
   * right for an account that is merely empty — nothing has stopped yet, so
   * nothing needs explaining. An overdrawn account is the opposite case: the
   * reversal handler pauses every campaign it can no longer fund, so by the
   * time the advertiser opens this screen there is nothing delivering and the
   * one condition that would have surfaced the banner is already false. The
   * debt would then be visible nowhere at all.
   */
  const owedLabel = balanceConfirmed ? walletTruth?.owedDisplay ?? null : null;
  const showBalanceBanner = zeroBalance || Boolean(owedLabel);

  /*
   * `null`, not `"—"`, while the wallet is loading.
   *
   * The chip decides what an absent balance reads as; a caller that hands it a
   * placeholder string is deciding for it, and the string it used to hand over
   * was the one glyph §31 rules out. Passing null says "no figure yet" and
   * leaves the wording in the one place that owns it.
   */
  const walletProp = loading
    ? { balanceLabel: null, fundingLive: false, loading: true }
    : model?.wallet
    ? {
        balanceLabel:
          balanceConfirmed || !walletTruth ? model.wallet.balanceLabel : walletTruth.display,
        fundingLive: model.wallet.fundingLive,
        loading: false
      }
    : null;

  /**
   * The spend card names the window it can actually report.
   *
   * `spend.windowed` is false whenever there is no per-day source — which, on
   * this backend, is always: `/api/pulse/ads/analytics` takes an account id and
   * no date range, so its total is lifetime. The card therefore titles itself
   * "Account spend" and its summary says "to date". The heading and the figure
   * agree, which is the whole requirement; "Spend · last 7 days" over a
   * lifetime total was a false report in the direction that hurts, because an
   * advertiser reading $0.00 under a weekly heading concludes their delivery
   * stopped this week rather than never started.
   */
  const spend = model?.spend;
  const spendEmpty = !spend || spend.daysCents.length === 0;
  const spendWindowed = Boolean(spend?.windowed) && !spendEmpty;
  const spendTotalLabel = money(spend?.totalCents || 0);
  const spendTitle = spendWindowed ? SEVEN_DAY_SPEND_TITLE : ACCOUNT_SPEND_TITLE;
  const spendSummary = spendWindowed
    ? `Daily spend, last seven days. ${spendTotalLabel} spent in that period.`
    : `Account spend. ${spendTotalLabel} spent to date. A day-by-day view isn't available yet.`;

  /* -------------------------------------------------------------- *
   * Marketplace mode
   * -------------------------------------------------------------- */

  /**
   * The account name, with its number gone from the strip entirely.
   *
   * Three versions of this row have now existed. The first read
   * `{business_name || "Ad account"} · Ad account {id}`, which put a database
   * key in the most prominent text on the screen and, for an account with no
   * name, said "Ad account · Ad account 8" — the same phrase twice, one of them
   * a number. The second put the name on its own line and demoted the key to a
   * quieter second line, behind `EXPO_PUBLIC_ACCOUNT_NAME_FIRST`; because that
   * flag defaults off, production kept rendering the first version.
   *
   * This is the third and the flag is gone. Two things were wrong with the
   * second: it shipped the fix switched off, and the fix was only half of one —
   * a quieter database key is still a database key, and the line under an
   * account name is the most valuable line on the strip. It now says whether
   * the account can run ads, which is the question the reader actually has.
   *
   * The number still exists and is still reachable. It belongs in account
   * details, support information and the audit log; `adAccountDisplay` remains
   * the way to render it there, and is used here only to name the account.
   */
  const accountLabel = adAccountDisplay(account, { accountCount: model?.accounts.length ?? 0 });
  const accountStanding = adAccountStanding(account);
  const accountStrip = account ? (
    <View style={styles.accountStrip}>
      {/* The dot is decorative — it repeats the line beside it, and it takes
          its colour from the same decision, so it cannot say "healthy" over a
          "Restricted" account. */}
      <View
        style={[styles.accountDot, { backgroundColor: adsLight.status[accountStanding.tone] }]}
        accessibilityElementsHidden
        importantForAccessibility="no"
      />
      {/* Tapping the name opens account details. That is where the account
          number went when it left this strip, so the identity row is still the
          way to reach it — one tap further away, rather than removed. */}
      <Pressable
        style={styles.accountTextGroup}
        onPress={() => openAccountDetails(account.id)}
        accessibilityRole="button"
        accessibilityLabel={`${accountLabel.name}. ${accountStanding.line}. Open account details.`}
      >
        <Text style={styles.accountName} numberOfLines={1}>
          {accountLabel.name}
        </Text>
        <Text style={styles.accountReference} numberOfLines={1}>
          {accountStanding.line}
        </Text>
      </Pressable>
      {model && model.accounts.length > 1 ? (
        <Pressable
          onPress={() => openClassic("Ad accounts")}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel="Switch ad account"
        >
          <Text style={styles.accountAction}>Switch</Text>
        </Pressable>
      ) : null}
    </View>
  ) : null;

  const marketplaceKpis = (() => {
    if (loading) {
      return (
        <View style={styles.kpiRow}>
          <AdsKpiSkeleton reducedMotion={reducedMotion} />
          <AdsKpiSkeleton reducedMotion={reducedMotion} />
          <AdsKpiSkeleton reducedMotion={reducedMotion} />
        </View>
      );
    }
    if (model?.analyticsStatus === "error") {
      return (
        <AdsSectionError
          message="Spend and clicks didn't load."
          onRetry={() => load("refresh")}
          reducedMotion={reducedMotion}
        />
      );
    }
    return (
      <View style={styles.kpiRow}>
        {/* "Spend · to date" and "Clicks · to date" — the tile label is one
            line by design (`StoreKpiCard` sets `numberOfLines={1}`) and three
            tiles share the row, so the middle dot cost the characters that made
            the label a sentence: the first rendered as "Spend · to da…". The
            window still has to be stated, because these totals are lifetime and
            an unqualified "Spend" would read as this week's. Dropping the
            separator keeps both facts and fits. */}
        <StoreKpiCard
          label="Spend to date"
          value={money(kpis.spendCents)}
          // MOCK-DATA guard: the analytics endpoint takes no date range, so
          // these totals are lifetime. Labelling them "· 7d" would misreport a
          // real number, so the label states the window the data actually has.
          caption={kpis.hasDailyBudget ? `of ${money(kpis.dailyBudgetCents)} daily budgets` : null}
          onPress={openReports}
          destinationHint="ad reports"
          reducedMotion={reducedMotion}
          delay={SLOT.kpis * STORE_STAGGER_MS}
        />
        <StoreKpiCard
          label="Clicks to date"
          value={formatters.count(kpis.clicks)}
          caption={
            kpis.impressions > 0 ? `${formatters.count(kpis.impressions)} impressions` : null
          }
          onPress={openReports}
          destinationHint="ad reports"
          reducedMotion={reducedMotion}
          delay={SLOT.kpis * STORE_STAGGER_MS}
        />
        <StoreKpiCard
          label="Cost per click"
          /**
           * No clicks means the figure is undefined, not zero — a cost per
           * click of nothing would be a claim that the clicks were free.
           *
           * What it must not be is a dash. This read `absentValueTextOr("—",
           * …)`, and because `EXPO_PUBLIC_STATE_LANGUAGE` defaults off, the
           * production build showed the em dash — the one character that means
           * "zero", "loading", "failed" and "not set up" all at once, which is
           * the ambiguity `api/stateLanguage.ts` exists to end. The state is
           * known here with certainty, so the wording is stated outright rather
           * than deferred to a rollout flag.
           */
          value={
            kpis.cpcCents == null
              ? absentValueText("no_activity", { notConfiguredText: "No clicks yet" })
              : money(kpis.cpcCents)
          }
          // MOCK-DATA guard: no prior period exists to compare against, so no
          // tile shows a trend arrow — including the "▼ cheaper" treatment the
          // design specifies here.
          caption={kpis.cpcCents == null ? "No clicks yet" : null}
          onPress={openReports}
          destinationHint="ad reports"
          reducedMotion={reducedMotion}
          delay={SLOT.kpis * STORE_STAGGER_MS}
        />
      </View>
    );
  })();

  const campaignList = (() => {
    if (loading) {
      return (
        <View style={styles.stack}>
          <AdsCampaignSkeleton reducedMotion={reducedMotion} />
          <AdsCampaignSkeleton reducedMotion={reducedMotion} />
        </View>
      );
    }
    if (model?.campaignsStatus === "error") {
      return (
        <AdsSectionError
          message="Your campaigns didn't load."
          onRetry={() => load("refresh")}
          reducedMotion={reducedMotion}
        />
      );
    }
    if (!model?.accounts.length) {
      return (
        <AdsEmpty
          title="Create your ad account"
          body="An ad account is how PulseSoc verifies and bills your advertising. New accounts start unverified and can't deliver until they're approved."
          ctaLabel="Create ad account"
          onPress={() => openClassic("Create ad account")}
          reducedMotion={reducedMotion}
        />
      );
    }
    if (!campaigns.length) {
      // The unverified account is folded into this one invitation rather than
      // stacking a verification banner above an empty state: with nothing to
      // deliver, verification is a step of setting up, not a warning.
      //
      // Both paths are offered at once, always. This block used to *choose*:
      // an unverified advertiser was shown "Verify your business" and no way
      // to start a campaign, which enforced a rule the platform does not have.
      // Verification gates delivery; it does not gate authoring. A draft
      // charges nothing and delivers nothing, so there is no state in which
      // making one is unsafe — and an advertiser waiting on document review is
      // precisely who should be drafting. The primary action is therefore the
      // work they can do now, and verification is the quieter second path that
      // unblocks delivery later.
      return (
        <AdsEmpty
          title="No campaigns yet"
          body={emptyStateBody}
          ctaLabel="Create campaign"
          onPress={() => openClassic("Create campaign")}
          secondaryLabel={emptyStateVerifyLabel}
          onSecondaryPress={emptyStateVerifyLabel ? requestVerification : undefined}
          reducedMotion={reducedMotion}
        />
      );
    }
    if (!visible.length) {
      return (
        <View style={styles.noMatches}>
          <Text style={styles.noMatchesText}>Nothing in this tab right now.</Text>
        </View>
      );
    }
    return (
      <View style={styles.stack}>
        {visible.map((campaign) => {
          /**
           * The pill used to read `campaignPhase`, which maps `status='active'`
           * straight to "Delivering". The selector wants seven more conditions
           * than that (`api/adsDelivery.ts` lists them against the SQL), so an
           * advertiser whose ad account was never approved — which is every
           * self-serve advertiser, because no route sets that column — saw a
           * green pill on a campaign that has never reached one person.
           *
           * `deliveryState` reads the gates the payload can actually see and
           * splits the green in two: "Delivering" only once `spent_cents` proves
           * money moved, "Ready to deliver" while it is still a forecast.
           */
          const delivery = deliveryState(model?.portal ?? null, campaign);
          const phase = campaignPhase(campaign);
          const budget = campaignBudget(campaign);
          const switchState = deliverySwitchState(campaign, account);
          /**
           * Resume reserves budget, and `reserve_campaign_budget` is owner-only:
           * a campaign manager who taps it is told "Campaign not found." about a
           * campaign they are looking at. Checked before the switch is offered
           * rather than discovered after.
           */
          const resume = resumeCheck(model?.portal ?? null, campaign);
          const resumeBlocked = !switchState.on && !resume.allowed;
          const spentCents = campaignSpendCents(campaign, model?.analytics || null);
          const row = model?.analytics?.campaigns.find(
            (entry) => Number(entry.campaign_id) === Number(campaign.id)
          );
          const clicks = Number(row?.clicks || 0);
          /**
           * The dash on these three rows meant two different things and there
           * was no way to tell which from the card: the analytics call failed,
           * or this campaign has not run yet and there is nothing to report.
           * The first is a fault worth retrying, the second is a new campaign
           * behaving normally.
           */
          const metricState = (): SurfaceState => {
            if (!model?.analytics) return "unavailable";
            return row ? "ready" : "no_activity";
          };
          /**
           * The absent branches say the words rather than draw the dash.
           *
           * These were `absentValueTextOr(…, "—", …)`, which meant the correct
           * wording only appeared in a build that had opted into
           * `EXPO_PUBLIC_STATE_LANGUAGE`. Off by default, so the shipping app
           * rendered the ambiguous character on all three rows — exactly the
           * state this screen's own comment above says it is closing.
           */
          const cpcCents =
            row && clicks > 0 && Number(row.estimated_cpc || 0) > 0
              ? Math.round(Number(row.estimated_cpc) * 100)
              : null;
          const metrics: CampaignCardMetric[] = [
            { key: "spent", label: "Spent", value: money(spentCents) },
            {
              key: "impressions",
              label: "Impressions",
              value: row
                ? formatters.count(Number(row.impressions || 0))
                : absentValueText(metricState(), { zeroText: formatters.count(0) })
            },
            {
              key: "clicks",
              label: "Clicks",
              value: row
                ? formatters.count(clicks)
                : absentValueText(metricState(), { zeroText: formatters.count(0) })
            },
            {
              key: "cpc",
              label: "CPC",
              // A campaign with no clicks has no cost per click — that is an
              // undefined figure, not a zero one. So a missing CPC is never
              // "ready": either the analytics call failed, or there is nothing
              // to divide by yet.
              value:
                cpcCents == null
                  ? absentValueText(!model?.analytics ? "unavailable" : "no_activity")
                  : money(cpcCents)
            }
          ];
          // Pause and resume belong to the switch; everything else the server
          // will accept becomes a secondary action, so no button offers a
          // transition the backend would reject.
          const actions: CampaignCardAction[] = availableAdCampaignActions(campaign)
            .filter((entry) => entry !== "pause" && entry !== "resume")
            .map((entry) => ({
              key: `${campaign.id}-${entry}`,
              label: ACTION_LABELS[entry],
              onPress: () => applyAction(campaign, entry)
            }));

          return (
            <CampaignCard
              key={campaign.id}
              name={adCampaignDisplay(campaign).name}
              reference={adCampaignDisplay(campaign).reference}
              objectiveLabel={formatObjective(campaign.objective)}
              phase={delivery}
              phaseLabel={deliveryStateLabel(delivery)}
              phaseTone={deliveryStateTone(delivery)}
              phaseDetail={deliveryStateDetail(model?.portal ?? null, campaign)}
              budget={
                budget
                  ? {
                      spentLabel: money(budget.spentCents),
                      budgetLabel:
                        budget.type === "daily"
                          ? `${money(budget.budgetCents)} daily budget`
                          : `${money(budget.budgetCents)} boost total`,
                      fraction: budget.fraction,
                      hot: budget.hot
                    }
                  : null
              }
              metrics={metrics}
              // Spend is only presented as moving when it demonstrably is.
              metricsLive={campaignMetricsAreLive(campaign) && delivery === "delivering"}
              metricsNote={attributionNote(campaign)}
              showSwitch={switchState.show}
              delivering={switchState.on}
              switchDisabled={switchState.disabled || offline || resumeBlocked}
              switchReason={
                offline && !switchState.disabled
                  ? "You're offline — delivery can't be changed."
                  : switchState.reason || (resumeBlocked ? resume.reason : null)
              }
              onToggleDelivery={(next) => {
                if (!switchState.action) return;
                if (next && !resume.allowed) return;
                applyAction(campaign, next ? "resume" : "pause").catch(() => undefined);
              }}
              toggleBusy={busyKey === `campaign-${campaign.id}`}
              blockedVerification={blocked.some((entry) => entry.id === campaign.id)}
              onVerify={verificationIsRequestable ? requestVerification : undefined}
              actions={actions}
              onPress={() => openClassic(campaign.campaign_name || "Campaign")}
              reducedMotion={reducedMotion}
            />
          );
        })}
      </View>
    );
  })();

  const marketplaceBody = (
    <ScrollView
      style={mode === "marketplace" ? undefined : styles.hidden}
      contentContainerStyle={[styles.content, { paddingBottom: bottomPad(insets.bottom) }]}
      showsVerticalScrollIndicator={false}
    >
      {offline ? (
        <AdsOfflineNote text="Offline — showing your saved advertising. Delivery changes and new campaigns are unavailable until you reconnect." />
      ) : null}

      <Animated.View style={[styles.stack, entrance.styleFor(SLOT.banners)]}>
        {/* Wallet outranks verification: a campaign that can't be paid for stops
            sooner than one waiting on approval, so it is stated first. Both are
            shown when both are true — neither is suppressed by the other. */}
        {showBalanceBanner && model?.wallet ? (
          <AdsZeroBalanceBanner
            fundingLive={model.wallet.fundingLive}
            onAddFunds={openWallet}
            reducedMotion={reducedMotion}
            owedLabel={owedLabel}
          />
        ) : null}
        {walletFailed ? (
          <AdsWalletUnavailable onRetry={() => load("refresh")} reducedMotion={reducedMotion} />
        ) : null}
        {blocked.length ? (
          <AdsVerificationBanner
            campaignName={blocked.length === 1 ? blocked[0].campaign_name || null : null}
            state={accountVerificationState(account || {})}
            reason={(account as { verification_reason?: string } | null)?.verification_reason || null}
            submitting={verifying}
            onVerify={requestVerification}
            reducedMotion={reducedMotion}
          />
        ) : null}
      </Animated.View>

      <Animated.View style={entrance.styleFor(SLOT.kpis)}>{marketplaceKpis}</Animated.View>

      {model?.primaryAccount || loading ? (
        <Animated.View style={entrance.styleFor(SLOT.chart)}>
          {loading ? (
            <AdsChartSkeleton reducedMotion={reducedMotion} />
          ) : (
            <Pressable
              onPress={openReports}
              accessibilityRole="button"
              accessibilityLabel="Open ad reports"
            >
              <SpendBarChart
                title={spendTitle}
                values={spend?.daysCents || []}
                dayLabels={dayLabels}
                summary={spendSummary}
                mock={Boolean(spend?.mock)}
                empty={spendEmpty}
                totalLabel={spendTotalLabel}
                reducedMotion={reducedMotion}
                seriesKey={spend?.totalCents ?? 0}
              />
            </Pressable>
          )}
        </Animated.View>
      ) : null}

      {campaigns.length ? (
        <>
          <Animated.View style={[styles.sectionHead, entrance.styleFor(SLOT.tabs)]}>
            <Text style={styles.sectionTitle}>Campaigns</Text>
            <Pressable
              onPress={() => openClassic("All campaigns")}
              hitSlop={8}
              accessibilityRole="button"
              accessibilityLabel={`Manage all campaigns, ${campaigns.length}`}
            >
              <Text style={styles.sectionAction}>Manage all ({campaigns.length})</Text>
            </Pressable>
          </Animated.View>

          <Animated.View style={entrance.styleFor(SLOT.tabs)}>
            <AdsTabBar
              tabs={tabs}
              active={tab}
              onChange={(key) => setTab(key as CampaignTabKey)}
              reducedMotion={reducedMotion}
            />
          </Animated.View>
        </>
      ) : null}

      <Animated.View style={[styles.section, entrance.styleFor(SLOT.list)]}>
        {campaignList}
      </Animated.View>

      <Animated.View style={[styles.section, entrance.styleFor(SLOT.tools)]}>
        <Text style={styles.sectionTitle}>Tools</Text>
        {/* Two per row, laid out by the grid. These four used to sit in a
            wrapping row and took a quarter of the width each, which is how
            "Audiences" rendered as "A…" and "Creative library" as "Cr…".
            "Wallet & billing" and "Creative library" are the longest labels in
            the surface, so this is where the defect showed first. */}
        <View style={styles.toolGrid}>
        <StoreQuickLinkGrid
          reducedMotion={reducedMotion}
          items={[
            {
              icon: "wallet-outline",
              label: "Wallet & billing",
              subtitle: model?.wallet
                ? model.wallet.balanceLabel
                : walletFailed
                  ? "Tap to retry"
                  : "Ad wallet",
              onPress: openWallet,
              reducedMotion
            },
            {
              icon: "bar-chart-outline",
              label: "Reports",
              subtitle: spendTotalLabel + " spent to date",
              onPress: openReports,
              reducedMotion
            },
            // Both of these used to be `disabled: true` with the subtitle "Not
            // available in the app yet". Accurate, and still a dead end: the
            // reader was told "no" in the one place that should have told them
            // what the feature is and what to do meanwhile.
            //
            // Audiences remains a report, because targeting genuinely has no
            // editable endpoint here, and its subtitle says so before the tap
            // rather than after. The creative library is no longer a report —
            // it lists the real creatives — so its subtitle carries a count.
            //
            // The subtitle was "See what targeting applies", which promises a
            // list of applied rules. None applies: `pulse_ad_targeting` is
            // never written, so `_matches_targeting` passes every campaign for
            // every viewer. The tile now names what the page can actually
            // answer — where the ad runs — so the tap is not spent finding out
            // the question had no answer.
            {
              icon: "people-outline",
              label: "Audiences",
              subtitle: "Where your ads run",
              onPress: openAudiences,
              reducedMotion
            },
            {
              icon: "images-outline",
              label: "Creative library",
              subtitle: creativeTileSubtitle(model?.portal ?? null),
              onPress: openCreatives,
              reducedMotion
            },
            {
              icon: "shield-checkmark-outline",
              label: "Policy Center",
              subtitle: policyTileSubtitle(model?.portal ?? null),
              onPress: openPolicy,
              reducedMotion
            }
          ]}
        />
        </View>
      </Animated.View>

      {message && mode === "marketplace" ? (
        <Text style={styles.message} accessibilityLiveRegion="polite">
          {message}
        </Text>
      ) : null}

      {model?.primaryAccount ? (
        <Animated.View style={[styles.section, entrance.styleFor(SLOT.cta)]}>
          <Pressable
            onPress={() => openClassic("Create campaign")}
            disabled={offline}
            style={[styles.cta, offline && styles.ctaDisabled]}
            accessibilityRole="button"
            accessibilityLabel="Create campaign"
            accessibilityState={{ disabled: offline }}
          >
            <Ionicons name="add" size={18} color={adsLight.cta.text} />
            <Text style={styles.ctaText}>Create campaign</Text>
          </Pressable>
          <Text style={styles.footnote}>
            Campaigns start as drafts. Nothing is charged and nothing delivers until you submit for
            review.
          </Text>
        </Animated.View>
      ) : null}
    </ScrollView>
  );

  /* -------------------------------------------------------------- *
   * Post mode
   * -------------------------------------------------------------- */

  const suggestion = useMemo(() => loadMockSuggestion(), []);
  const promotions = useMemo(() => loadMockPostPromotions(), []);
  const postKpis = useMemo(() => loadMockPostKpis(), []);
  const recentPosts = useMemo(() => loadMockRecentPosts(), []);

  const railItems: PromoteRailItem[] = recentPosts.map((post) => ({
    id: post.id,
    contentType: post.contentType,
    title: post.title,
    reachLabel: `${formatters.count(post.reach)} reached`,
    hotLabel: post.hotMultiplier ? `${post.hotMultiplier}×` : null
  }));

  const previewOnly = useCallback(() => {
    setMessage("Promoting posts is a preview — nothing is submitted and nothing is charged.");
  }, []);

  const postBody = (
    <ScrollView
      style={mode === "post" ? undefined : styles.hidden}
      contentContainerStyle={[styles.content, { paddingBottom: bottomPad(insets.bottom) }]}
      showsVerticalScrollIndicator={false}
    >
      {/* The disclosure only appears when there is sample data to disclaim.
          With the flag off there are no figures on this page, and this note
          used to run anyway — directly above an empty state that said the same
          thing in different words. Two notices saying "not available" is not
          twice the honesty; it is the whole page spent on a refusal. The
          flag-off pane is now one card that explains the product instead. */}
      {postEnabled ? (
        <AdsPreviewNote text="Post ads is a preview. Every figure below is sample data — no promotion is running, nothing is submitted and nothing is charged. Your ad wallet above is real and funds Marketplace ads." />
      ) : null}

      {postEnabled && postKpis ? (
        <Animated.View style={[styles.kpiRow, entrance.styleFor(SLOT.kpis)]}>
          <StoreKpiCard
            label="Reach · preview"
            value={formatters.count(postKpis.reach)}
            reducedMotion={reducedMotion}
            delay={SLOT.kpis * STORE_STAGGER_MS}
          />
          <StoreKpiCard
            label="New followers · preview"
            value={formatters.count(postKpis.newFollowers)}
            caption={
              postKpis.costPerFollowerCents == null
                ? null
                : `${money(postKpis.costPerFollowerCents)} each`
            }
            reducedMotion={reducedMotion}
            delay={SLOT.kpis * STORE_STAGGER_MS}
          />
          <StoreKpiCard
            label="Engagements · preview"
            value={formatters.count(postKpis.engagements)}
            reducedMotion={reducedMotion}
            delay={SLOT.kpis * STORE_STAGGER_MS}
          />
        </Animated.View>
      ) : null}

      {postEnabled && suggestion && !suggestionDismissed ? (
        <Animated.View style={[styles.section, entrance.styleFor(SLOT.banners)]}>
          <SuggestionCard
            contentType={suggestion.contentType}
            title={suggestion.title}
            reason={suggestion.reason}
            onPromote={previewOnly}
            onDismiss={() => setSuggestionDismissed(true)}
            reducedMotion={reducedMotion}
          />
        </Animated.View>
      ) : null}

      <Animated.View style={[styles.section, entrance.styleFor(SLOT.list)]}>
        {!postEnabled ? (
          <View style={styles.stack}>
            <View style={styles.infoCard}>
              <Text style={styles.infoTitle}>Post ads isn’t switched on yet</Text>
              <Text style={styles.infoBody}>
                It will let you put money behind something you already posted, instead of
                building an ad from scratch. Nothing about it is live in this build — there is
                no promotion running, and nothing here can be charged.
              </Text>
              <View style={styles.infoPoints}>
                {[
                  "Promote a post, a Reel or a live replay",
                  "Paid from the same ad wallet as your Marketplace campaigns — there is no second balance to top up",
                  "Reviewed before it delivers, the same way a Marketplace ad is"
                ].map((point) => (
                  <View key={point} style={styles.infoPointRow}>
                    <View
                      style={styles.infoPointDot}
                      accessibilityElementsHidden
                      importantForAccessibility="no"
                    />
                    <Text style={styles.infoPointText}>{point}</Text>
                  </View>
                ))}
              </View>
            </View>

            <View style={styles.infoCard}>
              <Text style={styles.infoTitle}>What you can run today</Text>
              {/* Was "…deliver in the feed and in Reels". There is no Reels
                  placement: `PLACEMENTS` seeds twelve rows and none of them is
                  Reels, so this named one surface that does not exist while
                  omitting eleven that do — Marketplace, Search and Pulse Radio
                  among them. The Audiences page now lists the real set from the
                  portal, so this points there instead of guessing again. */}
              <Text style={styles.infoBody}>
                Marketplace ads are live and deliver across a dozen placements — the feed,
                Marketplace, search and more, listed under Audiences. They use the same wallet,
                so anything you add now is spendable the moment post promotion arrives.
              </Text>
              <Pressable
                onPress={() => changeMode("marketplace")}
                accessibilityRole="button"
                accessibilityLabel="Switch to Marketplace ads"
                hitSlop={6}
              >
                <Text style={styles.infoLink}>Go to Marketplace ads ›</Text>
              </Pressable>
              <Pressable
                onPress={openWallet}
                accessibilityRole="button"
                accessibilityLabel="Open the ad wallet"
                hitSlop={6}
              >
                <Text style={styles.infoLink}>Ad wallet and billing ›</Text>
              </Pressable>
            </View>
          </View>
        ) : loading ? (
          <View style={styles.stack}>
            <AdsPromotionSkeleton reducedMotion={reducedMotion} />
            <AdsPromotionSkeleton reducedMotion={reducedMotion} />
          </View>
        ) : !promotions.length ? (
          <AdsEmpty
            title="Nothing promoted yet"
            body="Promote a post and it appears here with its review status, reach and spend."
            ctaLabel={railItems.length ? "Promote a post" : null}
            onPress={railItems.length ? previewOnly : undefined}
            reducedMotion={reducedMotion}
            tone="post"
          />
        ) : (
          <View style={styles.stack}>
            {promotions.map((promotion) => {
              const switchState = promotionSwitchState(promotion);
              const budgetCents = Number(promotion.budgetCents || 0);
              const spentCents = Number(promotion.spendCents || 0);
              const known = promotion.phase === "promoting" || promotion.phase === "completed";
              return (
                <PromotedPostCard
                  key={promotion.id}
                  contentType={promotion.contentType}
                  title={promotion.title}
                  phase={promotion.phase}
                  phaseLabel={promotionPhaseLabel(promotion.phase)}
                  phaseTone={promotionPhaseTone(promotion.phase)}
                  /*
                   * Two cells, not four, and no dashes in either.
                   *
                   * This strip used to be Reach / Likes / Follows / Cost per 1k
                   * with a literal "—" in three of them. §31 prohibits the
                   * universal dash precisely because it flattens four unrelated
                   * facts into one glyph: "this hasn't started", "we don't
                   * collect this", "there's nothing to divide by" and "the
                   * request failed" are different things and the reader can act
                   * on only some of them.
                   *
                   * Likes and Follows are gone rather than reworded. They had no
                   * source at all — not an empty one, not a failing one, none —
                   * so a cell for them was a label promising a measurement that
                   * does not exist anywhere in the product. That is the same
                   * finding as conversions in Phase 4 and it gets the same
                   * answer: say it in a sentence, because the thing being
                   * reported is the absence of a number and a cell cannot say
                   * that without printing something under the word.
                   *
                   * The two that remain are real. `reach` is measured once the
                   * promotion delivers; before that "None yet" is the truth, not
                   * a placeholder. Cost per 1k is derived from it and cannot
                   * exist before reach does.
                   */
                  metrics={[
                    {
                      key: "reach",
                      label: "Reach",
                      value: known
                        ? formatters.count(Number(promotion.reach || 0))
                        : absentValueText("no_activity")
                    },
                    {
                      key: "cpm",
                      label: "Cost per 1k views",
                      value:
                        known && Number(promotion.reach || 0) > 0
                          ? money(Math.round((spentCents / Number(promotion.reach)) * 1000))
                          : absentValueText("no_activity")
                    }
                  ]}
                  metricsNote="Likes and follows from a promotion aren’t tracked."
                  pacing={
                    budgetCents > 0
                      ? {
                          spentLabel: money(spentCents),
                          budgetLabel: `${money(budgetCents)} boost total`,
                          fraction: Math.max(0, Math.min(1, spentCents / budgetCents)),
                          hot: spentCents / budgetCents >= 0.9
                        }
                      : null
                  }
                  showSwitch={switchState.show}
                  promoting={switchState.on}
                  onTogglePromotion={() => undefined}
                  switchDisabled={switchState.disabled}
                  switchReason={switchState.reason}
                  rejectionReason={promotion.rejectionReason}
                  onEdit={previewOnly}
                  onPress={previewOnly}
                  reducedMotion={reducedMotion}
                />
              );
            })}
          </View>
        )}
      </Animated.View>

      {postEnabled ? (
        <Animated.View style={[styles.section, entrance.styleFor(SLOT.tools)]}>
          <Text style={styles.sectionTitle}>Promote a recent post</Text>
          {railItems.length ? (
            <PromoteRail items={railItems} onPromote={previewOnly} reducedMotion={reducedMotion} />
          ) : (
            <AdsEmpty
              title="No recent posts"
              body="Post something and it'll show up here, ready to promote."
              reducedMotion={reducedMotion}
              tone="post"
            />
          )}
        </Animated.View>
      ) : null}

      {message && mode === "post" ? (
        <Text style={styles.message} accessibilityLiveRegion="polite">
          {message}
        </Text>
      ) : null}

      {postEnabled ? (
        <Animated.View style={[styles.section, entrance.styleFor(SLOT.cta)]}>
          <Pressable
            onPress={previewOnly}
            style={[styles.cta, styles.ctaPost]}
            accessibilityRole="button"
            accessibilityLabel="Promote a post, preview"
          >
            <Text style={styles.ctaPostText}>🚀 Promote a post</Text>
          </Pressable>
          <Text style={styles.footnote}>
            Preview only. Nothing is submitted for review and nothing is charged.
          </Text>
        </Animated.View>
      ) : null}
    </ScrollView>
  );

  /* -------------------------------------------------------------- *
   * Frame
   * -------------------------------------------------------------- */

  return (
    <View style={styles.root}>
      <Animated.View style={entrance.styleFor(SLOT.header)}>
        <AdsHeader
          title={route?.params?.title || "Advertising"}
          mode={mode}
          onChangeMode={changeMode}
          onBack={() => navigation?.goBack?.()}
          postIsPreview={!postEnabled}
          wallet={walletProp}
          onWallet={openWallet}
          reducedMotion={reducedMotion}
          below={accountStrip}
        />
      </Animated.View>

      {marketplaceBody}
      {postBody}
    </View>
  );
}

function bottomPad(inset: number) {
  return Math.max(inset, 16) + BOTTOM_NAV_CONTENT_CLEARANCE;
}

/**
 * The Policy Center tile's subtitle.
 *
 * Four distinct answers, because §31 treats them as four distinct claims:
 *
 *   • `null` portal — the board was never fetched, either because the page is
 *     still loading or because the manager fell back to the five-call fan-out.
 *     The subtitle names the destination and asserts nothing about its
 *     contents. It must not read as reassurance: "All clear" here would tell an
 *     advertiser with a rejected ad that nothing is wrong, on the strength of a
 *     request that never happened.
 *   • `empty` — the board loaded and holds nothing. A real zero, said plainly.
 *   • rejections outstanding — the only state worth a number on a tile, because
 *     it is the only one the reader has to act on.
 *   • everything else — pending decisions, or a board that is entirely
 *     approved.
 *
 * Counts are pluralised rather than rendered as "1 creatives", which is the
 * kind of seam that makes a surface read as unfinished.
 */
function policyTileSubtitle(portal: AdsPortal | null): string {
  const model = policyCenterModel(portal);

  if (model.state === "unavailable") return "See review decisions";
  if (model.state === "empty") return "No decisions yet";
  if (model.actionCount > 0) {
    return model.actionCount === 1 ? "1 needs attention" : `${model.actionCount} need attention`;
  }
  if (model.reviewCount > 0) {
    return model.reviewCount === 1 ? "1 in review" : `${model.reviewCount} in review`;
  }
  return "All clear";
}

/**
 * The Creative library tile's subtitle.
 *
 * Same four-way split as the policy tile and for the same §31 reason: a library
 * that was never fetched must not render as an empty one. "Browse your
 * creatives" names the destination and claims nothing about what's in it, which
 * is the only honest thing to say about a request that hasn't happened.
 *
 * The number it shows when there is one counts rejections and unsubmitted
 * drafts together — both mean a creative the advertiser meant to run isn't
 * running, which is the fact a tile has room for.
 */
function creativeTileSubtitle(portal: AdsPortal | null): string {
  const model = creativeLibraryModel(portal);

  if (model.state === "unavailable") return "Browse your creatives";
  if (model.state === "empty") return "No creatives yet";
  if (model.actionCount > 0) {
    return model.actionCount === 1 ? "1 needs attention" : `${model.actionCount} need attention`;
  }
  return "All delivering";
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: adsLight.bg.page },
  hidden: { display: "none" },
  content: { paddingTop: 12, gap: 14 },
  stack: { gap: 10, paddingHorizontal: adsLight.space.card },
  section: { gap: 10 },
  kpiRow: { flexDirection: "row", gap: 10, paddingHorizontal: adsLight.space.card },
  accountStrip: { flexDirection: "row", alignItems: "center", gap: 8 },
  // No colour here: the fill comes from `adAccountStanding().tone` at the call
  // site. A default would be a claim about an account this stylesheet has
  // never seen.
  accountDot: { width: 8, height: 8, borderRadius: 4 },
  // Two lines rather than one joined string: the weight difference is what makes
  // the status line secondary, and a single `Text` could not express it.
  accountTextGroup: { flex: 1 },
  accountName: { fontSize: 13, color: adsLight.text.onDark, fontWeight: "700" },
  accountReference: { fontSize: 11, color: adsLight.text.onDarkMuted, fontWeight: "600" },
  accountAction: { fontSize: 12, fontWeight: "800", color: adsLight.text.onDark },
  // The flag-off Post-ads pane. Violet-washed like the rest of the product, but
  // a plain card rather than an empty state: it is explaining a thing, not
  // apologising for one.
  infoCard: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: adsLight.space.card,
    gap: 8
  },
  infoTitle: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary, lineHeight: 20 },
  infoBody: { fontSize: 13, color: adsLight.text.muted, lineHeight: 19 },
  infoPoints: { gap: 6, marginTop: 2 },
  infoPointRow: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  infoPointDot: {
    width: 4,
    height: 4,
    borderRadius: 2,
    marginTop: 8,
    backgroundColor: adsLight.post.base
  },
  infoPointText: { flex: 1, fontSize: 13, color: adsLight.text.primary, lineHeight: 19 },
  infoLink: { fontSize: 13, fontWeight: "700", color: adsLight.post.base, paddingVertical: 4 },
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: adsLight.space.card
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: adsLight.text.primary,
    paddingHorizontal: adsLight.space.card
  },
  sectionAction: { fontSize: 13, fontWeight: "700", color: adsLight.chart.axis },
  /* Was `flexDirection: "row", flexWrap: "wrap"` around four `flex: 1` tiles,
     which gave each a quarter of the width and clipped "Audiences" to "A…".
     `StoreQuickLinkGrid` owns the rows now; this keeps only the inset. */
  toolGrid: { paddingHorizontal: adsLight.space.card },
  noMatches: { paddingHorizontal: adsLight.space.card, paddingVertical: 20 },
  noMatchesText: { fontSize: 13, color: adsLight.text.muted, textAlign: "center" },
  message: {
    paddingHorizontal: adsLight.space.card,
    fontSize: 12,
    color: adsLight.text.primary,
    lineHeight: 17
  },
  cta: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    marginHorizontal: adsLight.space.card,
    minHeight: adsLight.size.tapTarget,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.cta.from
  },
  ctaDisabled: { opacity: 0.5 },
  ctaText: { fontSize: 15, fontWeight: "800", color: adsLight.cta.text },
  ctaPost: { backgroundColor: adsLight.post.base },
  ctaPostText: { fontSize: 15, fontWeight: "800", color: adsLight.post.onViolet },
  footnote: {
    paddingHorizontal: adsLight.space.card,
    fontSize: 11,
    color: adsLight.text.muted,
    lineHeight: 15
  }
});

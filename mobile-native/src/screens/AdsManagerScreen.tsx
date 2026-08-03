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
  runAdCampaignAction
} from "../api/businessOs";
import {
  AdsMarketplaceModel,
  AdsMode,
  CampaignTabKey,
  accountNameFirstEnabled,
  adAccountDisplay,
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
  SpendBarChart,
  SuggestionCard
} from "../components/ads";
import { StoreKpiCard, StoreQuickLinkGrid } from "../components/store";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { registerSyncInvalidation } from "../core/eventSync";
import { useFormatters } from "../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { adsLight } from "../theme/adsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance, STORE_STAGGER_MS } from "../theme/storeMotion";
import { absentValueTextOr, type SurfaceState } from "../api/stateLanguage";

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

  const openVerification = useCallback(() => {
    navigation?.navigate("VerificationCenter", { title: "Verification Center", track: "business" });
  }, [navigation]);

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
  const zeroBalance =
    Boolean(model?.wallet) &&
    (model?.wallet?.balanceCents || 0) <= 0 &&
    campaigns.some((campaign) => campaignPhase(campaign) === "delivering");

  const walletProp = loading
    ? { balanceLabel: "—", fundingLive: false, loading: true }
    : model?.wallet
    ? {
        balanceLabel: model.wallet.balanceLabel,
        fundingLive: model.wallet.fundingLive,
        loading: false
      }
    : null;

  const spend = model?.spend;
  const spendEmpty = !spend || spend.daysCents.length === 0;
  const spendTotalLabel = money(spend?.totalCents || 0);
  const spendSummary = spendEmpty
    ? `Daily spend. ${spendTotalLabel} spent to date. A day-by-day view isn't available yet.`
    : `Daily spend, last seven days. ${spendTotalLabel} spent to date, shown as a preview distribution of the real total.`;

  /* -------------------------------------------------------------- *
   * Marketplace mode
   * -------------------------------------------------------------- */

  /**
   * The account name, with its number demoted.
   *
   * The old line read `{business_name || "Ad account"} · Ad account {id}`, which
   * put a database key in the most prominent text on the screen and, for an
   * account with no name, said "Ad account · Ad account 8" — the same phrase
   * twice, one of them a number. Name first, number second and only when it
   * separates one account from another.
   */
  const accountLabel = adAccountDisplay(account, { accountCount: model?.accounts.length ?? 0 });
  const accountStrip = account ? (
    <View style={styles.accountStrip}>
      <View style={styles.accountDot} accessibilityElementsHidden importantForAccessibility="no" />
      {accountNameFirstEnabled() ? (
        <View style={styles.accountTextGroup}>
          <Text style={styles.accountName} numberOfLines={1}>
            {accountLabel.name}
          </Text>
          {accountLabel.reference ? (
            <Text style={styles.accountReference} numberOfLines={1}>
              {accountLabel.reference}
            </Text>
          ) : null}
        </View>
      ) : (
        <Text style={styles.accountText} numberOfLines={1}>
          {account.business_name || "Ad account"} · Ad account {account.id}
        </Text>
      )}
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
        <StoreKpiCard
          label="Spend · to date"
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
          label="Clicks · to date"
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
          // No clicks means the figure is undefined, not zero — a cost per click
          // of nothing would be a claim that the clicks were free.
          value={absentValueTextOr(
            kpis.cpcCents == null ? "—" : money(kpis.cpcCents),
            kpis.cpcCents == null ? "no_activity" : "ready",
            { notConfiguredText: "No clicks yet" }
          )}
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
      return (
        <AdsEmpty
          title="No campaigns yet"
          body={
            model.needsVerification
              ? "Create a campaign and get your business verified — campaigns can't deliver until PulseSoc approves the account. Nothing is charged while a campaign is a draft."
              : "Campaigns you create appear here with their delivery status, spend and pacing. Nothing is charged while a campaign is a draft."
          }
          ctaLabel={model.needsVerification ? "Verify your business" : "Create campaign"}
          onPress={
            model.needsVerification ? openVerification : () => openClassic("Create campaign")
          }
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
          const phase = campaignPhase(campaign);
          const budget = campaignBudget(campaign);
          const switchState = deliverySwitchState(campaign, account);
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
          const metrics: CampaignCardMetric[] = [
            { key: "spent", label: "Spent", value: money(spentCents) },
            {
              key: "impressions",
              label: "Impressions",
              value: absentValueTextOr(
                row ? formatters.count(Number(row.impressions || 0)) : "—",
                metricState(),
                { zeroText: formatters.count(0) }
              )
            },
            {
              key: "clicks",
              label: "Clicks",
              value: absentValueTextOr(
                row ? formatters.count(clicks) : "—",
                metricState(),
                { zeroText: formatters.count(0) }
              )
            },
            {
              key: "cpc",
              label: "CPC",
              value: absentValueTextOr(
                row && clicks > 0 && Number(row.estimated_cpc || 0) > 0
                  ? money(Math.round(Number(row.estimated_cpc) * 100))
                  : "—",
                // A campaign with no clicks has no cost per click — that is an
                // undefined figure, not a zero one.
                !model?.analytics ? "unavailable" : clicks > 0 ? "ready" : "no_activity"
              )
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
              phase={phase}
              phaseLabel={campaignPhaseLabel(phase)}
              phaseTone={campaignPhaseTone(phase)}
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
              metricsLive={campaignMetricsAreLive(campaign)}
              showSwitch={switchState.show}
              delivering={switchState.on}
              switchDisabled={switchState.disabled || offline}
              switchReason={
                offline && !switchState.disabled
                  ? "You're offline — delivery can't be changed."
                  : switchState.reason
              }
              onToggleDelivery={(next) => {
                if (!switchState.action) return;
                applyAction(campaign, next ? "resume" : "pause").catch(() => undefined);
              }}
              toggleBusy={busyKey === `campaign-${campaign.id}`}
              blockedVerification={blocked.some((entry) => entry.id === campaign.id)}
              onVerify={openVerification}
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
        {zeroBalance && model?.wallet ? (
          <AdsZeroBalanceBanner
            fundingLive={model.wallet.fundingLive}
            onAddFunds={openWallet}
            reducedMotion={reducedMotion}
          />
        ) : null}
        {walletFailed ? (
          <AdsWalletUnavailable onRetry={() => load("refresh")} reducedMotion={reducedMotion} />
        ) : null}
        {blocked.length ? (
          <AdsVerificationBanner
            campaignName={blocked.length === 1 ? blocked[0].campaign_name || null : null}
            onVerify={openVerification}
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
            // Audiences and the creative library have no endpoint in this app.
            // A tile that opens the wrong screen is worse than one that says
            // "not yet", so both are disabled and say why.
            {
              icon: "people-outline",
              label: "Audiences",
              subtitle: "Not available in the app yet",
              disabled: true,
              reducedMotion
            },
            {
              icon: "images-outline",
              label: "Creative library",
              subtitle: "Not available in the app yet",
              disabled: true,
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
      <AdsPreviewNote
        text={
          postEnabled
            ? "Post ads is a preview. Every figure below is sample data — no promotion is running, nothing is submitted and nothing is charged. Your ad wallet above is real and funds Marketplace ads."
            : "Promoting posts, Reels and live replays isn't available yet. Your ad wallet still funds Marketplace ads."
        }
      />

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
          <AdsEmpty
            title="Post ads is coming"
            body="Promoting a post, Reel or live replay will run from the same ad wallet as your Marketplace campaigns. It isn't switched on in this build."
            reducedMotion={reducedMotion}
            tone="post"
          />
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
                  metrics={[
                    {
                      key: "reach",
                      label: "Reach",
                      value: known ? formatters.count(Number(promotion.reach || 0)) : "—"
                    },
                    // MOCK-DATA: likes and follows attributable to a promotion
                    // have no source at all, so they read "—" even in the
                    // preview rather than showing an invented count.
                    { key: "likes", label: "Likes", value: "—" },
                    { key: "follows", label: "Follows", value: "—" },
                    {
                      key: "cpm",
                      label: "Cost per 1k views",
                      value:
                        known && Number(promotion.reach || 0) > 0
                          ? money(Math.round((spentCents / Number(promotion.reach)) * 1000))
                          : "—"
                    }
                  ]}
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

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: adsLight.bg.page },
  hidden: { display: "none" },
  content: { paddingTop: 12, gap: 14 },
  stack: { gap: 10, paddingHorizontal: adsLight.space.card },
  section: { gap: 10 },
  kpiRow: { flexDirection: "row", gap: 10, paddingHorizontal: adsLight.space.card },
  accountStrip: { flexDirection: "row", alignItems: "center", gap: 8 },
  accountDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: adsLight.status.success
  },
  accountText: { flex: 1, fontSize: 12, color: adsLight.text.onDarkMuted, fontWeight: "600" },
  // Two lines rather than one joined string: the weight difference is what makes
  // the number secondary, and a single `Text` could not express it.
  accountTextGroup: { flex: 1 },
  accountName: { fontSize: 13, color: adsLight.text.onDark, fontWeight: "700" },
  accountReference: { fontSize: 11, color: adsLight.text.onDarkMuted, fontWeight: "600" },
  accountAction: { fontSize: 12, fontWeight: "800", color: adsLight.text.onDark },
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

/**
 * Per-campaign management — the screen behind
 * `BusinessOsAdvertising { mode: "detail", campaignId }`.
 *
 * One campaign, five sections: Overview (budget pacing, schedule, objective,
 * audience summary, placements, 7-day spend), Ad sets, Creatives,
 * Recommendations, and the lifecycle action row. Everything rendered here is
 * server data from `GET /campaign/:id/detail` — nothing is computed locally
 * except display formatting.
 *
 * The pause switch and the Resume gate are the manager's own helpers
 * (`deliverySwitchState`, `resumeCheck`) imported rather than re-derived, so a
 * campaign can never be pausable on one screen and not the other.
 *
 * Recommendations are never applied automatically. The Apply chip opens a
 * confirmation dialog, and only an explicit confirm sends `approve: true` —
 * mirroring the server's own consent requirement.
 *
 * Design system: adsLight — same dark header and white cards as the manager
 * and its sub-pages. All copy is i18n'd under `commerce:adsDetail`; strings
 * from imported delivery helpers and server payloads (blocker messages,
 * insight titles, rejection reasons) are shown as delivered.
 */

import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Animated,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  AdAdset,
  AdAdsetAction,
  AdCampaignDetail,
  AdDetailCreative,
  AdInsight,
  adInsightsForCampaign,
  applyAdInsight,
  availableAdAdsetActions,
  createAdCampaignAdset,
  getAdCampaignDetail,
  listAdInsights,
  runAdAdsetAction
} from "../api/adsDetail";
import {
  AdAccount,
  AdCampaign,
  AdCampaignAction,
  availableAdCampaignActions,
  formatCents,
  formatObjective,
  listAdAccounts,
  loadCachedAdAccounts,
  runAdCampaignAction
} from "../api/businessOs";
import { campaignBudget, deliverySwitchState } from "../api/adsDashboard";
import {
  deliveryState,
  deliveryStateDetail,
  deliveryStateLabel,
  deliveryStateTone,
  resumeCheck
} from "../api/adsDelivery";
import { AdsPortal, getAdsPortal } from "../api/adsPortal";
import { isAdPlacementKey } from "../api/adsOs";
import { AD_PLACEMENT_LABEL_KEYS } from "../advertising/campaignDraft";
import {
  AdsSectionError,
  AdsSkeletonBlock,
  AdsStatusPill,
  BudgetPacingBar,
  PauseSwitch,
  SpendBarChart
} from "../components/ads";
import { useFormatters, useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { adsLight } from "../theme/adsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreEntrance } from "../theme/storeMotion";

type Props = {
  route?: { params?: { title?: string; campaignId?: number; accountId?: number } };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

const NS = "commerce:adsDetail";

/** Estimated-result metric names the backend records, mapped to copy. */
const METRIC_LABEL_KEYS: Record<string, string> = {
  impressions: "metricImpressions",
  clicks: "metricClicks",
  views: "metricViews"
};

const CAMPAIGN_ACTION_KEYS: Record<AdCampaignAction, string> = {
  pause: "actionPause",
  resume: "actionResume",
  archive: "actionArchive",
  duplicate: "actionDuplicate",
  submit: "actionSubmit",
  complete: "actionComplete"
};

const ADSET_ACTION_KEYS: Record<AdAdsetAction, string> = {
  pause: "actionPause",
  resume: "actionResume",
  archive: "actionArchive"
};

export function AdsCampaignDetailScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const formatters = useFormatters();
  const insets = useSafeAreaInsets();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(6, reducedMotion);

  const campaignId = Number(route?.params?.campaignId || 0);

  const [detail, setDetail] = useState<AdCampaignDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  const [portal, setPortal] = useState<AdsPortal | null>(null);
  const [account, setAccount] = useState<AdAccount | null>(null);

  const [insights, setInsights] = useState<AdInsight[]>([]);
  const [insightsFailed, setInsightsFailed] = useState(false);

  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");

  const [createOpen, setCreateOpen] = useState(false);
  const [newAdsetName, setNewAdsetName] = useState("");
  const [createError, setCreateError] = useState("");

  const [confirmInsight, setConfirmInsight] = useState<AdInsight | null>(null);
  const [appliedChanges, setAppliedChanges] = useState<string[] | null>(null);

  /* -------------------------------------------------------------- *
   * Data
   * -------------------------------------------------------------- */

  const load = useCallback(async () => {
    if (campaignId <= 0) {
      setLoading(false);
      setLoadFailed(true);
      return;
    }
    try {
      const data = await getAdCampaignDetail(campaignId);
      setDetail(data.detail);
      setLoadFailed(false);
      const accountId = data.detail.campaign.ad_account_id;
      // Secondary reads are individually fault-tolerant: the detail page
      // renders without any of them, each just enriches a section.
      getAdsPortal()
        .then((portalData) => setPortal(portalData.portal ?? null))
        .catch(() => undefined);
      loadCachedAdAccounts()
        .then((accounts) => {
          const found = accounts.find((row) => row.id === accountId);
          if (found) setAccount(found);
        })
        .catch(() => undefined);
      listAdAccounts()
        .then((accountsData) => {
          const found = accountsData.accounts.find((row) => row.id === accountId);
          if (found) setAccount(found);
        })
        .catch(() => undefined);
      if (accountId > 0) {
        listAdInsights(accountId)
          .then((insightData) => {
            setInsights(adInsightsForCampaign(insightData.recommendations, campaignId));
            setInsightsFailed(false);
          })
          .catch(() => setInsightsFailed(true));
      }
    } catch {
      setLoadFailed(true);
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => {
    void load();
  }, [load]);

  /* -------------------------------------------------------------- *
   * Derived campaign shape — feeds the shared delivery helpers
   * -------------------------------------------------------------- */

  const campaignShape = useMemo<AdCampaign | null>(() => {
    if (!detail) return null;
    return {
      id: detail.campaign.id,
      ad_account_id: detail.campaign.ad_account_id,
      campaign_name: detail.campaign.campaign_name,
      objective: detail.campaign.objective,
      status: detail.lifecycle.status,
      budget_type: detail.budget.budget_type,
      daily_budget_cents: detail.budget.daily_budget_cents,
      lifetime_budget_cents: detail.budget.lifetime_budget_cents,
      spent_cents: detail.budget.spent_cents,
      start_at: detail.schedule.start_at,
      end_at: detail.schedule.end_at,
      placements: detail.placements
    };
  }, [detail]);

  const delivery = campaignShape ? deliveryState(portal, campaignShape) : null;
  const switchState = campaignShape ? deliverySwitchState(campaignShape, account) : null;
  const resume = campaignShape ? resumeCheck(portal, campaignShape) : null;
  const campaignActions = campaignShape ? availableAdCampaignActions(campaignShape) : [];
  const budget = campaignShape ? campaignBudget(campaignShape) : null;

  /* -------------------------------------------------------------- *
   * Actions
   * -------------------------------------------------------------- */

  const runCampaignAction = useCallback(
    async (action: AdCampaignAction) => {
      if (busyKey) return;
      setBusyKey(`campaign-${action}`);
      setActionError("");
      try {
        await runAdCampaignAction(campaignId, action);
        await load();
      } catch (error) {
        setActionError(error instanceof Error ? error.message : t(`${NS}.actionFailed`));
      } finally {
        setBusyKey(null);
      }
    },
    [busyKey, campaignId, load, t]
  );

  const toggleDelivery = useCallback(
    (next: boolean) => {
      if (!switchState?.action) return;
      if (next && resume && !resume.allowed) return;
      void runCampaignAction(next ? "resume" : "pause");
    },
    [resume, runCampaignAction, switchState]
  );

  const runAdsetAction = useCallback(
    async (adset: AdAdset, action: AdAdsetAction) => {
      if (busyKey) return;
      setBusyKey(`adset-${adset.id}-${action}`);
      setActionError("");
      try {
        await runAdAdsetAction(adset.id, action);
        await load();
      } catch (error) {
        setActionError(error instanceof Error ? error.message : t(`${NS}.actionFailed`));
      } finally {
        setBusyKey(null);
      }
    },
    [busyKey, load, t]
  );

  const createAdset = useCallback(async () => {
    const name = newAdsetName.trim();
    if (!name || !detail || busyKey) return;
    setBusyKey("adset-create");
    setCreateError("");
    try {
      // The new ad set snapshots the campaign's current audience so it starts
      // from what already runs instead of from nothing.
      await createAdCampaignAdset(campaignId, {
        name,
        status: "active",
        targeting: detail.targeting
      });
      setCreateOpen(false);
      setNewAdsetName("");
      await load();
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : t(`${NS}.actionFailed`));
    } finally {
      setBusyKey(null);
    }
  }, [busyKey, campaignId, detail, load, newAdsetName, t]);

  const applyInsight = useCallback(async () => {
    const insight = confirmInsight;
    if (!insight || !detail || busyKey) return;
    setBusyKey(`insight-${insight.id}`);
    setActionError("");
    try {
      const result = await applyAdInsight(detail.campaign.ad_account_id, insight.id);
      const before = (result.before || {}) as Record<string, unknown>;
      const after = (result.after || {}) as Record<string, unknown>;
      const changes = Object.keys(after).map(
        (key) => `${key}: ${String(before[key] ?? "—")} → ${String(after[key] ?? "—")}`
      );
      setAppliedChanges(changes);
      setInsights((current) => current.filter((row) => row.id !== insight.id));
      await load();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : t(`${NS}.actionFailed`));
    } finally {
      setConfirmInsight(null);
      setBusyKey(null);
    }
  }, [busyKey, confirmInsight, detail, load, t]);

  const openEditor = useCallback(() => {
    navigation?.navigate("BusinessOsAdvertising", {
      title: detail?.campaign.campaign_name || t(`${NS}.title`),
      mode: "classic"
    });
  }, [detail, navigation, t]);

  const goBack = useCallback(() => {
    navigation?.goBack?.();
  }, [navigation]);

  /* -------------------------------------------------------------- *
   * Display helpers
   * -------------------------------------------------------------- */

  const money = useCallback((cents: number) => formatCents(cents), []);

  const scheduleText = useMemo(() => {
    if (!detail) return "";
    const start = detail.schedule.start_at.slice(0, 10);
    const end = detail.schedule.end_at.slice(0, 10);
    if (!start) return t(`${NS}.scheduleNotSet`);
    return end
      ? t(`${NS}.scheduleRange`, { start, end })
      : t(`${NS}.scheduleOpenEnded`, { start });
  }, [detail, t]);

  const audienceText = useMemo(() => {
    if (!detail) return "";
    const targeting = detail.targeting;
    const modeKey =
      targeting.audience_mode === "followers"
        ? "audienceFollowers"
        : targeting.audience_mode === "non_followers"
          ? "audienceNonFollowers"
          : targeting.audience_mode === "engaged"
            ? "audienceEngaged"
            : "audienceEveryone";
    const parts = [
      t(`${NS}.${modeKey}`),
      t(`${NS}.ageRange`, { min: targeting.min_age, max: targeting.max_age }),
      targeting.countries.length > 0
        ? t(`${NS}.countriesCount`, { count: targeting.countries.length })
        : t(`${NS}.allCountries`)
    ];
    return parts.join(" · ");
  }, [detail, t]);

  const placementLabel = useCallback(
    (key: string) =>
      isAdPlacementKey(key) ? t(`commerce:adsWizard.${AD_PLACEMENT_LABEL_KEYS[key]}`) : key,
    [t]
  );

  const metricsLine = useCallback(
    (metrics: { impressions: number; clicks: number; ctr: number; spend_cents: number }) =>
      t(`${NS}.metricsLine`, {
        impressions: formatters.number(metrics.impressions),
        clicks: formatters.number(metrics.clicks),
        ctr: formatters.percent(metrics.ctr)
      }),
    [formatters, t]
  );

  const adsetStatusLabel = useCallback(
    (status: AdAdset["status"]) =>
      t(`${NS}.${status === "paused" ? "statusPaused" : status === "archived" ? "statusArchived" : "statusActive"}`),
    [t]
  );

  const moderationLabel = useCallback(
    (creative: AdDetailCreative) => {
      const key =
        creative.moderation_status === "approved"
          ? "modApproved"
          : creative.moderation_status === "pending"
            ? "modPending"
            : creative.moderation_status === "rejected"
              ? "modRejected"
              : "";
      return key ? t(`${NS}.${key}`) : creative.moderation_status || t(`${NS}.modDraft`);
    },
    [t]
  );

  /* -------------------------------------------------------------- *
   * Frame
   * -------------------------------------------------------------- */

  const title = detail?.campaign.campaign_name || route?.params?.title || t(`${NS}.title`);

  return (
    <View style={styles.root}>
      <LinearGradient
        colors={[adsLight.bg.headerFrom, adsLight.bg.headerTo]}
        style={[styles.header, { paddingTop: insets.top + 8 }]}
      >
        <Pressable
          onPress={goBack}
          style={styles.iconButton}
          accessibilityRole="button"
          accessibilityLabel={t(`${NS}.backLabel`)}
          hitSlop={6}
        >
          <Ionicons name="chevron-back" size={24} color={adsLight.text.onDark} />
        </Pressable>
        <Text style={styles.headerTitle} numberOfLines={1} accessibilityRole="header">
          {title}
        </Text>
      </LinearGradient>

      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: bottomPad(insets.bottom) }]}
        showsVerticalScrollIndicator={false}
      >
        {loading ? (
          <View style={styles.stack}>
            <AdsSkeletonBlock width="100%" height={92} reducedMotion={reducedMotion} />
            <AdsSkeletonBlock width="100%" height={160} reducedMotion={reducedMotion} />
            <AdsSkeletonBlock width="100%" height={120} reducedMotion={reducedMotion} />
          </View>
        ) : null}

        {!loading && loadFailed ? (
          <View style={styles.stack}>
            <AdsSectionError
              message={t(`${NS}.loadFailed`)}
              onRetry={() => {
                setLoading(true);
                void load();
              }}
              reducedMotion={reducedMotion}
              retryLabel={t(`${NS}.retry`)}
            />
          </View>
        ) : null}

        {!loading && !loadFailed && detail && campaignShape ? (
          <>
            {/* --- Status + delivery switch ------------------------------ */}
            <Animated.View style={[styles.stack, entrance.styleFor(0)]}>
              <View style={styles.card}>
                <View style={styles.statusRow}>
                  {delivery ? (
                    <AdsStatusPill
                      label={deliveryStateLabel(delivery)}
                      tone={deliveryStateTone(delivery)}
                      reducedMotion={reducedMotion}
                    />
                  ) : null}
                  <Text style={styles.objectiveText} numberOfLines={1}>
                    {formatObjective(detail.campaign.objective)}
                  </Text>
                </View>
                {switchState?.show ? (
                  <PauseSwitch
                    on={switchState.on}
                    onToggle={toggleDelivery}
                    reducedMotion={reducedMotion}
                    busy={busyKey === "campaign-pause" || busyKey === "campaign-resume"}
                    disabled={switchState.disabled || Boolean(!switchState.on && resume && !resume.allowed)}
                    label={t(`${NS}.${switchState.on ? "delivering" : "paused"}`)}
                  />
                ) : null}
                {switchState?.reason ? <Text style={styles.mutedNote}>{switchState.reason}</Text> : null}
                {!switchState?.disabled && switchState && !switchState.on && resume && !resume.allowed && resume.reason ? (
                  <Text style={styles.mutedNote}>{resume.reason}</Text>
                ) : null}
                {detail.lifecycle.blocker ? (
                  <View style={styles.blockerBox}>
                    <Ionicons name="alert-circle-outline" size={16} color={adsLight.status.warning} />
                    <Text style={styles.blockerText}>{detail.lifecycle.blocker.message}</Text>
                  </View>
                ) : null}
                {deliveryStateDetail(portal, campaignShape) ? (
                  <Text style={styles.mutedNote}>{deliveryStateDetail(portal, campaignShape)}</Text>
                ) : null}
              </View>
            </Animated.View>

            {/* --- Overview ---------------------------------------------- */}
            <Animated.View style={[styles.stack, entrance.styleFor(1)]}>
              <Text style={styles.sectionTitle}>{t(`${NS}.overviewTitle`)}</Text>
              <View style={styles.card}>
                {budget ? (
                  <BudgetPacingBar
                    spentLabel={money(budget.spentCents)}
                    budgetLabel={
                      budget.type === "daily"
                        ? t(`${NS}.budgetDailyLabel`, { amount: money(budget.budgetCents) })
                        : t(`${NS}.budgetLifetimeLabel`, { amount: money(budget.budgetCents) })
                    }
                    fraction={budget.fraction}
                    hot={budget.hot}
                    reducedMotion={reducedMotion}
                  />
                ) : (
                  <Text style={styles.mutedNote}>{t(`${NS}.noBudget`)}</Text>
                )}
                <View style={styles.kvRow}>
                  <Text style={styles.kvLabel}>{t(`${NS}.scheduleLabel`)}</Text>
                  <Text style={styles.kvValue}>{scheduleText}</Text>
                </View>
                <View style={styles.kvRow}>
                  <Text style={styles.kvLabel}>{t(`${NS}.audienceLabel`)}</Text>
                  <Text style={styles.kvValue}>{audienceText}</Text>
                </View>
                <Text style={styles.kvLabel}>{t(`${NS}.placementsLabel`)}</Text>
                {detail.placements.length > 0 ? (
                  <View style={styles.chipWrap}>
                    {detail.placements.map((key) => (
                      <View key={key} style={styles.placementChip}>
                        <Text style={styles.placementChipText}>{placementLabel(key)}</Text>
                      </View>
                    ))}
                  </View>
                ) : (
                  <Text style={styles.mutedNote}>{t(`${NS}.placementsAutomatic`)}</Text>
                )}
                {detail.estimated_results ? (
                  <View style={styles.kvRow}>
                    <Text style={styles.kvLabel}>{t(`${NS}.estimatedResultsLabel`)}</Text>
                    <Text style={styles.kvValue}>
                      {`${formatters.number(detail.estimated_results.value)} ${
                        METRIC_LABEL_KEYS[detail.estimated_results.metric]
                          ? t(`${NS}.${METRIC_LABEL_KEYS[detail.estimated_results.metric]}`)
                          : detail.estimated_results.metric
                      }`}
                    </Text>
                  </View>
                ) : null}
              </View>
              <SpendBarChart
                title={t(`${NS}.spendTitle`)}
                values={detail.daily_series.map((point) => point.spend_cents)}
                dayLabels={detail.daily_series.map((point) => point.date.slice(8, 10))}
                summary={t(`${NS}.spendSummary`, { total: money(detail.totals.spend_cents) })}
                mock={false}
                empty={detail.daily_series.length === 0}
                totalLabel={money(detail.totals.spend_cents)}
                reducedMotion={reducedMotion}
                seriesKey={detail.campaign.updated_at}
              />
              <View style={styles.card}>
                <Text style={styles.kvValue}>{metricsLine(detail.totals)}</Text>
                <Text style={styles.mutedNote}>
                  {t(`${NS}.spentLine`, { amount: money(detail.totals.spend_cents) })}
                </Text>
              </View>
            </Animated.View>

            {/* --- Ad sets ----------------------------------------------- */}
            <Animated.View style={[styles.stack, entrance.styleFor(2)]}>
              <View style={styles.sectionHead}>
                <Text style={styles.sectionTitle}>{t(`${NS}.adsetsTitle`)}</Text>
                {detail.lifecycle.can_edit ? (
                  <Pressable
                    onPress={() => {
                      setCreateError("");
                      setCreateOpen(true);
                    }}
                    accessibilityRole="button"
                    accessibilityLabel={t(`${NS}.newAdset`)}
                    hitSlop={6}
                  >
                    <Text style={styles.inlineLink}>{t(`${NS}.newAdset`)}</Text>
                  </Pressable>
                ) : null}
              </View>
              {detail.adsets.length === 0 ? (
                <View style={styles.card}>
                  <Text style={styles.mutedNote}>{t(`${NS}.adsetsEmpty`)}</Text>
                </View>
              ) : (
                detail.adsets.map((adset) => (
                  <View key={adset.id} style={styles.card}>
                    <View style={styles.rowHead}>
                      <Text style={styles.cardTitle} numberOfLines={1}>
                        {adset.name}
                      </Text>
                      {adset.is_default ? (
                        <View style={styles.defaultTag}>
                          <Text style={styles.defaultTagText}>{t(`${NS}.adsetDefault`)}</Text>
                        </View>
                      ) : null}
                    </View>
                    <Text style={styles.mutedNote}>{adsetStatusLabel(adset.status)}</Text>
                    <Text style={styles.kvValue}>{metricsLine(adset.metrics)}</Text>
                    <View style={styles.actionRow}>
                      {availableAdAdsetActions(adset).map((action) => {
                        const key = `adset-${adset.id}-${action}`;
                        return (
                          <Pressable
                            key={key}
                            style={[styles.chip, busyKey ? styles.chipDimmed : null]}
                            onPress={() => void runAdsetAction(adset, action)}
                            disabled={Boolean(busyKey)}
                            accessibilityRole="button"
                            accessibilityLabel={t(`${NS}.${ADSET_ACTION_KEYS[action]}`)}
                          >
                            <Text style={styles.chipText}>{t(`${NS}.${ADSET_ACTION_KEYS[action]}`)}</Text>
                          </Pressable>
                        );
                      })}
                    </View>
                  </View>
                ))
              )}
            </Animated.View>

            {/* --- Creatives --------------------------------------------- */}
            <Animated.View style={[styles.stack, entrance.styleFor(3)]}>
              <Text style={styles.sectionTitle}>{t(`${NS}.creativesTitle`)}</Text>
              {detail.creatives.length === 0 ? (
                <View style={styles.card}>
                  <Text style={styles.mutedNote}>{t(`${NS}.creativesEmpty`)}</Text>
                </View>
              ) : (
                detail.creatives.map((creative) => (
                  <View key={creative.id} style={styles.card}>
                    <View style={styles.rowHead}>
                      <Text style={styles.cardTitle} numberOfLines={1}>
                        {creative.headline || creative.title || `#${creative.id}`}
                      </Text>
                      <Text style={styles.mutedNote}>{moderationLabel(creative)}</Text>
                    </View>
                    {creative.rejection_reason ? (
                      <View style={styles.blockerBox}>
                        <Ionicons name="alert-circle-outline" size={16} color={adsLight.status.warning} />
                        <Text style={styles.blockerText}>{creative.rejection_reason}</Text>
                      </View>
                    ) : null}
                    {!creative.media_ready ? (
                      <Text style={styles.mutedNote}>{t(`${NS}.mediaProcessing`)}</Text>
                    ) : null}
                    <Text style={styles.kvValue}>{metricsLine(creative.metrics)}</Text>
                  </View>
                ))
              )}
            </Animated.View>

            {/* --- Recommendations --------------------------------------- */}
            <Animated.View style={[styles.stack, entrance.styleFor(4)]}>
              <Text style={styles.sectionTitle}>{t(`${NS}.insightsTitle`)}</Text>
              {appliedChanges ? (
                <View style={styles.card}>
                  <Text style={styles.cardTitle}>{t(`${NS}.appliedTitle`)}</Text>
                  {appliedChanges.length > 0 ? (
                    <>
                      <Text style={styles.mutedNote}>{t(`${NS}.changesLabel`)}</Text>
                      {appliedChanges.map((line) => (
                        <Text key={line} style={styles.kvValue}>
                          {line}
                        </Text>
                      ))}
                    </>
                  ) : null}
                </View>
              ) : null}
              {insightsFailed ? <Text style={styles.mutedNote}>{t(`${NS}.insightsFailed`)}</Text> : null}
              {!insightsFailed && insights.length === 0 && !appliedChanges ? (
                <View style={styles.card}>
                  <Text style={styles.mutedNote}>{t(`${NS}.insightsEmpty`)}</Text>
                </View>
              ) : null}
              {insights.map((insight) => (
                <View key={insight.id} style={styles.card}>
                  <View style={styles.rowHead}>
                    <Ionicons
                      name={insight.severity === "warning" ? "alert-circle-outline" : "bulb-outline"}
                      size={18}
                      color={insight.severity === "warning" ? adsLight.status.warning : adsLight.status.success}
                    />
                    <Text style={styles.cardTitle} numberOfLines={2}>
                      {insight.title}
                    </Text>
                  </View>
                  <Text style={styles.mutedNote}>{insight.why}</Text>
                  <View style={styles.actionRow}>
                    <Pressable
                      style={[styles.chip, busyKey ? styles.chipDimmed : null]}
                      onPress={() => setConfirmInsight(insight)}
                      disabled={Boolean(busyKey)}
                      accessibilityRole="button"
                      accessibilityLabel={t(`${NS}.apply`)}
                    >
                      <Text style={styles.chipText}>{t(`${NS}.apply`)}</Text>
                    </Pressable>
                  </View>
                </View>
              ))}
            </Animated.View>

            {/* --- Lifecycle actions ------------------------------------- */}
            <Animated.View style={[styles.stack, entrance.styleFor(5)]}>
              <Text style={styles.sectionTitle}>{t(`${NS}.actionsTitle`)}</Text>
              {actionError ? <Text style={styles.errorText}>{actionError}</Text> : null}
              <View style={styles.actionRow}>
                {detail.lifecycle.can_edit ? (
                  <Pressable
                    style={[styles.chip, busyKey ? styles.chipDimmed : null]}
                    onPress={openEditor}
                    disabled={Boolean(busyKey)}
                    accessibilityRole="button"
                    accessibilityLabel={t(`${NS}.editCampaign`)}
                  >
                    <Text style={styles.chipText}>{t(`${NS}.editCampaign`)}</Text>
                  </Pressable>
                ) : null}
                {campaignActions.map((action) => (
                  <Pressable
                    key={action}
                    style={[styles.chip, busyKey ? styles.chipDimmed : null]}
                    onPress={() => void runCampaignAction(action)}
                    disabled={Boolean(busyKey)}
                    accessibilityRole="button"
                    accessibilityLabel={t(`${NS}.${CAMPAIGN_ACTION_KEYS[action]}`)}
                  >
                    <Text style={styles.chipText}>{t(`${NS}.${CAMPAIGN_ACTION_KEYS[action]}`)}</Text>
                  </Pressable>
                ))}
              </View>
            </Animated.View>
          </>
        ) : null}
      </ScrollView>

      {/* --- Create ad set sheet ---------------------------------------- */}
      <Modal visible={createOpen} transparent animationType="fade" onRequestClose={() => setCreateOpen(false)}>
        <Pressable style={styles.modalBackdrop} onPress={() => setCreateOpen(false)}>
          <Pressable style={styles.modalSheet} onPress={(event) => event.stopPropagation()}>
            <Text style={styles.modalTitle}>{t(`${NS}.newAdsetTitle`)}</Text>
            <Text style={styles.mutedNote}>{t(`${NS}.newAdsetHint`)}</Text>
            <Text style={styles.kvLabel}>{t(`${NS}.nameLabel`)}</Text>
            <TextInput
              style={styles.input}
              value={newAdsetName}
              onChangeText={setNewAdsetName}
              placeholder={t(`${NS}.namePlaceholder`)}
              placeholderTextColor={adsLight.text.muted}
              maxLength={120}
              accessibilityLabel={t(`${NS}.nameLabel`)}
            />
            {createError ? <Text style={styles.errorText}>{createError}</Text> : null}
            <View style={styles.actionRow}>
              <Pressable
                style={[styles.chip, styles.chipPrimary, !newAdsetName.trim() || busyKey ? styles.chipDimmed : null]}
                onPress={() => void createAdset()}
                disabled={!newAdsetName.trim() || Boolean(busyKey)}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.create`)}
              >
                <Text style={[styles.chipText, styles.chipTextPrimary]}>{t(`${NS}.create`)}</Text>
              </Pressable>
              <Pressable
                style={styles.chip}
                onPress={() => setCreateOpen(false)}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.cancel`)}
              >
                <Text style={styles.chipText}>{t(`${NS}.cancel`)}</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      {/* --- Insight confirmation --------------------------------------- */}
      <Modal
        visible={Boolean(confirmInsight)}
        transparent
        animationType="fade"
        onRequestClose={() => setConfirmInsight(null)}
      >
        <Pressable style={styles.modalBackdrop} onPress={() => setConfirmInsight(null)}>
          <Pressable style={styles.modalSheet} onPress={(event) => event.stopPropagation()}>
            <Text style={styles.modalTitle}>{t(`${NS}.confirmTitle`)}</Text>
            {confirmInsight ? <Text style={styles.cardTitle}>{confirmInsight.title}</Text> : null}
            {confirmInsight ? <Text style={styles.mutedNote}>{confirmInsight.why}</Text> : null}
            <Text style={styles.mutedNote}>{t(`${NS}.confirmBody`)}</Text>
            <View style={styles.actionRow}>
              <Pressable
                style={[styles.chip, styles.chipPrimary, busyKey ? styles.chipDimmed : null]}
                onPress={() => void applyInsight()}
                disabled={Boolean(busyKey)}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.confirm`)}
              >
                <Text style={[styles.chipText, styles.chipTextPrimary]}>{t(`${NS}.confirm`)}</Text>
              </Pressable>
              <Pressable
                style={styles.chip}
                onPress={() => setConfirmInsight(null)}
                accessibilityRole="button"
                accessibilityLabel={t(`${NS}.cancel`)}
              >
                <Text style={styles.chipText}>{t(`${NS}.cancel`)}</Text>
              </Pressable>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );
}

function bottomPad(inset: number) {
  return Math.max(inset, 16) + BOTTOM_NAV_CONTENT_CLEARANCE;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: adsLight.bg.page },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: adsLight.space.card,
    paddingBottom: 12
  },
  iconButton: {
    minWidth: adsLight.size.tapTarget,
    minHeight: adsLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  headerTitle: { flex: 1, fontSize: 20, fontWeight: "700", color: adsLight.text.onDark },
  content: { paddingTop: 12, gap: 14 },
  stack: { gap: 10, paddingHorizontal: adsLight.space.card },
  sectionTitle: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary },
  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  card: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: adsLight.space.card,
    gap: 8
  },
  cardTitle: { flex: 1, fontSize: 15, fontWeight: "800", color: adsLight.text.primary, lineHeight: 20 },
  statusRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
  objectiveText: { fontSize: 13, fontWeight: "700", color: adsLight.text.muted },
  rowHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  kvRow: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 12 },
  kvLabel: { fontSize: 12, fontWeight: "700", color: adsLight.text.muted },
  kvValue: { flexShrink: 1, fontSize: 13, fontWeight: "600", color: adsLight.text.primary, lineHeight: 19, textAlign: "right" },
  mutedNote: { fontSize: 13, color: adsLight.text.muted, lineHeight: 19 },
  blockerBox: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    padding: 10,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.strip
  },
  blockerText: { flex: 1, fontSize: 13, color: adsLight.text.primary, lineHeight: 19 },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  placementChip: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.bg.strip,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  placementChipText: { fontSize: 12, fontWeight: "700", color: adsLight.text.primary },
  defaultTag: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.bg.strip
  },
  defaultTagText: { fontSize: 11, fontWeight: "800", color: adsLight.text.muted },
  actionRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 2 },
  chip: {
    minHeight: adsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 14,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    backgroundColor: adsLight.bg.strip
  },
  chipPrimary: { backgroundColor: adsLight.cta.from, borderColor: adsLight.cta.from },
  chipDimmed: { opacity: 0.45 },
  chipText: { fontSize: 13, fontWeight: "800", color: adsLight.text.primary },
  chipTextPrimary: { color: adsLight.cta.text },
  inlineLink: { fontSize: 13, fontWeight: "700", color: adsLight.text.link, paddingVertical: 4 },
  errorText: { fontSize: 13, fontWeight: "700", color: adsLight.status.error, lineHeight: 19 },
  input: {
    minHeight: adsLight.size.tapTarget,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    backgroundColor: adsLight.bg.strip,
    paddingHorizontal: 12,
    fontSize: 14,
    color: adsLight.text.primary
  },
  modalBackdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(10, 14, 20, 0.5)"
  },
  modalSheet: {
    backgroundColor: adsLight.bg.card,
    borderTopLeftRadius: adsLight.radius.card,
    borderTopRightRadius: adsLight.radius.card,
    padding: adsLight.space.card,
    paddingBottom: 28,
    gap: 10
  },
  modalTitle: { fontSize: 17, fontWeight: "800", color: adsLight.text.primary }
});

export default AdsCampaignDetailScreen;

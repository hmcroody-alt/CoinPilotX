/**
 * Insights — the seventh card of the business "Sections" grid.
 *
 * Insights is the reconciliation point for the whole seller surface. Every
 * number on it is owned by another screen: revenue and orders by Orders, the
 * source split by Store and Marketplace, followers by Advertising. A dashboard
 * that contradicts its sources is worse than no dashboard, so three rules govern
 * this file and none of them is negotiable.
 *
 * **Every figure is aggregated server-side, over the whole table.** The screen
 * calls one endpoint — `GET /api/pulse/insights/seller/summary` — which sums both
 * order tables inside an explicit half-open window cut at the seller's local
 * midnight, using the same status-exclusion rule the Orders screen uses. The
 * tempting shortcut, adding up the seller-orders list the Store screen already
 * has cached, is wrong in a way that hides: that endpoint is `LIMIT 100` per
 * table with no date range, so a "90-day total" built from it is the newest
 * hundred rows and understates more the better the seller does.
 *
 * **A metric with no source does not render.** Store views, ads attribution,
 * on-time dispatch, reply rate and offers answered have no backend that can
 * produce them (`INSIGHTS_MOCK_DATA_GAPS` names each one and the work it needs).
 * They appear as a stated absence or not at all. A zero reads as a measurement,
 * and "0 store views" would be a false one.
 *
 * **Period switches cannot race.** Every load runs through a monotonic request
 * gate, so tapping 7d then 90d can never paint seven days of numbers under a
 * highlighted 90d pill. That is precisely the class of lie this screen exists to
 * prevent.
 *
 * The previous version of this screen mixed ad-delivery counters with two
 * `.length` counts of cached lists and called the result "store performance".
 * Those counts are gone rather than restyled.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  Pressable,
  RefreshControl,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  useWindowDimensions,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import {
  cachedPeriods,
  compareToPrior,
  createInsightsRequestGate,
  INSIGHTS_PERIODS,
  insightsErrorCausesEnabled,
  insightsErrorMessage,
  insightsExportBlockedReason,
  insightsFailure,
  isGap,
  loadInsights,
  readSavedPeriod,
  sourceShare,
  writeSavedPeriod,
  type InsightsComparison,
  type InsightsFailure,
  type InsightsLoad,
  type InsightsPeriod,
  type InsightsSummary
} from "../api/insightsDashboard";
import {
  isDismissed,
  itemMeta,
  recordDismissal,
  selectTip,
  type TipDismissals
} from "../api/insightsRules";
import {
  DualLineChart,
  PeriodPicker,
  RankedListingRow,
  SourceBreakdownRow,
  TipCard,
  type PeriodOption
} from "../components/insights";
import {
  StoreHeader,
  StoreKpiCard,
  StoreKpiSkeleton,
  StoreOfflineNote,
  StoreRowSkeleton,
  StoreSectionError,
  StoreSkeletonBlock
} from "../components/store";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { useFormatters } from "../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { insightsLight } from "../theme/insightsLight";
import { useInsightsPeriodFade } from "../theme/insightsMotion";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { STORE_STAGGER_MS, useStoreEntrance } from "../theme/storeMotion";
import { absentValueText } from "../api/stateLanguage";
import { BusinessOsModules } from "../components/business/BusinessOsModules";

/** Dismissed tips, per rule and subject. Device-local: it is a view preference. */
const DISMISSALS_KEY = "pulse.insights.tipDismissals.v1";

/** Entrance slots, named so a module cannot silently animate out of order. */
const SLOT = {
  kpis: 0,
  chart: 1,
  sources: 2,
  top: 3,
  rings: 4,
  tip: 5
} as const;
const SECTION_COUNT = Object.keys(SLOT).length;

const PERIOD_LABELS: Record<InsightsPeriod, string> = {
  today: "Today",
  "7d": "7 days",
  "30d": "30 days",
  "90d": "90 days"
};

/**
 * How each period's comparison basis is named out loud.
 *
 * Every "▲ N%" on this screen compares against the immediately preceding window
 * of equal length, and the sub-line says which window that was. A percentage
 * without its basis is a number the seller cannot check.
 */
const PRIOR_LABELS: Record<InsightsPeriod, string> = {
  today: "yesterday",
  "7d": "the prior 7 days",
  "30d": "the prior 30 days",
  "90d": "the prior 90 days"
};

const SOURCE_LABELS: Record<"store" | "marketplace", string> = {
  store: "Your store",
  marketplace: "Marketplace"
};

type Props = {
  route?: { params?: RootStackParamList["BusinessOsInsights"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function BusinessOsInsightsScreen({ route, navigation }: Props = {}) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const entrance = useStoreEntrance(SECTION_COUNT, reducedMotion);
  const { width } = useWindowDimensions();

  const [period, setPeriod] = useState<InsightsPeriod>("7d");
  const [load, setLoad] = useState<InsightsLoad | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  /**
   * The failure, with its cause — not just a sentence.
   *
   * The screen previously kept a string, which meant every failure got the same
   * treatment: a line and a Retry. Keeping the cause lets the entitlement case
   * drop the button it cannot use and the sign-in case send the seller somewhere
   * that would actually fix it.
   */
  const [failure, setFailure] = useState<InsightsFailure | null>(null);
  const error = failure?.message ?? null;
  const [available, setAvailable] = useState<InsightsPeriod[] | null>(null);
  const [dismissals, setDismissals] = useState<TipDismissals>({});
  const [exporting, setExporting] = useState(false);

  /**
   * The race guard. Created once per mount and invalidated on unmount, so a
   * response that arrives after the seller has left cannot call `setState`.
   */
  const gate = useRef(createInsightsRequestGate());
  useEffect(() => {
    const current = gate.current;
    return () => current.cancel();
  }, []);

  const fetchPeriod = useCallback(
    async (next: InsightsPeriod, mode: "initial" | "switch" | "refresh") => {
      if (mode === "refresh") setRefreshing(true);
      else setLoading(true);
      setFailure(null);
      try {
        const result = await gate.current.run(() => loadInsights(next));
        setLoad(result);
      } catch (caught) {
        // A superseded request is not a failure and must never be shown: the
        // newer request is still in flight and owns the screen.
        if (gate.current.isStale(caught)) return;
        // With the flag off the wording is the shipped one, wrapped in the same
        // shape so only one code path renders it.
        setFailure(
          insightsErrorCausesEnabled()
            ? insightsFailure(caught, "Your insights")
            : {
                cause: "unexpected",
                message: insightsErrorMessage(caught, "Insights"),
                actionLabel: "Try again",
                retries: true
              }
        );
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    []
  );

  // The saved period and the dismissals are restored *before* the first fetch,
  // so a seller who lives in 30d never sees 7d flash first.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [saved, stored] = await Promise.all([
        readSavedPeriod(),
        readJsonCache<TipDismissals>(DISMISSALS_KEY, (value) => (value || {}) as TipDismissals)
      ]);
      if (cancelled) return;
      setDismissals(stored || {});
      setPeriod(saved);
      await fetchPeriod(saved, "initial");
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [fetchPeriod]);

  /**
   * Offline, the picker only offers periods that were actually cached. Serving
   * one period's numbers under another period's heading is the exact failure the
   * request gate prevents online, and it would be no more acceptable from disk.
   */
  useEffect(() => {
    if (!load?.fromCache) {
      setAvailable(null);
      return;
    }
    let cancelled = false;
    cachedPeriods()
      .then((periods) => {
        if (!cancelled) setAvailable(periods);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [load?.fromCache]);

  const onPeriodChange = useCallback(
    (next: InsightsPeriod) => {
      if (next === period) return;
      setPeriod(next);
      writeSavedPeriod(next).catch(() => undefined);
      fetchPeriod(next, "switch").catch(() => undefined);
    },
    [fetchPeriod, period]
  );

  const summary = load?.summary || null;
  const fade = useInsightsPeriodFade(reducedMotion, period);

  /* ------------------------------------------------------------ formatting */

  const currency = summary?.currency || "USD";

  const money = useCallback(
    (minor: number, compact = false) =>
      formatters.currency(minor / 100, {
        currency,
        compact,
        maximumFractionDigits: compact ? 1 : 2
      }),
    [currency, formatters]
  );

  /**
   * A comparison in words, or a stated refusal.
   *
   * Two refusals, deliberately different sentences: a seller who did not exist
   * last period is told so, and a seller who existed and earned nothing is told
   * that instead. Neither renders as ▲100%, which is what a naive percentage
   * against zero produces and what makes new-seller dashboards untrustworthy.
   */
  const comparisonText = useCallback(
    (comparison: InsightsComparison): string => {
      if (comparison.kind === "none") {
        return comparison.reason === "no_prior_period"
          ? "New — no prior period"
          : `Nothing in ${PRIOR_LABELS[period]}`;
      }
      if (comparison.direction === "flat") return `Level with ${PRIOR_LABELS[period]}`;
      const magnitude = formatters.percent(Math.abs(comparison.ratio));
      const word = comparison.direction === "up" ? "up" : "down";
      return `${word} ${magnitude} vs ${PRIOR_LABELS[period]}`;
    },
    [formatters, period]
  );

  const trendFor = useCallback(
    (comparison: InsightsComparison) =>
      comparison.kind === "change" && comparison.direction !== "flat"
        ? {
            direction: comparison.direction,
            label: formatters.percent(Math.abs(comparison.ratio))
          }
        : null,
    [formatters]
  );

  /* ---------------------------------------------------------------- chart */

  const series = summary?.series || [];

  /** Plot width: the card's inner width minus the y-label gutter and its gap. */
  const chartWidth = Math.max(width - insightsLight.space.card * 2 - 62 - 8, 120);

  /**
   * A calendar day at local noon.
   *
   * The bucket `date` is a plain `YYYY-MM-DD` already shifted into the seller's
   * timezone server-side. Parsing it as an instant would re-apply the device
   * offset and slide every label back a day for anyone west of UTC, so it is
   * built as a local date and pinned to midday, which no offset can roll over.
   */
  const dayOf = useCallback((iso: string): Date | null => {
    const parts = iso.split("-").map((part) => Number(part));
    if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return null;
    return new Date(parts[0], parts[1] - 1, parts[2], 12, 0, 0, 0);
  }, []);

  const xLabels = useMemo(() => {
    if (series.length === 0) return [];
    const weekdays = formatters.weekdayNames("short");
    // Thin the labels rather than the data: every bucket is still plotted, but a
    // 90-day axis with 13 legible marks beats one with 13 unreadable ones.
    const stride =
      series.length <= 8 ? 1 : series.length <= 16 ? 2 : Math.ceil(series.length / 6);
    return series.map((bucket, index) => {
      if (index % stride !== 0 && index !== series.length - 1) return "";
      const date = dayOf(bucket.date);
      if (!date) return "";
      if (period === "7d" || period === "today") return weekdays[date.getDay()] || "";
      return formatters.day(date);
    });
  }, [dayOf, formatters, period, series]);

  const gridLabels = useMemo((): [string, string, string] => {
    const peak = series.reduce((max, bucket) => Math.max(max, bucket.revenue_minor), 0);
    if (peak <= 0) return ["", "", money(0, true)];
    return [money(peak, true), money(peak / 2, true), money(0, true)];
  }, [money, series]);

  /**
   * The chart's text alternative.
   *
   * The drawing itself is marked decorative, so this sentence is the whole chart
   * for anyone using a screen reader: the span it covers, where revenue started
   * and ended, where it peaked, and the same shape for orders. Four facts, in
   * words, because a trend that only exists as a slope is not accessible.
   */
  const chartSummary = useMemo(() => {
    if (series.length === 0) return "Revenue and orders chart. No data in this period.";
    const first = series[0];
    const last = series[series.length - 1];
    const peak = series.reduce(
      (best, bucket) => (bucket.revenue_minor > best.revenue_minor ? bucket : best),
      series[0]
    );
    const startDate = dayOf(first.date);
    const endDate = dayOf(last.date);
    const peakDate = dayOf(peak.date);
    const span =
      startDate && endDate ? formatters.range(startDate, endDate) : `${first.date} to ${last.date}`;
    const totalOrders = series.reduce((sum, bucket) => sum + bucket.orders, 0);
    return [
      `Revenue and orders, ${span}, in ${summary?.bucket === "week" ? "weekly" : "daily"} steps.`,
      `Revenue started at ${money(first.revenue_minor)} and ended at ${money(last.revenue_minor)}.`,
      peakDate
        ? `It peaked at ${money(peak.revenue_minor)} on ${formatters.day(peakDate)}.`
        : `It peaked at ${money(peak.revenue_minor)}.`,
      `${formatters.count(totalOrders)} orders across the period.`
    ].join(" ");
  }, [dayOf, formatters, money, series, summary?.bucket]);

  /* ------------------------------------------------------------------ tip */

  const tip = useMemo(() => (summary ? selectTip(summary) : null), [summary]);
  const tipVisible = Boolean(tip && !isDismissed(tip, dismissals));

  const dismissTip = useCallback(() => {
    if (!tip) return;
    const next = recordDismissal(tip, dismissals);
    setDismissals(next);
    writeJsonCache(DISMISSALS_KEY, next).catch(() => undefined);
  }, [dismissals, tip]);

  /* ----------------------------------------------------------- navigation */

  const go = useCallback(
    (name: string, params?: Record<string, unknown>) => navigation?.navigate(name, params),
    [navigation]
  );

  const openOrders = useCallback(() => go("SellerStore", { mode: "orders" }), [go]);
  const openStore = useCallback(() => go("SellerStore", { mode: "dashboard" }), [go]);
  const openAdvertising = useCallback(
    () => go("BusinessOsAdvertising", { mode: "manager" }),
    [go]
  );
  // The listing editor is reached through the store surface; there is no
  // standalone listing-detail route in this app, so a ranked row opens the place
  // the seller can actually act on it rather than a dead end.
  const openListing = useCallback(
    (title: string | null) => go("SellerStore", { mode: "dashboard", title: title || undefined }),
    [go]
  );

  const runTipAction = useCallback(() => {
    if (!tip) return;
    // Bound to a local so the discriminated union narrows cleanly across the
    // branches below; `tip.action.kind` re-reads a property TypeScript will not
    // keep narrowed past an intervening call.
    const action = tip.action;
    if (action.kind === "orders") return openOrders();
    if (action.kind === "add_listing") return go("SellerStore", { mode: "create" });
    const item = summary?.top_items.find((entry) => entry.item_id === action.itemId);
    return openListing(item?.title || null);
  }, [go, openListing, openOrders, summary, tip]);

  /* --------------------------------------------------------------- export */

  /**
   * INTERIM: this app has no Reports surface and no export flow to hand off to.
   *
   * Rather than ship a pill that goes nowhere, Export shares a CSV built from
   * *the same response the screen rendered* — not a second query — so the file
   * and the screen cannot disagree. When a Reports/export flow exists this
   * becomes a navigate call and the CSV builder goes with it. Flagged in the
   * report as an interim behaviour.
   */
  const exportCsv = useCallback(async () => {
    // Mirrors the disabled state below. A Pressable's `disabled` prop is the
    // affordance; this is the guard, so a programmatic call cannot slip past it.
    if (!summary || exporting || load?.fromCache) return;
    setExporting(true);
    try {
      const lines = [
        `Insights,${PERIOD_LABELS[summary.period]}`,
        `Window,${summary.start},${summary.end}`,
        `Currency,${summary.currency}`,
        "",
        "Date,Revenue,Orders",
        ...summary.series.map(
          (bucket) => `${bucket.date},${(bucket.revenue_minor / 100).toFixed(2)},${bucket.orders}`
        ),
        "",
        "Source,Revenue,Orders",
        ...summary.sources.map(
          (source) =>
            `${SOURCE_LABELS[source.key]},${(source.revenue_minor / 100).toFixed(2)},${source.orders}`
        ),
        "",
        "Listing,Revenue,Orders",
        ...summary.top_items.map(
          (item) =>
            `"${(item.title || item.item_id).replace(/"/g, '""')}",${(item.revenue_minor / 100).toFixed(2)},${item.orders}`
        )
      ];
      await Share.share({
        title: `Insights — ${PERIOD_LABELS[summary.period]}`,
        message: lines.join("\n")
      });
    } catch {
      // A cancelled share sheet is not an error worth a banner.
    } finally {
      setExporting(false);
    }
  }, [exporting, load?.fromCache, summary]);

  /* -------------------------------------------------------------- modules */

  const retry = useCallback(() => {
    fetchPeriod(period, "switch").catch(() => undefined);
  }, [fetchPeriod, period]);

  /**
   * The recovery, chosen by cause rather than assumed to be a retry.
   *
   * Three outcomes, and each of them is a different control: retry the request,
   * go and sign in, or offer nothing because nothing would help. Passing `null`
   * removes the button rather than disabling it, because a disabled Retry on a
   * failure the reader cannot fix is still a control that promises a way out.
   */
  const failureAction: { onPress: (() => void) | null; label: string } = (() => {
    if (!failure) return { onPress: retry, label: "Try again" };
    if (!failure.actionLabel) return { onPress: null, label: "" };
    if (failure.cause === "authentication") {
      return { onPress: () => go("Login"), label: failure.actionLabel };
    }
    return { onPress: failure.retries ? retry : null, label: failure.actionLabel };
  })();

  const periodOptions = useMemo(
    (): PeriodOption[] =>
      INSIGHTS_PERIODS.map((key) => ({
        key,
        label: PERIOD_LABELS[key],
        disabledReason:
          available && !available.includes(key)
            ? "You're offline and this period hasn't been saved on this device."
            : undefined
      })),
    [available]
  );

  const kpiQuad = (() => {
    if (loading && !summary) {
      return (
        <View style={styles.kpiGrid}>
          <View style={styles.kpiRow}>
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
          </View>
          <View style={styles.kpiRow}>
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
          </View>
        </View>
      );
    }
    if (error && !summary) {
      return <StoreSectionError
          message={error}
          onRetry={failureAction.onPress}
          actionLabel={failureAction.label || "Try again"}
          reducedMotion={reducedMotion}
        />;
    }
    if (!summary) return null;

    const revenue = compareToPrior(
      summary.totals.revenue_minor,
      summary.prior_totals?.revenue_minor,
      summary.has_prior_period
    );
    const orders = compareToPrior(
      summary.totals.orders,
      summary.prior_totals?.orders,
      summary.has_prior_period
    );
    const followers = compareToPrior(
      summary.followers.gained,
      summary.followers.prior_gained,
      summary.has_prior_period
    );

    return (
      <View style={styles.kpiGrid}>
        <View style={styles.kpiRow}>
          <StoreKpiCard
            label="Revenue"
            value={money(summary.totals.revenue_minor)}
            caption={comparisonText(revenue)}
            trend={trendFor(revenue)}
            onPress={openOrders}
            destinationHint="Orders"
            reducedMotion={reducedMotion}
            delay={0}
          />
          <StoreKpiCard
            label="Orders"
            value={formatters.count(summary.totals.orders)}
            caption={comparisonText(orders)}
            trend={trendFor(orders)}
            onPress={openOrders}
            destinationHint="Orders"
            reducedMotion={reducedMotion}
            delay={STORE_STAGGER_MS}
          />
        </View>
        <View style={styles.kpiRow}>
          {/* MOCK-DATA: the design's fourth KPI is "Store views". Nothing on this
              platform counts a storefront or listing view — the post and video
              counters cover feed content only — so the tile states the absence
              instead of showing a zero that would read as a measurement. */}
          <StoreKpiCard
            label="Store views"
            // The caption already said "Not measured yet" while the value said
            // the same character the loading, failed and zero tiles said. One of
            // those two lines was carrying the meaning; now it is the value.
            value={absentValueText("not_configured", {
              notConfiguredText: "Not measured yet"
            })}
            caption="Not measured yet"
            onPress={openStore}
            destinationHint="your store"
            reducedMotion={reducedMotion}
            delay={STORE_STAGGER_MS * 2}
          />
          <StoreKpiCard
            label="New followers"
            value={formatters.count(summary.followers.gained)}
            caption={comparisonText(followers)}
            trend={trendFor(followers)}
            onPress={openAdvertising}
            destinationHint="Advertising"
            reducedMotion={reducedMotion}
            delay={STORE_STAGGER_MS * 3}
          />
        </View>
      </View>
    );
  })();

  const chartModule = (() => {
    if (loading && !summary) {
      return (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Revenue & orders</Text>
          <View style={styles.chartSkeleton}>
            <StoreSkeletonBlock width="100%" height={168} radius={8} reducedMotion={reducedMotion} />
          </View>
        </View>
      );
    }
    if (!summary) return null;

    const startDate = dayOf(summary.series[0]?.date || "");
    const endDate = dayOf(summary.series[summary.series.length - 1]?.date || "");

    return (
      <View style={styles.card}>
        <View style={styles.cardHead}>
          <Text style={styles.cardTitle}>Revenue & orders</Text>
          {startDate && endDate ? (
            <Text style={styles.cardCaption} numberOfLines={1}>
              {formatters.range(startDate, endDate)}
            </Text>
          ) : null}
        </View>
        <DualLineChart
          series={summary.series}
          bucket={summary.bucket}
          xLabels={xLabels}
          gridLabels={gridLabels}
          revenueLegend={`Revenue (${summary.currency})`}
          ordersLegend="Orders (count)"
          accessibilitySummary={chartSummary}
          width={chartWidth}
          reducedMotion={reducedMotion}
          emptyMessage="No sales in this period yet. Your first one will show up here."
        />
      </View>
    );
  })();

  const sourcesModule = (() => {
    if (loading && !summary) {
      return (
        <View style={styles.card}>
          <Text style={styles.cardTitle}>Where sales came from</Text>
          <StoreSkeletonBlock width="100%" height={44} radius={8} reducedMotion={reducedMotion} />
          <StoreSkeletonBlock width="100%" height={44} radius={8} reducedMotion={reducedMotion} />
        </View>
      );
    }
    if (!summary || summary.sources.length === 0) return null;

    return (
      <View style={styles.card}>
        <View style={styles.cardHead}>
          <Text style={styles.cardTitle}>Where sales came from</Text>
          <Pressable
            onPress={openOrders}
            hitSlop={10}
            accessibilityRole="link"
            accessibilityLabel="Details. Opens Orders, where every sale is listed."
          >
            <Text style={styles.cardLink}>Details</Text>
          </Pressable>
        </View>

        {summary.sources.map((source, index) => {
          const share = sourceShare(source, summary.sources);
          return (
            <SourceBreakdownRow
              key={source.key}
              source={source.key}
              label={SOURCE_LABELS[source.key]}
              amount={money(source.revenue_minor)}
              orders={`${formatters.count(source.orders)} ${source.orders === 1 ? "order" : "orders"}`}
              sharePercent={formatters.percent(share)}
              share={share}
              animationKey={`${period}:${summary.start}`}
              delay={index * 90}
              reducedMotion={reducedMotion}
            />
          );
        })}

        {/* MOCK-DATA: the design's third row is "From ads". The attribution engine
            is real — four models and a lookback window — but its campaign and
            channel reports accept neither a business id nor a date range, so no
            per-seller, per-period attributed figure can be taken from it. Sellers
            make spend decisions on that number, so the row does not ship and the
            breakdown falls back to the two splits the platform can prove. */}
        {!isGap(summary, "ads_attribution") ? null : (
          <Text style={styles.note}>
            Revenue from ads isn't broken out yet — the platform can't yet attribute a
            sale to a campaign for one seller over one period.
          </Text>
        )}
      </View>
    );
  })();

  const topModule = (() => {
    if (loading && !summary) {
      return (
        <View style={styles.cardFlush}>
          <Text style={styles.flushTitle}>Top performers</Text>
          <StoreRowSkeleton reducedMotion={reducedMotion} />
          <StoreRowSkeleton reducedMotion={reducedMotion} />
          <StoreRowSkeleton reducedMotion={reducedMotion} />
        </View>
      );
    }
    if (!summary || summary.top_items.length === 0) return null;

    return (
      <View style={styles.cardFlush}>
        <Text style={styles.flushTitle}>Top performers</Text>
        {summary.top_items.map((item, index) => (
          <RankedListingRow
            key={`${item.item_type}:${item.item_id}`}
            rank={index + 1}
            title={item.title || `Listing ${item.item_id}`}
            imageUrl={item.image_url}
            meta={itemMeta(item, summary)}
            revenue={money(item.revenue_minor)}
            source={item.source}
            destinationHint="Opens your store"
            onPress={() => openListing(item.title)}
            reducedMotion={reducedMotion}
          />
        ))}
      </View>
    );
  })();

  /**
   * Fulfilment health.
   *
   * All three metrics the design specifies — on-time dispatch, replies under the
   * threshold, offers answered — have no backend source. `HealthRing` is built,
   * tested and exported for the day one exists; what renders today is a single
   * honest sentence rather than three em-dashes in a row, which would read as a
   * module that failed to load rather than one that has nothing to say.
   */
  const ringsModule = (() => {
    if (!summary) return null;
    const missing = ["on_time_dispatch", "reply_rate", "offers_answered"].filter((key) =>
      isGap(summary, key)
    );
    if (missing.length < 3) return null;
    return (
      <View style={styles.card}>
        <Text style={styles.cardTitle}>Fulfilment health</Text>
        {/* MOCK-DATA: on-time dispatch needs a promised ship-by date and a recorded
            dispatch time; reply rate needs first-response latency per conversation;
            offers answered needs a live offers table. None exist. */}
        <Text style={styles.note}>
          On-time dispatch, reply speed and offers answered aren't measured yet. They'll
          appear here once orders record a promised ship-by date and messages record a
          first reply.
        </Text>
      </View>
    );
  })();

  const tipModule = (() => {
    if (!tip || !tipVisible) return null;
    // The estimate is trailing revenue over the observed window scaled to seven
    // days — arithmetic on what already happened, not a forecast, which is why
    // the copy says "was earning".
    const body =
      tip.weeklyRunRateMinor !== null
        ? tip.body.replace("{rate}", money(tip.weeklyRunRateMinor))
        : tip.body;
    return (
      <TipCard
        title={tip.title}
        body={body}
        actionLabel={tip.actionLabel}
        onAction={runTipAction}
        onDismiss={dismissTip}
        destinationHint={
          tip.action.kind === "orders"
            ? "Opens Orders"
            : tip.action.kind === "add_listing"
              ? "Opens the listing composer"
              : "Opens your store"
        }
        reducedMotion={reducedMotion}
      />
    );
  })();

  /* --------------------------------------------------------------- render */

  const offlineNote =
    load?.fromCache && load.cachedAt
      ? `You're offline. Showing figures saved ${formatters.relative(new Date(load.cachedAt).toISOString())}.`
      : null;

  /**
   * Export is blocked while the screen is showing cached figures.
   *
   * The on-screen numbers carry their own "saved {time} ago" caveat, but an
   * exported file does not — it outlives the banner, gets attached to an email,
   * and is read months later as a record. Shipping a stale CSV that looks
   * identical to a fresh one is the dishonest option, so the pill dims and says
   * why out loud rather than quietly producing yesterday's revenue.
   */
  const legacyExportBlockedReason = load?.fromCache
    ? "Not available while offline — these figures are from a saved copy."
    : null;

  /**
   * Why Export is off, in one sentence, or `null` when it is on.
   *
   * The cached case was the only one the screen covered. Three others reached
   * the same dimmed pill with nothing said: still loading, the request failed,
   * and — the one that actually produces an empty file — a period with no
   * orders in it. `insightsExportBlockedReason` covers all four in the api
   * layer, so the screen renders a reason instead of deciding one.
   */
  const exportBlockedReason = insightsErrorCausesEnabled()
    ? insightsExportBlockedReason({
        summary,
        loading: loading && !summary,
        failed: Boolean(failure) && !summary,
        fromCache: Boolean(load?.fromCache)
      })
    : legacyExportBlockedReason;
  const exportDisabled = !summary || exporting || Boolean(exportBlockedReason);

  return (
    <View style={styles.root}>
      <StoreHeader
        title={route?.params?.title || "Insights"}
        query=""
        onQueryChange={() => undefined}
        onSubmitSearch={() => undefined}
        onBack={() => navigation?.goBack?.()}
        onNotifications={() => undefined}
        unreadCount={0}
        searchPlaceholder=""
        reducedMotion={reducedMotion}
        hideSearch
        hideNotifications
        accessories={
          <Pressable
            onPress={exportCsv}
            disabled={exportDisabled}
            style={[styles.exportPill, exportDisabled && styles.exportPillDisabled]}
            hitSlop={6}
            accessibilityRole="button"
            accessibilityLabel={`Export ${PERIOD_LABELS[period]}`}
            accessibilityHint={
              exportBlockedReason || "Shares a CSV of the figures on screen"
            }
            accessibilityState={{ disabled: exportDisabled }}
            testID="insights-export"
          >
            {/* Opacity alone read as a rendering fault. The lock is a second,
                non-colour signal that the control is off on purpose, and it is
                what the reason line below refers to. */}
            <Ionicons
              name={exportDisabled ? "lock-closed-outline" : "share-outline"}
              size={14}
              color={insightsLight.text.onDark}
              accessibilityElementsHidden
              importantForAccessibility="no"
            />
            <Text style={styles.exportText}>Export</Text>
          </Pressable>
        }
        below={
          <>
            <PeriodPicker
              options={periodOptions}
              value={period}
              onChange={onPeriodChange}
              reducedMotion={reducedMotion}
            />
            {/* The reason is on screen, not only in the accessibility hint. A
                hint is read by a screen reader and by nobody else, so a sighted
                seller was left with a dimmed button and no explanation. */}
            {exportBlockedReason ? (
              <Text style={styles.exportReason} testID="insights-export-reason">
                {exportBlockedReason}
              </Text>
            ) : null}
          </>
        }
      />

      <ScrollView
        style={styles.root}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => fetchPeriod(period, "refresh").catch(() => undefined)}
            tintColor={insightsLight.text.muted}
          />
        }
      >
        {offlineNote ? <StoreOfflineNote text={offlineNote} /> : null}

        {/* One crossfade wrapping every module: a period switch is a change of
            view, not a navigation, so the entrance cascade does not replay. */}
        <Animated.View style={{ opacity: fade }}>
          <Animated.View style={entrance.styleFor(SLOT.kpis)}>{kpiQuad}</Animated.View>
          <Animated.View style={entrance.styleFor(SLOT.chart)}>{chartModule}</Animated.View>
          <Animated.View style={entrance.styleFor(SLOT.sources)}>{sourcesModule}</Animated.View>
          <Animated.View style={entrance.styleFor(SLOT.top)}>{topModule}</Animated.View>
          <Animated.View style={entrance.styleFor(SLOT.rings)}>{ringsModule}</Animated.View>
          <Animated.View style={entrance.styleFor(SLOT.tip)}>{tipModule}</Animated.View>
        </Animated.View>

        {error && summary ? (
          <StoreSectionError
          message={error}
          onRetry={failureAction.onPress}
          actionLabel={failureAction.label || "Try again"}
          reducedMotion={reducedMotion}
        />
        ) : null}

        {/* Roadmap for the Insights section, below the live charts. */}
        <BusinessOsModules section="insights" />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: insightsLight.bg.page },
  content: {
    gap: insightsLight.space.gutter,
    paddingTop: insightsLight.space.gutter,
    paddingBottom: BOTTOM_NAV_CONTENT_CLEARANCE + 24
  },
  kpiGrid: { gap: insightsLight.space.gutter, paddingHorizontal: insightsLight.space.card },
  kpiRow: { flexDirection: "row", gap: insightsLight.space.gutter },
  card: {
    marginHorizontal: insightsLight.space.card,
    padding: insightsLight.space.card,
    gap: 10,
    backgroundColor: insightsLight.bg.card,
    borderRadius: insightsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: insightsLight.border.hairline
  },
  /** Rows go edge to edge; only the heading is inset. */
  cardFlush: { backgroundColor: insightsLight.bg.card },
  flushTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: insightsLight.text.primary,
    paddingHorizontal: insightsLight.space.card,
    paddingTop: insightsLight.space.card,
    paddingBottom: 6
  },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  cardTitle: { flex: 1, fontSize: 15, fontWeight: "800", color: insightsLight.text.primary },
  cardCaption: { fontSize: 11, color: insightsLight.text.muted, fontWeight: "600" },
  cardLink: { fontSize: 13, fontWeight: "700", color: insightsLight.accent.orange },
  chartSkeleton: { paddingVertical: 4 },
  note: { fontSize: 12, lineHeight: 17, color: insightsLight.text.muted },
  exportPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    minHeight: 32,
    paddingHorizontal: 12,
    borderRadius: insightsLight.radius.pill,
    backgroundColor: "rgba(255,255,255,0.14)"
  },
  exportPillDisabled: { opacity: 0.45 },
  // No `numberOfLines`: the reason is the only explanation the pill has, and a
  // truncated reason is no reason at all.
  exportReason: {
    fontSize: 12,
    lineHeight: 17,
    color: insightsLight.text.onDarkMuted,
    paddingHorizontal: insightsLight.space.card,
    paddingBottom: 8
  },
  exportText: { fontSize: 12, fontWeight: "700", color: insightsLight.text.onDark }
});

/**
 * Promote your content — the Post Ads home (mobile-first refinement).
 *
 * This is the real, single-page post-promotion surface that replaced the "Post
 * ads isn't switched on yet" placeholder and the sample-data rails. The feature
 * shape is unchanged from the approved design; this pass refines it for the
 * phone: a compact two-part hero, a responsive horizontal card carousel for
 * picking content, campaign-setup rows that open focused bottom-sheet editors
 * instead of stacking inline, a truthful summary, and a sticky Continue that
 * clears the safe area.
 *
 * Everything shown is real:
 *   • eligibility is the server's verdict — Select is offered only when the
 *     server says `promotable`; other items show *why* they can't be promoted.
 *   • the ad *is* the existing content — the promotion references the original
 *     object; nothing is duplicated or reposted.
 *   • Goal options and their enabled flags come from
 *     `GET /api/promotions/eligibility`. Audience and Placement are "Automatic"
 *     because that is what the server stores — shown read-only, never as a
 *     picker that fabricates choices the engine doesn't offer.
 *   • there is NO "Estimated results" block: no approved forecasting provider is
 *     configured, so the summary states that plainly instead of inventing reach.
 *   • budget spends from the ONE shared Ad Wallet — there is no Post-Ads balance.
 *
 * State lives in the shared promotion draft (`promotionDraft` +
 * `promotionDraftStore`), the same store the wizard reads, so Continue simply
 * persists `step: "review"` and opens the wizard on its Review stage — the
 * idempotency key minted at draft creation carries through to Submit.
 */

import { Ionicons } from "@expo/vector-icons";
import { ReactElement, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  PromotableContentItem,
  PromotionEligibility,
  PromotionGoalOption,
  appendPromotablePage,
  getPromotionEligibility,
  listPromotableContent
} from "../../api/promotions";
import { PulseApiError } from "../../api/pulseApi";
import {
  PROMOTION_MAX_DURATION_DAYS,
  PROMOTION_MIN_DURATION_DAYS,
  PromotionContentSelection,
  createPromotionDraft,
  parsePromotionBudgetCents,
  promotionGoalLabel,
  promotionGoalsForContent,
  validatePromotionStep,
  withSelectedContent
} from "../../advertising/promotionDraft";
import {
  hydratePromotionDraft,
  persistPromotionDraft,
  updatePromotionDraft,
  usePromotionDraft
} from "../../advertising/promotionDraftStore";
import { useFormatters } from "../../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/BottomNavVisibility";
import { adsLight } from "../../theme/adsLight";

const PAGE_SIZE = 12;
const PREVIEW_COUNT = 6;
const DURATION_PRESETS = [3, 7, 14, 30];
const CARD_GAP = 10;
const GUTTER = adsLight.space.gutter;
const SKELETON_KEYS = ["s1", "s2", "s3"];

const CONTENT_TYPE_LABELS: Record<string, string> = {
  post: "Post",
  reel: "Reel",
  live_replay: "Live replay"
};

/** Three honest reasons to promote — no metrics, no counts, no forecasts. */
const BENEFITS: { icon: keyof typeof Ionicons.glyphMap; label: string }[] = [
  { icon: "flash-outline", label: "Quick setup" },
  { icon: "shield-checkmark-outline", label: "Reviewed before delivery" },
  { icon: "wallet-outline", label: "Same ad wallet" }
];

/**
 * Non-promotable verdicts get a short status pill so the owner sees *why* the
 * content can't be promoted right now instead of a dead button.
 */
const STATUS_PILL: Record<string, { label: string; tone: "info" | "warning" | "muted" }> = {
  ACTIVE_PROMOTION: { label: "Promoting", tone: "info" },
  UNDER_REVIEW: { label: "In review", tone: "warning" },
  PRIVATE: { label: "Private", tone: "muted" },
  REPLAY_PROCESSING: { label: "Processing", tone: "muted" },
  PROCESSING: { label: "Processing", tone: "muted" },
  MODERATION_BLOCKED: { label: "Not eligible", tone: "warning" },
  NOT_ELIGIBLE: { label: "Not eligible", tone: "muted" }
};

type SetupSheet = "goal" | "budget" | "duration";

type PromoteNavParams = {
  mode: "promote";
  title: string;
  accountId?: number;
};

type Props = {
  /** True when the Post pane is the active tab; the pane is display:none otherwise. */
  visible: boolean;
  accountId?: number;
  navigation?: { navigate: (route: string, params?: PromoteNavParams) => void; goBack?: () => void };
};

function contentTypeLabel(type: string): string {
  return CONTENT_TYPE_LABELS[type] || "Content";
}

function contentTypeIcon(type: string): keyof typeof Ionicons.glyphMap {
  if (type === "reel") return "film-outline";
  if (type === "live_replay") return "radio-outline";
  return "image-outline";
}

function durationLabel(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "";
  const total = Math.round(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m}:${String(s).padStart(2, "0")}` : `0:${String(s).padStart(2, "0")}`;
}

function formatUsd(cents: number): string {
  const value = Math.max(0, Math.round(cents)) / 100;
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function PromoteContentPane({ visible, accountId, navigation }: Props) {
  const fmt = useFormatters();
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const draft = usePromotionDraft();

  const [items, setItems] = useState<PromotableContentItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string>("");
  const [nextOffset, setNextOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadedOnce, setLoadedOnce] = useState(false);
  const [seeAll, setSeeAll] = useState(false);

  const [eligibility, setEligibility] = useState<PromotionEligibility | null>(null);
  const [eligibilityLoading, setEligibilityLoading] = useState(false);
  const [eligibilityError, setEligibilityError] = useState<string>("");

  const [activeSheet, setActiveSheet] = useState<SetupSheet | null>(null);
  const [attempted, setAttempted] = useState(false);

  const requestToken = useRef(0);
  const hydratedRef = useRef(false);

  const selected = draft.content;

  /**
   * Responsive card width. On a wide iPhone three compact cards fit across; on a
   * narrow one the same width yields a peek-and-scroll carousel. No hardcoded
   * widths — everything flexes from the live viewport.
   */
  const cardW = useMemo(() => {
    const available = width - GUTTER * 2 - CARD_GAP * 2;
    return Math.round(Math.max(120, Math.min(168, available / 3)));
  }, [width]);
  const thumbH = Math.round(cardW * 0.72);
  const snap = cardW + CARD_GAP;

  /* ------------------------------------------------------------------ *
   * Content list
   * ------------------------------------------------------------------ */
  const load = useCallback(async (mode: "initial" | "refresh") => {
    const token = ++requestToken.current;
    if (mode === "refresh") setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const page = await listPromotableContent({ filter: "all", limit: PAGE_SIZE, offset: 0 });
      if (token !== requestToken.current) return;
      setItems(page.items);
      setNextOffset(page.nextOffset);
      setHasMore(page.hasMore);
    } catch (err) {
      if (token !== requestToken.current) return;
      setError(err instanceof PulseApiError ? err.message : "We couldn't load your content. Try again.");
      setItems([]);
      setHasMore(false);
    } finally {
      if (token === requestToken.current) {
        setLoading(false);
        setRefreshing(false);
        setLoadedOnce(true);
      }
    }
  }, []);

  // Load on first reveal, and resume any in-progress draft so the picker and the
  // setup rows reflect where the owner left off.
  useEffect(() => {
    if (!visible) return;
    if (!hydratedRef.current) {
      hydratedRef.current = true;
      void hydratePromotionDraft();
    }
    if (!loadedOnce) void load("initial");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const onRefresh = useCallback(() => {
    void load("refresh");
  }, [load]);

  const loadMore = useCallback(async () => {
    if (loadingMore || loading || refreshing || !hasMore) return;
    const token = requestToken.current;
    setLoadingMore(true);
    try {
      const page = await listPromotableContent({ filter: "all", limit: PAGE_SIZE, offset: nextOffset });
      if (token !== requestToken.current) return;
      setItems((prev) => appendPromotablePage(prev, page));
      setNextOffset(page.nextOffset);
      setHasMore(page.hasMore);
    } catch {
      if (token === requestToken.current) setHasMore(false);
    } finally {
      if (token === requestToken.current) setLoadingMore(false);
    }
  }, [hasMore, loading, loadingMore, nextOffset, refreshing]);

  /* ------------------------------------------------------------------ *
   * Server eligibility for the selected content — authoritative goals,
   * billing readiness and forecasting state.
   * ------------------------------------------------------------------ */
  const contentKey = selected ? `${selected.contentType}:${selected.contentId}` : "";
  useEffect(() => {
    if (!selected) {
      setEligibility(null);
      setEligibilityError("");
      return;
    }
    let cancelled = false;
    const { contentType, contentId } = selected;
    setEligibilityLoading(true);
    setEligibilityError("");
    getPromotionEligibility(contentType, contentId)
      .then((value) => {
        if (!cancelled) setEligibility(value);
      })
      .catch((err) => {
        if (!cancelled) {
          setEligibilityError(err instanceof PulseApiError ? err.message : "Couldn't load promotion options.");
        }
      })
      .finally(() => {
        if (!cancelled) setEligibilityLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contentKey]);

  const goalOptions: PromotionGoalOption[] = useMemo(() => {
    if (eligibility && eligibility.goals.length) return eligibility.goals;
    const type = selected?.contentType || "post";
    return promotionGoalsForContent(type).map((key) => ({
      key,
      label: promotionGoalLabel(key),
      enabled: true,
      reason: ""
    }));
  }, [eligibility, selected?.contentType]);

  const forecastingMessage =
    eligibility?.forecastingMessage ||
    "Estimated results aren't available yet, so we don't show a forecast for this promotion.";

  const budgetCents = parsePromotionBudgetCents(draft.budgetAmount);
  const estimatedTotalCents = draft.budgetType === "daily" ? budgetCents * draft.durationDays : budgetCents;

  const reviewIssues = useMemo(() => validatePromotionStep("review", draft), [draft]);
  const canContinue = reviewIssues.length === 0;

  const selectedGoalLabel = draft.goal
    ? goalOptions.find((g) => g.key === draft.goal)?.label || promotionGoalLabel(draft.goal)
    : "";

  /* ------------------------------------------------------------------ *
   * Draft mutations
   * ------------------------------------------------------------------ */
  const selectContent = useCallback((item: PromotableContentItem) => {
    const selection: PromotionContentSelection = {
      contentType: item.contentType,
      contentId: item.contentId,
      title: item.title,
      thumbnailUrl: item.thumbnailUrl,
      mediaKind: item.mediaKind
    };
    updatePromotionDraft((current) => withSelectedContent(current, selection));
    void persistPromotionDraft();
    setAttempted(false);
    setActiveSheet("goal");
  }, []);

  const clearSelection = useCallback(() => {
    updatePromotionDraft(() => createPromotionDraft());
    void persistPromotionDraft();
    setActiveSheet(null);
    setAttempted(false);
    setEligibility(null);
  }, []);

  const chooseGoal = useCallback((goal: string) => {
    updatePromotionDraft({ goal });
    void persistPromotionDraft();
    setActiveSheet(null);
  }, []);

  const setBudgetType = useCallback((budgetType: "total" | "daily") => {
    updatePromotionDraft({ budgetType });
    void persistPromotionDraft();
  }, []);

  const setBudgetAmount = useCallback((raw: string) => {
    // Keep only digits and a single decimal point; the reducer parses to cents.
    const cleaned = raw.replace(/[^0-9.]/g, "").replace(/(\..*)\./g, "$1");
    updatePromotionDraft({ budgetAmount: cleaned });
  }, []);

  const setDuration = useCallback((days: number) => {
    const clamped = Math.min(PROMOTION_MAX_DURATION_DAYS, Math.max(PROMOTION_MIN_DURATION_DAYS, Math.round(days)));
    updatePromotionDraft({ durationDays: clamped });
    void persistPromotionDraft();
  }, []);

  const closeSheet = useCallback(() => {
    void persistPromotionDraft();
    setActiveSheet(null);
  }, []);

  const onContinue = useCallback(() => {
    if (!canContinue) {
      setAttempted(true);
      // Open the editor for the first unresolved input so the fix is one tap away.
      const first = reviewIssues[0]?.field;
      if (first === "goal") setActiveSheet("goal");
      else if (first === "budget") setActiveSheet("budget");
      else if (first === "duration" || first === "startDate" || first === "endDate") setActiveSheet("duration");
      return;
    }
    updatePromotionDraft({ step: "review" });
    void persistPromotionDraft();
    navigation?.navigate("BusinessOsAdvertising", {
      mode: "promote",
      title: "Promote your content",
      accountId
    });
  }, [accountId, canContinue, navigation, reviewIssues]);

  /* ------------------------------------------------------------------ *
   * Content picker
   * ------------------------------------------------------------------ */
  const promotable = useMemo(() => items.filter((i) => i.promotable), [items]);
  const previewItems = seeAll ? items : promotable.slice(0, PREVIEW_COUNT);
  const showSeeAll = items.length > previewItems.length || (seeAll && promotable.length < items.length);

  const renderContentCard = (item: PromotableContentItem): ReactElement => {
    const pill = item.promotable ? null : STATUS_PILL[item.eligibility] || STATUS_PILL.NOT_ELIGIBLE;
    const duration = durationLabel(item.durationSeconds);
    const isSelected = selected?.contentType === item.contentType && selected?.contentId === item.contentId;
    return (
      <View
        key={`${item.contentType}:${item.contentId}`}
        style={[styles.card, { width: cardW }, isSelected && styles.cardSelected]}
      >
        <View style={[styles.thumbWrap, { height: thumbH }]}>
          {item.thumbnailUrl ? (
            <Image source={{ uri: item.thumbnailUrl }} style={styles.thumb} resizeMode="cover" />
          ) : (
            <View style={[styles.thumb, styles.thumbFallback]}>
              <Ionicons name={contentTypeIcon(item.contentType)} size={26} color={adsLight.post.base} />
            </View>
          )}
          <View style={[styles.typeBadge, typeBadgeStyle(item.contentType)]}>
            <Text style={[styles.typeBadgeText, typeBadgeTextStyle(item.contentType)]}>
              {contentTypeLabel(item.contentType)}
            </Text>
          </View>
          {duration ? (
            <View style={styles.durationChip}>
              <Text style={styles.durationText}>{duration}</Text>
            </View>
          ) : null}
          {pill ? (
            <View style={[styles.statusPill, pillToneStyle(pill.tone)]}>
              <Text style={[styles.statusPillText, pillToneTextStyle(pill.tone)]}>{pill.label}</Text>
            </View>
          ) : null}
        </View>

        <View style={styles.cardBody}>
          <Text style={styles.cardTitle} numberOfLines={2}>
            {item.title || contentTypeLabel(item.contentType)}
          </Text>

          {item.promotable ? (
            <Pressable
              onPress={() => selectContent(item)}
              style={[styles.selectBtn, isSelected && styles.selectBtnActive]}
              accessibilityRole="button"
              accessibilityState={{ selected: isSelected }}
              accessibilityLabel={
                isSelected
                  ? `Selected ${contentTypeLabel(item.contentType).toLowerCase()}`
                  : `Select ${contentTypeLabel(item.contentType).toLowerCase()} to promote`
              }
              hitSlop={6}
            >
              {isSelected ? (
                <>
                  <Ionicons name="checkmark" size={14} color={adsLight.post.onViolet} />
                  <Text style={styles.selectBtnActiveText}>Selected</Text>
                </>
              ) : (
                <Text style={styles.selectBtnText}>Select</Text>
              )}
            </Pressable>
          ) : (
            <Text style={styles.ineligibleReason} numberOfLines={2}>
              {item.eligibilityReason}
            </Text>
          )}
        </View>
      </View>
    );
  };

  const renderSkeletonCard = (key: string): ReactElement => (
    <View key={key} style={[styles.card, { width: cardW }]}>
      <View style={[styles.thumbWrap, { height: thumbH, backgroundColor: adsLight.bg.skeleton }]} />
      <View style={styles.cardBody}>
        <View style={[styles.skelLine, { width: "88%" }]} />
        <View style={[styles.skelLine, { width: "55%" }]} />
        <View style={[styles.skelBtn]} />
      </View>
    </View>
  );

  const renderPicker = (): ReactElement => {
    if (loading && !refreshing) {
      return (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.carousel}
          scrollEnabled={false}
        >
          {SKELETON_KEYS.map(renderSkeletonCard)}
        </ScrollView>
      );
    }
    if (error) {
      return (
        <View style={styles.stateCard}>
          <Ionicons name="cloud-offline-outline" size={22} color={adsLight.text.muted} />
          <Text style={styles.stateBody}>Couldn't load your content.</Text>
          <Pressable onPress={onRefresh} style={styles.retryBtn} accessibilityRole="button" accessibilityLabel="Retry loading content">
            <Ionicons name="refresh" size={15} color={adsLight.post.onViolet} />
            <Text style={styles.retryBtnText}>Retry</Text>
          </Pressable>
        </View>
      );
    }
    if (!items.length) {
      return (
        <View style={styles.stateCard}>
          <Ionicons name="sparkles-outline" size={22} color={adsLight.post.base} />
          <Text style={styles.stateBody}>No content available to promote yet.</Text>
          <Text style={styles.stateHint}>Post something or share a Reel — it'll show up here, ready to promote.</Text>
        </View>
      );
    }
    if (previewItems.length === 0) {
      return (
        <View style={styles.stateCard}>
          <Text style={styles.stateBody}>None of your content can be promoted right now.</Text>
          <Pressable onPress={() => setSeeAll(true)} accessibilityRole="button" accessibilityLabel="See all content" hitSlop={6}>
            <Text style={styles.seeAll}>See all</Text>
          </Pressable>
        </View>
      );
    }
    return (
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        decelerationRate="fast"
        snapToInterval={snap}
        snapToAlignment="start"
        contentContainerStyle={styles.carousel}
        onMomentumScrollEnd={(e) => {
          if (!seeAll || !hasMore) return;
          const { contentOffset, contentSize, layoutMeasurement } = e.nativeEvent;
          if (contentOffset.x + layoutMeasurement.width >= contentSize.width - snap) void loadMore();
        }}
      >
        {previewItems.map(renderContentCard)}
        {seeAll && loadingMore ? (
          <View style={[styles.card, styles.carouselSpinner, { width: cardW, height: thumbH + 90 }]}>
            <ActivityIndicator color={adsLight.post.base} />
          </View>
        ) : null}
      </ScrollView>
    );
  };

  /* ------------------------------------------------------------------ *
   * Campaign setup rows — compact; editable rows open a bottom sheet.
   * ------------------------------------------------------------------ */
  const goalIssue = attempted && !draft.goal;
  const budgetIssue = attempted && budgetCents <= 0;

  const renderSetupRow = (
    icon: keyof typeof Ionicons.glyphMap,
    label: string,
    value: string,
    opts: { onPress?: () => void; issue?: boolean; locked?: boolean }
  ): ReactElement => {
    const body = (
      <>
        <View style={styles.setupIcon}>
          <Ionicons name={icon} size={16} color={adsLight.post.base} />
        </View>
        <View style={styles.setupRowLabelWrap}>
          <Text style={styles.setupRowLabel}>{label}</Text>
          <Text style={[styles.setupRowValue, opts.issue && styles.setupRowValueIssue]} numberOfLines={1}>
            {value}
          </Text>
        </View>
        {opts.locked ? (
          <View style={styles.lockPill}>
            <Ionicons name="sparkles-outline" size={11} color={adsLight.post.base} />
            <Text style={styles.lockPillText}>Optimized</Text>
          </View>
        ) : (
          <Ionicons name="chevron-forward" size={18} color={adsLight.text.muted} />
        )}
      </>
    );
    if (opts.onPress) {
      return (
        <Pressable
          onPress={opts.onPress}
          style={styles.setupRow}
          accessibilityRole="button"
          accessibilityLabel={`${label}, ${value}`}
        >
          {body}
        </Pressable>
      );
    }
    return (
      <View style={styles.setupRow} accessibilityRole="text" accessibilityLabel={`${label}, ${value}`}>
        {body}
      </View>
    );
  };

  /* ------------------------------------------------------------------ *
   * Campaign summary (truthful — no fabricated estimates)
   * ------------------------------------------------------------------ */
  const renderSummary = (): ReactElement => (
    <View style={styles.summaryCard}>
      <Text style={styles.summaryTitle}>Campaign summary</Text>
      <SummaryLine label="Content" value={selected ? contentTypeLabel(selected.contentType) : "—"} />
      <SummaryLine label="Goal" value={selectedGoalLabel || "Not chosen"} />
      <SummaryLine label="Audience" value="Automatic" />
      <SummaryLine
        label="Budget"
        value={budgetCents > 0 ? `${formatUsd(budgetCents)} ${draft.budgetType === "daily" ? "per day" : "total"}` : "Not set"}
      />
      <SummaryLine label="Duration" value={`${draft.durationDays} ${draft.durationDays === 1 ? "day" : "days"}`} />
      {draft.budgetType === "daily" && budgetCents > 0 ? (
        <SummaryLine label="Estimated max spend" value={formatUsd(estimatedTotalCents)} />
      ) : null}
      <SummaryLine label="Placement" value="Automatic" />
      <View style={styles.summaryNote}>
        <Ionicons name="information-circle-outline" size={14} color={adsLight.text.muted} />
        <Text style={styles.summaryNoteText}>{forecastingMessage}</Text>
      </View>
    </View>
  );

  /* ------------------------------------------------------------------ *
   * Frame — one vertical scroll, sticky Continue outside it.
   * ------------------------------------------------------------------ */
  const showFooter = !!selected;
  const footerPad = Math.max(insets.bottom, 10);
  const scrollBottomPad = (showFooter ? 88 : 12) + Math.max(insets.bottom, 12) + BOTTOM_NAV_CONTENT_CLEARANCE;

  return (
    <View style={[styles.root, !visible && styles.hidden]}>
      <ScrollView
        contentContainerStyle={[styles.scrollContent, { paddingBottom: scrollBottomPad }]}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={adsLight.post.base} />}
      >
        {/* Hero — two-part: copy on the left, lightweight art on the right. */}
        <View style={styles.hero}>
          <View style={styles.heroCopy}>
            <Text style={styles.heroTitle}>Promote your content</Text>
            <Text style={styles.heroSubtitle}>Turn a post, reel, or live replay into an ad in minutes.</Text>
          </View>
          <View style={styles.heroArt}>
            <View style={styles.heroArtDisc}>
              <Ionicons name="megaphone-outline" size={30} color={adsLight.post.base} />
            </View>
            <View style={styles.heroArtBadge}>
              <Ionicons name="trending-up" size={14} color={adsLight.post.onViolet} />
            </View>
          </View>
        </View>
        <View style={styles.benefitRow}>
          {BENEFITS.map((benefit) => (
            <View key={benefit.label} style={styles.benefitItem}>
              <Ionicons name={benefit.icon} size={14} color={adsLight.post.base} />
              <Text style={styles.benefitText} numberOfLines={1}>
                {benefit.label}
              </Text>
            </View>
          ))}
        </View>

        {/* Picker */}
        <View style={styles.sectionHead}>
          <Text style={styles.sectionTitle}>Choose something to promote</Text>
          {showSeeAll ? (
            <Pressable
              onPress={() => setSeeAll((v) => !v)}
              accessibilityRole="button"
              accessibilityLabel={seeAll ? "Show fewer" : "See all content"}
              hitSlop={6}
            >
              <Text style={styles.seeAll}>{seeAll ? "Show less" : "See all"}</Text>
            </Pressable>
          ) : null}
        </View>
        {renderPicker()}

        {/* Campaign setup + summary (only after a selection) */}
        {selected ? (
          <>
            <View style={styles.selectionBar}>
              <Ionicons name={contentTypeIcon(selected.contentType)} size={15} color={adsLight.post.base} />
              <Text style={styles.selectionBarText} numberOfLines={1}>
                <Text style={styles.selectionBarStrong}>{selected.title || contentTypeLabel(selected.contentType)}</Text>
              </Text>
              <Pressable onPress={clearSelection} accessibilityRole="button" accessibilityLabel="Change content" hitSlop={6}>
                <Text style={styles.changeLink}>Change</Text>
              </Pressable>
            </View>

            <Text style={styles.sectionTitle}>Campaign setup</Text>
            <View style={styles.setupGroup}>
              {renderSetupRow("flag-outline", "Goal", selectedGoalLabel || (goalIssue ? "Pick a goal" : "Choose a goal"), {
                onPress: () => setActiveSheet("goal"),
                issue: goalIssue
              })}
              {renderSetupRow("people-outline", "Audience", "Automatic", { locked: true })}
              {renderSetupRow(
                "cash-outline",
                "Budget",
                budgetCents > 0
                  ? `${formatUsd(budgetCents)} ${draft.budgetType === "daily" ? "per day" : "total"}`
                  : budgetIssue
                  ? "Enter a budget"
                  : "Set a budget",
                { onPress: () => setActiveSheet("budget"), issue: budgetIssue }
              )}
              {renderSetupRow(
                "calendar-outline",
                "Duration",
                `${draft.durationDays} ${draft.durationDays === 1 ? "day" : "days"}`,
                { onPress: () => setActiveSheet("duration") }
              )}
              {renderSetupRow("locate-outline", "Placement", "Automatic", { locked: true })}
            </View>

            {renderSummary()}
          </>
        ) : null}
      </ScrollView>

      {/* Sticky Continue — outside the scroll, clears the home indicator. */}
      {showFooter ? (
        <View style={[styles.footer, { paddingBottom: footerPad + 8 }]}>
          <Pressable
            onPress={onContinue}
            style={[styles.continueBtn, !canContinue && styles.continueBtnDisabled]}
            accessibilityRole="button"
            accessibilityLabel="Continue to review and confirm your promotion"
            accessibilityState={{ disabled: !canContinue }}
          >
            <Text style={styles.continueText}>Continue</Text>
            <Ionicons name="arrow-forward" size={18} color={adsLight.post.onViolet} />
          </Pressable>
          <Text style={styles.continueHint}>Review and confirm your promotion.</Text>
        </View>
      ) : null}

      {/* ---- Config bottom sheets ---- */}
      <BottomSheet visible={activeSheet === "goal"} title="Goal" onClose={closeSheet}>
        {eligibilityLoading && !eligibility ? (
          <ActivityIndicator color={adsLight.post.base} style={{ marginVertical: 16 }} />
        ) : (
          goalOptions.map((option) => {
            const active = draft.goal === option.key;
            return (
              <Pressable
                key={option.key}
                onPress={() => option.enabled && chooseGoal(option.key)}
                disabled={!option.enabled}
                style={[styles.optionRow, active && styles.optionRowActive, !option.enabled && styles.optionRowDisabled]}
                accessibilityRole="radio"
                accessibilityState={{ selected: active, disabled: !option.enabled }}
                accessibilityLabel={option.label}
              >
                <View style={[styles.radio, active && styles.radioActive]}>{active ? <View style={styles.radioDot} /> : null}</View>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.optionLabel, !option.enabled && styles.optionLabelDisabled]}>{option.label}</Text>
                  {!option.enabled && option.reason ? <Text style={styles.optionReason}>{option.reason}</Text> : null}
                </View>
              </Pressable>
            );
          })
        )}
        {eligibilityError ? <Text style={styles.inlineError}>{eligibilityError}</Text> : null}
      </BottomSheet>

      <BottomSheet visible={activeSheet === "budget"} title="Budget" onClose={closeSheet} primaryLabel="Done">
        <View style={styles.segment}>
          {(["total", "daily"] as const).map((type) => {
            const active = draft.budgetType === type;
            return (
              <Pressable
                key={type}
                onPress={() => setBudgetType(type)}
                style={[styles.segmentBtn, active && styles.segmentBtnActive]}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={type === "total" ? "Total budget" : "Daily budget"}
              >
                <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{type === "total" ? "Total" : "Daily"}</Text>
              </Pressable>
            );
          })}
        </View>
        <View style={styles.amountRow}>
          <Text style={styles.amountPrefix}>$</Text>
          <TextInput
            value={draft.budgetAmount}
            onChangeText={setBudgetAmount}
            onBlur={() => void persistPromotionDraft()}
            keyboardType="decimal-pad"
            placeholder="0.00"
            placeholderTextColor={adsLight.text.muted}
            style={styles.amountInput}
            accessibilityLabel="Budget amount in dollars"
            autoFocus
          />
        </View>
        <Text style={styles.sheetHint}>Between $5.00 and $5,000.00. Spend comes from your shared Ad Wallet.</Text>
      </BottomSheet>

      <BottomSheet visible={activeSheet === "duration"} title="Duration" onClose={closeSheet} primaryLabel="Done">
        <View style={styles.presetRow}>
          {DURATION_PRESETS.map((days) => {
            const active = draft.durationDays === days;
            return (
              <Pressable
                key={days}
                onPress={() => setDuration(days)}
                style={[styles.presetChip, active && styles.presetChipActive]}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`${days} days`}
              >
                <Text style={[styles.presetText, active && styles.presetTextActive]}>{days}d</Text>
              </Pressable>
            );
          })}
        </View>
        <View style={styles.stepper}>
          <Pressable
            onPress={() => setDuration(draft.durationDays - 1)}
            disabled={draft.durationDays <= PROMOTION_MIN_DURATION_DAYS}
            style={[styles.stepBtn, draft.durationDays <= PROMOTION_MIN_DURATION_DAYS && styles.stepBtnDisabled]}
            accessibilityRole="button"
            accessibilityLabel="Decrease duration"
          >
            <Ionicons name="remove" size={20} color={adsLight.text.primary} />
          </Pressable>
          <Text style={styles.stepValue}>
            {draft.durationDays} {draft.durationDays === 1 ? "day" : "days"}
          </Text>
          <Pressable
            onPress={() => setDuration(draft.durationDays + 1)}
            disabled={draft.durationDays >= PROMOTION_MAX_DURATION_DAYS}
            style={[styles.stepBtn, draft.durationDays >= PROMOTION_MAX_DURATION_DAYS && styles.stepBtnDisabled]}
            accessibilityRole="button"
            accessibilityLabel="Increase duration"
          >
            <Ionicons name="add" size={20} color={adsLight.text.primary} />
          </Pressable>
        </View>
        <Text style={styles.sheetHint}>Between 1 and 30 days.</Text>
      </BottomSheet>
    </View>
  );
}

/* -------------------------------------------------------------------- *
 * Reusable bottom-sheet modal — one focused editor at a time.
 * -------------------------------------------------------------------- */
function BottomSheet({
  visible,
  title,
  onClose,
  primaryLabel,
  children
}: {
  visible: boolean;
  title: string;
  onClose: () => void;
  primaryLabel?: string;
  children: ReactNode;
}): ReactElement {
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.sheetBackdrop} onPress={onClose} accessibilityLabel="Close" accessibilityRole="button" />
      <View style={[styles.sheet, { paddingBottom: Math.max(insets.bottom, 12) + 8 }]}>
        <View style={styles.sheetGrabber} />
        <View style={styles.sheetHead}>
          <Text style={styles.sheetTitle}>{title}</Text>
          <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel={primaryLabel || "Close"} hitSlop={8}>
            <Text style={styles.sheetDone}>{primaryLabel || "Done"}</Text>
          </Pressable>
        </View>
        <View style={styles.sheetBody}>{children}</View>
      </View>
    </Modal>
  );
}

function SummaryLine({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <View style={styles.summaryLine}>
      <Text style={styles.summaryLabel}>{label}</Text>
      <Text style={styles.summaryValue} numberOfLines={1}>
        {value}
      </Text>
    </View>
  );
}

function typeBadgeStyle(type: string) {
  if (type === "reel") return { backgroundColor: adsLight.content.reelBg };
  if (type === "live_replay") return { backgroundColor: adsLight.content.liveBg };
  return { backgroundColor: adsLight.content.postBg };
}

function typeBadgeTextStyle(type: string) {
  if (type === "reel") return { color: adsLight.content.reelText };
  if (type === "live_replay") return { color: adsLight.content.liveText };
  return { color: adsLight.content.postText };
}

function pillToneStyle(tone: "info" | "warning" | "muted") {
  if (tone === "info") return { backgroundColor: adsLight.post.tint };
  if (tone === "warning") return { backgroundColor: adsLight.bg.warning };
  return { backgroundColor: adsLight.bg.strip };
}

function pillToneTextStyle(tone: "info" | "warning" | "muted") {
  if (tone === "info") return { color: adsLight.post.base };
  if (tone === "warning") return { color: adsLight.status.warning };
  return { color: adsLight.text.muted };
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: adsLight.bg.page
  },
  hidden: {
    display: "none"
  },
  scrollContent: {
    paddingHorizontal: GUTTER,
    paddingTop: 14,
    gap: 14
  },

  /* Hero */
  hero: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: adsLight.bg.postSurface,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.suggestion.border,
    padding: 16
  },
  heroCopy: {
    flex: 1,
    gap: 5
  },
  heroTitle: {
    fontSize: 20,
    fontWeight: "800",
    color: adsLight.text.primary
  },
  heroSubtitle: {
    fontSize: 13,
    lineHeight: 18,
    color: adsLight.text.muted
  },
  heroArt: {
    width: 64,
    height: 64,
    alignItems: "center",
    justifyContent: "center"
  },
  heroArtDisc: {
    width: 60,
    height: 60,
    borderRadius: 30,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: adsLight.post.tint
  },
  heroArtBadge: {
    position: "absolute",
    right: 0,
    bottom: 2,
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: adsLight.post.base,
    borderWidth: 2,
    borderColor: adsLight.bg.postSurface
  },
  benefitRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: -4
  },
  benefitItem: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
    paddingHorizontal: 8,
    paddingVertical: 8,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.post.tint
  },
  benefitText: {
    flexShrink: 1,
    fontSize: 11,
    fontWeight: "700",
    color: adsLight.post.base
  },

  /* Section head */
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between"
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: "800",
    color: adsLight.text.primary
  },
  seeAll: {
    fontSize: 14,
    fontWeight: "700",
    color: adsLight.text.link
  },

  /* Carousel + cards */
  carousel: {
    gap: CARD_GAP,
    paddingRight: GUTTER,
    paddingVertical: 2
  },
  card: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    overflow: "hidden"
  },
  cardSelected: {
    borderColor: adsLight.post.base,
    borderWidth: 1.5
  },
  carouselSpinner: {
    alignItems: "center",
    justifyContent: "center"
  },
  thumbWrap: {
    width: "100%",
    backgroundColor: adsLight.bg.skeleton
  },
  thumb: {
    width: "100%",
    height: "100%"
  },
  thumbFallback: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: adsLight.bg.postSurface
  },
  typeBadge: {
    position: "absolute",
    top: 6,
    left: 6,
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: adsLight.radius.pill
  },
  typeBadgeText: {
    fontSize: 10,
    fontWeight: "800"
  },
  durationChip: {
    position: "absolute",
    bottom: 6,
    right: 6,
    backgroundColor: "rgba(0,0,0,0.7)",
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 2
  },
  durationText: {
    color: "#FFFFFF",
    fontSize: 10,
    fontWeight: "700"
  },
  statusPill: {
    position: "absolute",
    bottom: 6,
    left: 6,
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: adsLight.radius.pill
  },
  statusPillText: {
    fontSize: 10,
    fontWeight: "700"
  },
  cardBody: {
    padding: 10,
    gap: 8
  },
  cardTitle: {
    fontSize: 13,
    lineHeight: 17,
    fontWeight: "700",
    color: adsLight.text.primary,
    minHeight: 34
  },
  selectBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    paddingVertical: 8,
    borderRadius: adsLight.radius.control,
    borderWidth: 1,
    borderColor: adsLight.post.base,
    backgroundColor: adsLight.bg.card
  },
  selectBtnActive: {
    backgroundColor: adsLight.post.base
  },
  selectBtnText: {
    color: adsLight.post.base,
    fontSize: 13,
    fontWeight: "700"
  },
  selectBtnActiveText: {
    color: adsLight.post.onViolet,
    fontSize: 13,
    fontWeight: "700"
  },
  ineligibleReason: {
    fontSize: 11,
    lineHeight: 15,
    color: adsLight.text.muted,
    fontStyle: "italic"
  },
  skelLine: {
    height: 11,
    borderRadius: 4,
    backgroundColor: adsLight.bg.skeleton
  },
  skelBtn: {
    height: 32,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.skeleton,
    marginTop: 2
  },

  /* States */
  stateCard: {
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 26,
    paddingHorizontal: 20,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    backgroundColor: adsLight.bg.card
  },
  stateBody: {
    fontSize: 14,
    fontWeight: "700",
    color: adsLight.text.primary,
    textAlign: "center"
  },
  stateHint: {
    fontSize: 13,
    lineHeight: 18,
    color: adsLight.text.muted,
    textAlign: "center"
  },
  retryBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    marginTop: 4,
    paddingHorizontal: 18,
    paddingVertical: 9,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.post.base
  },
  retryBtnText: {
    color: adsLight.post.onViolet,
    fontSize: 14,
    fontWeight: "700"
  },

  /* Selection bar */
  selectionBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: adsLight.post.tint,
    borderRadius: adsLight.radius.control,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  selectionBarText: {
    flex: 1,
    fontSize: 13,
    color: adsLight.text.primary
  },
  selectionBarStrong: {
    fontWeight: "800"
  },
  changeLink: {
    fontSize: 13,
    fontWeight: "700",
    color: adsLight.post.base
  },

  /* Setup rows */
  setupGroup: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    overflow: "hidden"
  },
  setupRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: adsLight.border.hairline,
    paddingHorizontal: 14,
    paddingVertical: 13,
    minHeight: adsLight.size.tapTarget
  },
  setupIcon: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: adsLight.post.tint
  },
  setupRowLabelWrap: {
    flex: 1
  },
  setupRowLabel: {
    fontSize: 12,
    fontWeight: "600",
    color: adsLight.text.muted
  },
  setupRowValue: {
    fontSize: 15,
    fontWeight: "700",
    color: adsLight.text.primary,
    marginTop: 1
  },
  setupRowValueIssue: {
    color: adsLight.status.error
  },
  lockPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.post.tint
  },
  lockPillText: {
    fontSize: 10,
    fontWeight: "700",
    color: adsLight.post.base
  },

  /* Bottom sheet */
  sheetBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.35)"
  },
  sheet: {
    backgroundColor: adsLight.bg.card,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 18,
    paddingTop: 8
  },
  sheetGrabber: {
    alignSelf: "center",
    width: 40,
    height: 4,
    borderRadius: 2,
    backgroundColor: adsLight.border.hairline,
    marginBottom: 12
  },
  sheetHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 12
  },
  sheetTitle: {
    fontSize: 17,
    fontWeight: "800",
    color: adsLight.text.primary
  },
  sheetDone: {
    fontSize: 15,
    fontWeight: "800",
    color: adsLight.post.base
  },
  sheetBody: {
    gap: 8
  },
  sheetHint: {
    fontSize: 12,
    lineHeight: 17,
    color: adsLight.text.muted,
    marginTop: 4
  },

  /* Goal options */
  optionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 11,
    paddingHorizontal: 12,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.page
  },
  optionRowActive: {
    backgroundColor: adsLight.post.tint
  },
  optionRowDisabled: {
    opacity: 0.55
  },
  radio: {
    width: 20,
    height: 20,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: adsLight.border.hairline,
    alignItems: "center",
    justifyContent: "center"
  },
  radioActive: {
    borderColor: adsLight.post.base
  },
  radioDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: adsLight.post.base
  },
  optionLabel: {
    fontSize: 15,
    fontWeight: "600",
    color: adsLight.text.primary
  },
  optionLabelDisabled: {
    color: adsLight.text.muted
  },
  optionReason: {
    fontSize: 12,
    color: adsLight.text.muted,
    marginTop: 2
  },
  inlineError: {
    fontSize: 12,
    color: adsLight.status.error,
    marginTop: 4
  },

  /* Budget */
  segment: {
    flexDirection: "row",
    backgroundColor: adsLight.bg.strip,
    borderRadius: adsLight.radius.control,
    padding: 3
  },
  segmentBtn: {
    flex: 1,
    paddingVertical: 9,
    alignItems: "center",
    borderRadius: adsLight.radius.control - 2
  },
  segmentBtnActive: {
    backgroundColor: adsLight.bg.card
  },
  segmentText: {
    fontSize: 13,
    fontWeight: "700",
    color: adsLight.text.muted
  },
  segmentTextActive: {
    color: adsLight.post.base
  },
  amountRow: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    borderRadius: adsLight.radius.control,
    paddingHorizontal: 12,
    backgroundColor: adsLight.bg.page,
    marginTop: 4
  },
  amountPrefix: {
    fontSize: 20,
    fontWeight: "700",
    color: adsLight.text.primary,
    marginRight: 4
  },
  amountInput: {
    flex: 1,
    paddingVertical: 12,
    fontSize: 20,
    fontWeight: "700",
    color: adsLight.text.primary
  },

  /* Duration */
  presetRow: {
    flexDirection: "row",
    gap: 8
  },
  presetChip: {
    flex: 1,
    alignItems: "center",
    paddingVertical: 10,
    borderRadius: adsLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    backgroundColor: adsLight.bg.page
  },
  presetChipActive: {
    backgroundColor: adsLight.post.base,
    borderColor: adsLight.post.base
  },
  presetText: {
    fontSize: 13,
    fontWeight: "700",
    color: adsLight.text.primary
  },
  presetTextActive: {
    color: adsLight.post.onViolet
  },
  stepper: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    borderRadius: adsLight.radius.control,
    paddingHorizontal: 8,
    paddingVertical: 6,
    backgroundColor: adsLight.bg.page,
    marginTop: 4
  },
  stepBtn: {
    width: 40,
    height: 40,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.card
  },
  stepBtnDisabled: {
    opacity: 0.4
  },
  stepValue: {
    fontSize: 16,
    fontWeight: "700",
    color: adsLight.text.primary
  },

  /* Summary */
  summaryCard: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: 16,
    gap: 4
  },
  summaryTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: adsLight.text.primary,
    marginBottom: 4
  },
  summaryLine: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    paddingVertical: 5
  },
  summaryLabel: {
    fontSize: 13,
    color: adsLight.text.muted
  },
  summaryValue: {
    flexShrink: 1,
    fontSize: 14,
    fontWeight: "700",
    color: adsLight.text.primary
  },
  summaryNote: {
    flexDirection: "row",
    gap: 6,
    marginTop: 8,
    paddingTop: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: adsLight.border.hairline
  },
  summaryNoteText: {
    flex: 1,
    fontSize: 12,
    lineHeight: 17,
    color: adsLight.text.muted
  },

  /* Sticky footer */
  footer: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: GUTTER,
    paddingTop: 10,
    backgroundColor: adsLight.bg.card,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: adsLight.border.hairline
  },
  continueBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 15,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.post.base
  },
  continueBtnDisabled: {
    opacity: 0.45
  },
  continueText: {
    color: adsLight.post.onViolet,
    fontSize: 16,
    fontWeight: "800"
  },
  continueHint: {
    fontSize: 12,
    color: adsLight.text.muted,
    textAlign: "center",
    marginTop: 6
  }
});

export default PromoteContentPane;

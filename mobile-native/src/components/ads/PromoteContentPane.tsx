/**
 * Promote your content — the Post Ads home.
 *
 * This is the real, single-page post-promotion surface that replaced the "Post
 * ads isn't switched on yet" placeholder and the sample-data rails. It is built
 * from three server-authoritative pieces, top to bottom:
 *
 *   1. a hero that explains what promoting does (no fake numbers, no metrics);
 *   2. "Choose something to promote" — up to three of the signed-in owner's
 *      already-published Posts / Reels / finalized Live replays from
 *      `GET /api/promotions/content`, each stamped with a server-decided
 *      eligibility verdict; "See all" expands the full paginated list; and
 *   3. once something is picked, an inline "Campaign setup" (Goal / Audience /
 *      Budget / Duration / Placement) plus a truthful "Campaign summary", and a
 *      single "Continue" that hands off to the review + submit wizard.
 *
 * Everything shown is real:
 *   • eligibility is the server's verdict — Select is offered only when the
 *     server says `promotable`; other items show *why* they can't be promoted.
 *   • the ad *is* the existing content — the promotion references the original
 *     object; nothing is duplicated or reposted.
 *   • Goal options and their enabled flags come from
 *     `GET /api/promotions/eligibility`. Audience and Placement are "Automatic"
 *     because that is what the server stores — they are shown read-only, never
 *     as a picker that fabricates choices the engine doesn't offer.
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
import { ReactElement, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
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
const PREVIEW_COUNT = 3;
const DURATION_PRESETS = [3, 7, 14, 30];

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

type SetupRow = "goal" | "budget" | "duration";

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

  const [expandedRow, setExpandedRow] = useState<SetupRow | null>(null);
  const [attempted, setAttempted] = useState(false);

  const requestToken = useRef(0);
  const hydratedRef = useRef(false);

  const selected = draft.content;

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
    setExpandedRow("goal");
    setAttempted(false);
  }, []);

  const clearSelection = useCallback(() => {
    updatePromotionDraft(() => createPromotionDraft());
    void persistPromotionDraft();
    setExpandedRow(null);
    setAttempted(false);
    setEligibility(null);
  }, []);

  const chooseGoal = useCallback((goal: string) => {
    updatePromotionDraft({ goal });
    void persistPromotionDraft();
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

  const toggleRow = useCallback((row: SetupRow) => {
    setExpandedRow((prev) => (prev === row ? null : row));
  }, []);

  const onContinue = useCallback(() => {
    if (!canContinue) {
      setAttempted(true);
      // Open the first unresolved setup row so the fix is one tap away.
      const first = reviewIssues[0]?.field;
      if (first === "goal") setExpandedRow("goal");
      else if (first === "budget") setExpandedRow("budget");
      else if (first === "duration" || first === "startDate" || first === "endDate") setExpandedRow("duration");
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

  const renderContentCard = (item: PromotableContentItem): ReactElement => {
    const pill = item.promotable ? null : STATUS_PILL[item.eligibility] || STATUS_PILL.NOT_ELIGIBLE;
    const duration = durationLabel(item.durationSeconds);
    const published = item.createdAt ? fmt.relative(item.createdAt) : "";
    const isSelected = selected?.contentType === item.contentType && selected?.contentId === item.contentId;
    return (
      <View
        key={`${item.contentType}:${item.contentId}`}
        style={[styles.card, isSelected && styles.cardSelected]}
      >
        <View style={styles.thumbWrap}>
          {item.thumbnailUrl ? (
            <Image source={{ uri: item.thumbnailUrl }} style={styles.thumb} resizeMode="cover" />
          ) : (
            <View style={[styles.thumb, styles.thumbFallback]}>
              <Text style={styles.thumbFallbackText}>{contentTypeLabel(item.contentType).charAt(0)}</Text>
            </View>
          )}
          {duration ? (
            <View style={styles.durationChip}>
              <Text style={styles.durationText}>{duration}</Text>
            </View>
          ) : null}
        </View>

        <View style={styles.cardBody}>
          <View style={styles.cardHeaderRow}>
            <View style={[styles.typeBadge, typeBadgeStyle(item.contentType)]}>
              <Text style={[styles.typeBadgeText, typeBadgeTextStyle(item.contentType)]}>
                {contentTypeLabel(item.contentType)}
              </Text>
            </View>
            {pill ? (
              <View style={[styles.statusPill, pillToneStyle(pill.tone)]}>
                <Text style={[styles.statusPillText, pillToneTextStyle(pill.tone)]}>{pill.label}</Text>
              </View>
            ) : null}
          </View>

          <Text style={styles.cardTitle} numberOfLines={2}>
            {item.title}
          </Text>
          {item.snippet ? (
            <Text style={styles.cardSnippet} numberOfLines={2}>
              {item.snippet}
            </Text>
          ) : null}
          {published ? <Text style={styles.cardMeta}>{published}</Text> : null}

          {item.promotable ? (
            <Pressable
              onPress={() => selectContent(item)}
              style={[styles.selectBtn, isSelected && styles.selectBtnActive]}
              accessibilityRole="button"
              accessibilityState={{ selected: isSelected }}
              accessibilityLabel={
                isSelected
                  ? `Selected ${contentTypeLabel(item.contentType).toLowerCase()}`
                  : `Select ${contentTypeLabel(item.contentType).toLowerCase()}`
              }
              hitSlop={6}
            >
              {isSelected ? (
                <>
                  <Ionicons name="checkmark" size={15} color={adsLight.post.onViolet} />
                  <Text style={styles.selectBtnActiveText}>Selected</Text>
                </>
              ) : (
                <Text style={styles.selectBtnText}>Select</Text>
              )}
            </Pressable>
          ) : (
            <Text style={styles.ineligibleReason}>{item.eligibilityReason}</Text>
          )}
        </View>
      </View>
    );
  };

  const renderPicker = (): ReactElement => {
    if (loading && !refreshing) {
      return (
        <View style={styles.centered}>
          <ActivityIndicator color={adsLight.post.base} />
        </View>
      );
    }
    if (error) {
      return (
        <View style={styles.centered}>
          <Text style={styles.stateTitle}>Couldn't load your content</Text>
          <Text style={styles.stateBody}>{error}</Text>
          <Pressable onPress={onRefresh} style={styles.retryBtn} accessibilityRole="button" accessibilityLabel="Retry">
            <Text style={styles.retryBtnText}>Try again</Text>
          </Pressable>
        </View>
      );
    }
    if (!items.length) {
      return (
        <View style={styles.centered}>
          <Text style={styles.stateTitle}>Nothing to promote yet</Text>
          <Text style={styles.stateBody}>
            Post something, share a Reel, or finish a live stream — it'll show up here, ready to promote.
          </Text>
        </View>
      );
    }
    return (
      <View style={styles.pickerList}>
        {!seeAll && previewItems.length === 0 ? (
          <Text style={styles.pickerNote}>
            None of your content can be promoted right now. Tap “See all” to see why.
          </Text>
        ) : (
          previewItems.map(renderContentCard)
        )}
        {seeAll && hasMore ? (
          <Pressable
            onPress={loadMore}
            style={styles.loadMoreBtn}
            accessibilityRole="button"
            accessibilityLabel="Load more content"
            disabled={loadingMore}
          >
            {loadingMore ? (
              <ActivityIndicator color={adsLight.post.base} />
            ) : (
              <Text style={styles.loadMoreText}>Load more</Text>
            )}
          </Pressable>
        ) : null}
      </View>
    );
  };

  /* ------------------------------------------------------------------ *
   * Campaign setup rows
   * ------------------------------------------------------------------ */
  const goalIssue = attempted && !draft.goal;
  const budgetIssue = attempted && parsePromotionBudgetCents(draft.budgetAmount) <= 0;

  const renderGoalRow = (): ReactElement => (
    <View style={styles.setupRow}>
      <Pressable
        onPress={() => toggleRow("goal")}
        style={styles.setupRowHead}
        accessibilityRole="button"
        accessibilityLabel="Goal"
        accessibilityState={{ expanded: expandedRow === "goal" }}
      >
        <View style={styles.setupRowLabelWrap}>
          <Text style={styles.setupRowLabel}>Goal</Text>
          <Text style={[styles.setupRowValue, goalIssue && styles.setupRowValueIssue]} numberOfLines={1}>
            {selectedGoalLabel || (goalIssue ? "Pick a goal" : "Choose a goal")}
          </Text>
        </View>
        <Ionicons
          name={expandedRow === "goal" ? "chevron-up" : "chevron-down"}
          size={18}
          color={adsLight.text.muted}
        />
      </Pressable>
      {expandedRow === "goal" ? (
        <View style={styles.setupRowBody}>
          {eligibilityLoading && !eligibility ? (
            <ActivityIndicator color={adsLight.post.base} style={{ marginVertical: 8 }} />
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
                  <View style={[styles.radio, active && styles.radioActive]}>
                    {active ? <View style={styles.radioDot} /> : null}
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={[styles.optionLabel, !option.enabled && styles.optionLabelDisabled]}>
                      {option.label}
                    </Text>
                    {!option.enabled && option.reason ? (
                      <Text style={styles.optionReason}>{option.reason}</Text>
                    ) : null}
                  </View>
                </Pressable>
              );
            })
          )}
          {eligibilityError ? <Text style={styles.inlineError}>{eligibilityError}</Text> : null}
        </View>
      ) : null}
    </View>
  );

  const renderAudienceRow = (): ReactElement => (
    <View style={styles.setupRow}>
      <View style={styles.setupRowHead}>
        <View style={styles.setupRowLabelWrap}>
          <Text style={styles.setupRowLabel}>Audience</Text>
          <Text style={styles.setupRowValue}>Automatic</Text>
        </View>
        <View style={styles.lockPill}>
          <Ionicons name="sparkles-outline" size={12} color={adsLight.post.base} />
          <Text style={styles.lockPillText}>Optimized</Text>
        </View>
      </View>
      <Text style={styles.setupRowHint}>We reach the people most likely to respond. Manual targeting isn't available yet.</Text>
    </View>
  );

  const renderBudgetRow = (): ReactElement => (
    <View style={styles.setupRow}>
      <Pressable
        onPress={() => toggleRow("budget")}
        style={styles.setupRowHead}
        accessibilityRole="button"
        accessibilityLabel="Budget"
        accessibilityState={{ expanded: expandedRow === "budget" }}
      >
        <View style={styles.setupRowLabelWrap}>
          <Text style={styles.setupRowLabel}>Budget</Text>
          <Text style={[styles.setupRowValue, budgetIssue && styles.setupRowValueIssue]} numberOfLines={1}>
            {budgetCents > 0
              ? `${formatUsd(budgetCents)} ${draft.budgetType === "daily" ? "per day" : "total"}`
              : budgetIssue
              ? "Enter a budget"
              : "Set a budget"}
          </Text>
        </View>
        <Ionicons
          name={expandedRow === "budget" ? "chevron-up" : "chevron-down"}
          size={18}
          color={adsLight.text.muted}
        />
      </Pressable>
      {expandedRow === "budget" ? (
        <View style={styles.setupRowBody}>
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
                  <Text style={[styles.segmentText, active && styles.segmentTextActive]}>
                    {type === "total" ? "Total" : "Daily"}
                  </Text>
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
            />
          </View>
          <Text style={styles.setupRowHint}>
            Between $5.00 and $5,000.00. Spend comes from your shared Ad Wallet.
          </Text>
        </View>
      ) : null}
    </View>
  );

  const renderDurationRow = (): ReactElement => (
    <View style={styles.setupRow}>
      <Pressable
        onPress={() => toggleRow("duration")}
        style={styles.setupRowHead}
        accessibilityRole="button"
        accessibilityLabel="Duration"
        accessibilityState={{ expanded: expandedRow === "duration" }}
      >
        <View style={styles.setupRowLabelWrap}>
          <Text style={styles.setupRowLabel}>Duration</Text>
          <Text style={styles.setupRowValue}>
            {draft.durationDays} {draft.durationDays === 1 ? "day" : "days"}
          </Text>
        </View>
        <Ionicons
          name={expandedRow === "duration" ? "chevron-up" : "chevron-down"}
          size={18}
          color={adsLight.text.muted}
        />
      </Pressable>
      {expandedRow === "duration" ? (
        <View style={styles.setupRowBody}>
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
              <Ionicons name="remove" size={18} color={adsLight.text.primary} />
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
              <Ionicons name="add" size={18} color={adsLight.text.primary} />
            </Pressable>
          </View>
          <Text style={styles.setupRowHint}>Between 1 and 30 days.</Text>
        </View>
      ) : null}
    </View>
  );

  const renderPlacementRow = (): ReactElement => (
    <View style={styles.setupRow}>
      <View style={styles.setupRowHead}>
        <View style={styles.setupRowLabelWrap}>
          <Text style={styles.setupRowLabel}>Placement</Text>
          <Text style={styles.setupRowValue}>Automatic</Text>
        </View>
        <View style={styles.lockPill}>
          <Ionicons name="sparkles-outline" size={12} color={adsLight.post.base} />
          <Text style={styles.lockPillText}>Optimized</Text>
        </View>
      </View>
      <Text style={styles.setupRowHint}>Your promotion is placed where it's most likely to perform.</Text>
    </View>
  );

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
        <Ionicons name="information-circle-outline" size={15} color={adsLight.text.muted} />
        <Text style={styles.summaryNoteText}>{forecastingMessage}</Text>
      </View>
    </View>
  );

  /* ------------------------------------------------------------------ *
   * Frame
   * ------------------------------------------------------------------ */
  return (
    <View style={[styles.root, !visible && styles.hidden]}>
      <ScrollView
        contentContainerStyle={[styles.scrollContent, { paddingBottom: bottomPad(insets.bottom) }]}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={adsLight.post.base} />}
      >
        {/* Hero */}
        <View style={styles.hero}>
          <Text style={styles.heroTitle}>Promote your content</Text>
          <Text style={styles.heroSubtitle}>Turn your posts, reels, or live replays into ads in minutes.</Text>
          <View style={styles.benefitRow}>
            {BENEFITS.map((benefit) => (
              <View key={benefit.label} style={styles.benefitChip}>
                <Ionicons name={benefit.icon} size={14} color={adsLight.post.base} />
                <Text style={styles.benefitText}>{benefit.label}</Text>
              </View>
            ))}
          </View>
        </View>

        {/* Picker */}
        <View style={styles.sectionHead}>
          <Text style={styles.sectionTitle}>Choose something to promote</Text>
          {items.length > PREVIEW_COUNT || (!seeAll && items.length > promotable.slice(0, PREVIEW_COUNT).length) ? (
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
              <Text style={styles.selectionBarText} numberOfLines={1}>
                Promoting: <Text style={styles.selectionBarStrong}>{selected.title || contentTypeLabel(selected.contentType)}</Text>
              </Text>
              <Pressable onPress={clearSelection} accessibilityRole="button" accessibilityLabel="Change content" hitSlop={6}>
                <Text style={styles.changeLink}>Change</Text>
              </Pressable>
            </View>

            <Text style={styles.sectionTitle}>Campaign setup</Text>
            <View style={styles.setupGroup}>
              {renderGoalRow()}
              {renderAudienceRow()}
              {renderBudgetRow()}
              {renderDurationRow()}
              {renderPlacementRow()}
            </View>

            {renderSummary()}

            <Pressable
              onPress={onContinue}
              style={[styles.continueBtn, !canContinue && styles.continueBtnDisabled]}
              accessibilityRole="button"
              accessibilityLabel="Continue to review"
              accessibilityState={{ disabled: !canContinue }}
            >
              <Text style={styles.continueText}>Continue</Text>
            </Pressable>
            <Text style={styles.continueHint}>Review and confirm your promotion.</Text>
          </>
        ) : null}
      </ScrollView>
    </View>
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

function bottomPad(inset: number) {
  return Math.max(inset, 12) + BOTTOM_NAV_CONTENT_CLEARANCE;
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
    paddingHorizontal: adsLight.space.gutter,
    paddingTop: 16,
    gap: 16
  },

  /* Hero */
  hero: {
    backgroundColor: adsLight.bg.postSurface,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.suggestion.border,
    padding: 16,
    gap: 6
  },
  heroTitle: {
    fontSize: 22,
    fontWeight: "800",
    color: adsLight.text.primary
  },
  heroSubtitle: {
    fontSize: 14,
    lineHeight: 20,
    color: adsLight.text.muted
  },
  benefitRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 8
  },
  benefitChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.post.tint
  },
  benefitText: {
    fontSize: 12,
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

  /* Picker */
  pickerList: {
    gap: 12
  },
  pickerNote: {
    fontSize: 13,
    lineHeight: 19,
    color: adsLight.text.muted
  },
  card: {
    flexDirection: "row",
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
  thumbWrap: {
    width: 96,
    backgroundColor: adsLight.bg.skeleton
  },
  thumb: {
    width: 96,
    height: "100%",
    minHeight: 96
  },
  thumbFallback: {
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: adsLight.bg.postSurface
  },
  thumbFallbackText: {
    fontSize: 28,
    fontWeight: "800",
    color: adsLight.post.base
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
    fontSize: 11,
    fontWeight: "700"
  },
  cardBody: {
    flex: 1,
    padding: 12,
    gap: 4
  },
  cardHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  typeBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: adsLight.radius.pill
  },
  typeBadgeText: {
    fontSize: 11,
    fontWeight: "700"
  },
  statusPill: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: adsLight.radius.pill
  },
  statusPillText: {
    fontSize: 11,
    fontWeight: "700"
  },
  cardTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: adsLight.text.primary,
    marginTop: 2
  },
  cardSnippet: {
    fontSize: 13,
    lineHeight: 18,
    color: adsLight.text.muted
  },
  cardMeta: {
    fontSize: 12,
    color: adsLight.text.muted,
    marginTop: 2
  },
  selectBtn: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: 8,
    paddingHorizontal: 18,
    paddingVertical: 9,
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
    fontSize: 14,
    fontWeight: "700"
  },
  selectBtnActiveText: {
    color: adsLight.post.onViolet,
    fontSize: 14,
    fontWeight: "700"
  },
  ineligibleReason: {
    marginTop: 8,
    fontSize: 12,
    lineHeight: 17,
    color: adsLight.text.muted,
    fontStyle: "italic"
  },
  loadMoreBtn: {
    marginTop: 4,
    paddingVertical: 12,
    alignItems: "center",
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    backgroundColor: adsLight.bg.card
  },
  loadMoreText: {
    fontSize: 14,
    fontWeight: "700",
    color: adsLight.post.base
  },

  /* Selection bar */
  selectionBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
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
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: adsLight.border.hairline,
    paddingHorizontal: 14,
    paddingVertical: 12
  },
  setupRowHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12
  },
  setupRowLabelWrap: {
    flex: 1
  },
  setupRowLabel: {
    fontSize: 13,
    fontWeight: "600",
    color: adsLight.text.muted
  },
  setupRowValue: {
    fontSize: 15,
    fontWeight: "700",
    color: adsLight.text.primary,
    marginTop: 2
  },
  setupRowValueIssue: {
    color: adsLight.status.error
  },
  setupRowHint: {
    fontSize: 12,
    lineHeight: 17,
    color: adsLight.text.muted,
    marginTop: 6
  },
  setupRowBody: {
    marginTop: 10,
    gap: 8
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
    fontSize: 11,
    fontWeight: "700",
    color: adsLight.post.base
  },

  /* Goal options */
  optionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 10,
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
    fontSize: 14,
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
    paddingVertical: 8,
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
    backgroundColor: adsLight.bg.page
  },
  amountPrefix: {
    fontSize: 18,
    fontWeight: "700",
    color: adsLight.text.primary,
    marginRight: 4
  },
  amountInput: {
    flex: 1,
    paddingVertical: 10,
    fontSize: 18,
    fontWeight: "700",
    color: adsLight.text.primary
  },

  /* Duration */
  presetRow: {
    flexDirection: "row",
    gap: 8
  },
  presetChip: {
    paddingHorizontal: 14,
    paddingVertical: 8,
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
    backgroundColor: adsLight.bg.page
  },
  stepBtn: {
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.card
  },
  stepBtnDisabled: {
    opacity: 0.4
  },
  stepValue: {
    fontSize: 15,
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

  /* Continue */
  continueBtn: {
    paddingVertical: 15,
    alignItems: "center",
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
    marginTop: -8
  },

  /* States */
  centered: {
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 24,
    paddingVertical: 32,
    gap: 8
  },
  stateTitle: {
    fontSize: 16,
    fontWeight: "700",
    color: adsLight.text.primary,
    textAlign: "center"
  },
  stateBody: {
    fontSize: 14,
    lineHeight: 20,
    color: adsLight.text.muted,
    textAlign: "center"
  },
  retryBtn: {
    marginTop: 12,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.post.base
  },
  retryBtnText: {
    color: adsLight.post.onViolet,
    fontSize: 14,
    fontWeight: "700"
  }
});

export default PromoteContentPane;

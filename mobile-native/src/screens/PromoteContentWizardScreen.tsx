/**
 * "Promote your content" — the promotion-creation wizard behind
 * `BusinessOsAdvertising { mode: "promote", promoteContent }`.
 *
 * Reached from the Post Ads home (`PromoteContentPane`) when the owner taps
 * Promote on an already-published Post, Reel or finalized Live replay. The
 * promotion references that original content object — nothing is duplicated or
 * reposted. This screen never authors creative; the ad *is* the existing
 * content. It only collects goal → audience → budget → duration and submits.
 *
 * One route, six internal steps driven by step state inside a persisted draft
 * (`promotionDraft` + `promotionDraftStore`), mirroring the campaign wizard.
 * Everything is server-authoritative:
 *   • eligibility, enabled goals and billing readiness come from
 *     `GET /api/promotions/eligibility` — the goals shown are the server's,
 *     with their enabled flags and reasons.
 *   • Submit posts to `POST /api/promotions` with `launch: true` and the
 *     idempotency key minted at draft creation, so a double-tap or a retry
 *     after a network failure cannot create two campaigns.
 *   • the success stage reads the *returned* status truthfully — a submitted
 *     promotion shows "In review", never "Delivering".
 *
 * Design: the Post-ads violet identity from `adsLight` — violet chrome and a
 * violet promote CTA, on the shared light commerce surface.
 */

import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  PromotableContentType,
  PromotionEligibility,
  PromotionStatus,
  createPromotion,
  getPromotionEligibility,
  promotionStatusLabel
} from "../api/promotions";
import { PulseApiError } from "../api/pulseApi";
import {
  PROMOTION_MAX_BUDGET_CENTS,
  PROMOTION_MAX_DURATION_DAYS,
  PROMOTION_MIN_BUDGET_CENTS,
  PROMOTION_MIN_DURATION_DAYS,
  PROMOTION_WIZARD_STEPS,
  PromotionContentSelection,
  PromotionDraftIssue,
  PromotionWizardStep,
  buildCreatePromotionInput,
  createPromotionDraft,
  nextPromotionStep,
  parsePromotionBudgetCents,
  previousPromotionStep,
  promotionDraftIssueFor,
  promotionGoalLabel,
  promotionGoalsForContent,
  validatePromotionStep,
  withSelectedContent
} from "../advertising/promotionDraft";
import {
  clearPromotionDraft,
  hydratePromotionDraft,
  persistPromotionDraft,
  updatePromotionDraft,
  usePromotionDraft
} from "../advertising/promotionDraftStore";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import type { RootStackParamList } from "../navigation/types";
import { adsLight } from "../theme/adsLight";

type Props = {
  route?: { params?: RootStackParamList["BusinessOsAdvertising"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

const CONTENT_TYPE_LABEL: Record<PromotableContentType, string> = {
  post: "Post",
  reel: "Reel",
  live_replay: "Live replay"
};

const STEP_LABEL: Record<PromotionWizardStep, string> = {
  content: "Content",
  goal: "Goal",
  audience: "Audience",
  budget: "Budget",
  duration: "Duration",
  review: "Review"
};

function formatUsd(cents: number): string {
  const value = Math.max(0, Math.round(cents)) / 100;
  return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Which step owns a validation field, so Submit can jump to the first problem. */
function stepForField(field: string): PromotionWizardStep | null {
  switch (field) {
    case "content":
      return "content";
    case "goal":
      return "goal";
    case "audience":
      return "audience";
    case "budget":
      return "budget";
    case "duration":
    case "startDate":
    case "endDate":
      return "duration";
    default:
      return null;
  }
}

export function PromoteContentWizardScreen({ route, navigation }: Props) {
  const insets = useSafeAreaInsets();
  const draft = usePromotionDraft();

  const seededRef = useRef(false);
  const submittingRef = useRef(false);

  const [attempted, setAttempted] = useState(false);
  const [issues, setIssues] = useState<PromotionDraftIssue[]>([]);
  const [eligibility, setEligibility] = useState<PromotionEligibility | null>(null);
  const [eligibilityLoading, setEligibilityLoading] = useState(false);
  const [eligibilityError, setEligibilityError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<{ status: PromotionStatus; message: string } | null>(null);

  const promoteContent = route?.params?.promoteContent;

  /* ------------------------------------------------------------------ *
   * Seed once — a freshly tapped item starts a clean draft on the Goal
   * step; otherwise resume whatever was persisted.
   * ------------------------------------------------------------------ */
  useEffect(() => {
    if (seededRef.current) return;
    seededRef.current = true;
    void (async () => {
      if (promoteContent && promoteContent.contentId > 0) {
        const selection: PromotionContentSelection = {
          contentType: promoteContent.contentType,
          contentId: promoteContent.contentId,
          title: promoteContent.title || "",
          thumbnailUrl: promoteContent.thumbnailUrl || "",
          mediaKind: promoteContent.mediaKind || promoteContent.contentType
        };
        const seeded = withSelectedContent(createPromotionDraft(), selection);
        updatePromotionDraft(() => ({ ...seeded, step: "goal" }));
        await persistPromotionDraft();
      } else {
        await hydratePromotionDraft();
      }
    })();
  }, [promoteContent]);

  /* ------------------------------------------------------------------ *
   * Server eligibility for the selected content — authoritative goals,
   * billing readiness and forecasting state.
   * ------------------------------------------------------------------ */
  const contentKey = draft.content ? `${draft.content.contentType}:${draft.content.contentId}` : "";
  useEffect(() => {
    if (!draft.content) {
      setEligibility(null);
      return;
    }
    let cancelled = false;
    const { contentType, contentId } = draft.content;
    setEligibilityLoading(true);
    setEligibilityError(null);
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

  const goalOptions = useMemo(() => {
    if (eligibility && eligibility.goals.length) return eligibility.goals;
    const type: PromotableContentType = draft.content?.contentType || "post";
    return promotionGoalsForContent(type).map((key) => ({
      key,
      label: promotionGoalLabel(key),
      enabled: true,
      reason: ""
    }));
  }, [eligibility, draft.content?.contentType]);

  const step = draft.step;
  const stepIndex = PROMOTION_WIZARD_STEPS.indexOf(step);
  const isReview = step === "review";

  const budgetCents = parsePromotionBudgetCents(draft.budgetAmount);
  const estimatedTotalCents = draft.budgetType === "daily" ? budgetCents * draft.durationDays : budgetCents;

  /* ------------------------------------------------------------------ *
   * Step navigation
   * ------------------------------------------------------------------ */
  const goToStep = useCallback((next: PromotionWizardStep) => {
    updatePromotionDraft({ step: next });
    void persistPromotionDraft();
    setAttempted(false);
    setIssues([]);
    setSubmitError(null);
  }, []);

  const handleContinue = useCallback(() => {
    const stepIssues = validatePromotionStep(step, draft);
    if (stepIssues.length) {
      setAttempted(true);
      setIssues(stepIssues);
      return;
    }
    const next = nextPromotionStep(step);
    if (next) goToStep(next);
  }, [step, draft, goToStep]);

  const handleBack = useCallback(() => {
    const prev = previousPromotionStep(step);
    if (prev) goToStep(prev);
    else navigation?.goBack?.();
  }, [step, goToStep, navigation]);

  const handleSubmit = useCallback(async () => {
    if (submittingRef.current) return;
    const allIssues = validatePromotionStep("review", draft);
    if (allIssues.length) {
      setAttempted(true);
      setIssues(allIssues);
      const target = stepForField(allIssues[0].field);
      if (target) goToStep(target);
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const input = buildCreatePromotionInput(draft, { launch: true });
      const result = await createPromotion(input);
      setSubmitted({
        status: result.promotion.status,
        message: result.message || "Your promotion was submitted for review."
      });
      await clearPromotionDraft();
    } catch (err) {
      setSubmitError(
        err instanceof PulseApiError ? err.message : "Couldn't submit your promotion. Please try again."
      );
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }, [draft, goToStep]);

  const goToManager = useCallback(() => {
    navigation?.navigate("BusinessOsAdvertising", { mode: "manager" });
  }, [navigation]);

  /* ------------------------------------------------------------------ *
   * Success stage — truthful status from the server response.
   * ------------------------------------------------------------------ */
  if (submitted) {
    return (
      <View style={[styles.root, styles.centerFill]}>
        <View style={styles.successBlock}>
          <View style={styles.successBadge}>
            <Ionicons name="checkmark" size={30} color={adsLight.post.onViolet} />
          </View>
          <Text style={styles.successTitle}>Promotion submitted</Text>
          <View style={styles.statusPill}>
            <Text style={styles.statusPillText}>{promotionStatusLabel(submitted.status)}</Text>
          </View>
          <Text style={styles.successBody}>{submitted.message}</Text>
          <Text style={styles.successHint}>
            We&apos;ll review it before it starts delivering. You can track its status in Advertising.
          </Text>
          <View style={styles.successActions}>
            <PrimaryButton label="Back to Advertising" onPress={goToManager} />
          </View>
        </View>
      </View>
    );
  }

  /* ------------------------------------------------------------------ *
   * Frame
   * ------------------------------------------------------------------ */
  return (
    <View style={styles.root}>
      <View style={styles.bar}>
        <Pressable
          style={styles.barButton}
          onPress={handleBack}
          accessibilityRole="button"
          accessibilityLabel="Back"
        >
          <Ionicons name="chevron-back" size={22} color={adsLight.text.primary} />
        </Pressable>
        <View style={styles.stepIndicator}>
          <Text style={styles.stepCount}>
            Step {stepIndex + 1} of {PROMOTION_WIZARD_STEPS.length}
          </Text>
          <Text style={styles.stepName}>{STEP_LABEL[step]}</Text>
        </View>
        <View style={styles.barButton} />
      </View>

      <View style={styles.progressTrack}>
        {PROMOTION_WIZARD_STEPS.map((s, i) => (
          <View key={s} style={[styles.progressSeg, i <= stepIndex && styles.progressSegActive]} />
        ))}
      </View>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={[
          styles.scrollContent,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE + 88 }
        ]}
        keyboardShouldPersistTaps="handled"
      >
        {step === "content" ? renderContent() : null}
        {step === "goal" ? renderGoal() : null}
        {step === "audience" ? renderAudience() : null}
        {step === "budget" ? renderBudget() : null}
        {step === "duration" ? renderDuration() : null}
        {step === "review" ? renderReview() : null}
      </ScrollView>

      <View style={[styles.footer, { paddingBottom: Math.max(insets.bottom, 12) }]}>
        {submitError ? <Text style={styles.footerError}>{submitError}</Text> : null}
        {isReview ? (
          <PrimaryButton
            label={submitting ? "Submitting…" : "Submit for review"}
            onPress={handleSubmit}
            disabled={submitting}
            busy={submitting}
          />
        ) : (
          <PrimaryButton label="Continue" onPress={handleContinue} disabled={!draft.content && step !== "content"} />
        )}
      </View>
    </View>
  );

  /* -------------------------------------------------------------- *
   * Step 1 — Content
   * -------------------------------------------------------------- */
  function renderContent() {
    if (!draft.content) {
      return (
        <View style={styles.stack}>
          <Text style={styles.pageTitle}>Promote your content</Text>
          <Text style={styles.pageSubtitle}>Choose something you&apos;ve already posted and reach more people.</Text>
          <View style={styles.emptyCard}>
            <Ionicons name="images-outline" size={26} color={adsLight.post.base} />
            <Text style={styles.emptyText}>Pick a post, reel or live replay to promote.</Text>
            <Pressable style={styles.secondaryButton} onPress={() => navigation?.goBack?.()}>
              <Text style={styles.secondaryText}>Choose content</Text>
            </Pressable>
          </View>
        </View>
      );
    }
    const c = draft.content;
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>You&apos;re promoting</Text>
        <Text style={styles.pageSubtitle}>This boosts your original {CONTENT_TYPE_LABEL[c.contentType].toLowerCase()} — nothing is reposted.</Text>
        <View style={styles.contentCard}>
          {c.thumbnailUrl ? (
            <Image source={{ uri: c.thumbnailUrl }} style={styles.contentThumb} />
          ) : (
            <View style={[styles.contentThumb, styles.contentThumbFallback]}>
              <Ionicons name="image-outline" size={22} color={adsLight.text.muted} />
            </View>
          )}
          <View style={styles.contentMeta}>
            <View style={styles.typeBadge}>
              <Text style={styles.typeBadgeText}>{CONTENT_TYPE_LABEL[c.contentType]}</Text>
            </View>
            <Text style={styles.contentTitle} numberOfLines={2}>
              {c.title || `Untitled ${CONTENT_TYPE_LABEL[c.contentType].toLowerCase()}`}
            </Text>
          </View>
        </View>
        {eligibilityError ? <Text style={styles.inlineError}>{eligibilityError}</Text> : null}
        <Pressable style={styles.linkRow} onPress={() => navigation?.goBack?.()}>
          <Ionicons name="swap-horizontal" size={16} color={adsLight.text.link} />
          <Text style={styles.linkText}>Choose different content</Text>
        </Pressable>
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 2 — Goal
   * -------------------------------------------------------------- */
  function renderGoal() {
    const error = attempted ? promotionDraftIssueFor(issues, "goal") : "";
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>What&apos;s your goal?</Text>
        <Text style={styles.pageSubtitle}>Pick what you want this promotion to drive.</Text>
        {eligibilityLoading && !eligibility ? (
          <View style={styles.inlineLoading}>
            <ActivityIndicator color={adsLight.post.base} />
          </View>
        ) : null}
        <View style={styles.optionGroup}>
          {goalOptions.map((option) => {
            const active = draft.goal === option.key;
            const disabled = option.enabled === false;
            return (
              <Pressable
                key={option.key}
                style={[styles.optionRow, active && styles.optionRowActive, disabled && styles.optionRowDisabled]}
                disabled={disabled}
                onPress={() => {
                  updatePromotionDraft({ goal: option.key });
                  void persistPromotionDraft();
                }}
                accessibilityRole="radio"
                accessibilityState={{ selected: active, disabled }}
                accessibilityLabel={option.label}
              >
                <View style={[styles.radioOuter, active && styles.radioOuterActive]}>
                  {active ? <View style={styles.radioInner} /> : null}
                </View>
                <View style={styles.optionTextBlock}>
                  <Text style={[styles.optionLabel, disabled && styles.optionLabelDisabled]}>{option.label}</Text>
                  {disabled && option.reason ? <Text style={styles.optionCaption}>{option.reason}</Text> : null}
                </View>
              </Pressable>
            );
          })}
        </View>
        {error ? <Text style={styles.inlineError}>{error}</Text> : null}
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 3 — Audience
   * -------------------------------------------------------------- */
  function renderAudience() {
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>Who should see it?</Text>
        <Text style={styles.pageSubtitle}>PulseSoc finds the people most likely to respond.</Text>
        <View style={[styles.optionRow, styles.optionRowActive]}>
          <View style={[styles.radioOuter, styles.radioOuterActive]}>
            <View style={styles.radioInner} />
          </View>
          <View style={styles.optionTextBlock}>
            <Text style={styles.optionLabel}>Automatic Audience</Text>
            <Text style={styles.optionCaption}>
              We target based on who already engages with your content and similar people.
            </Text>
          </View>
        </View>
        <Text style={styles.helperNote}>Custom targeting isn&apos;t available yet — it&apos;s coming.</Text>
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 4 — Budget
   * -------------------------------------------------------------- */
  function renderBudget() {
    const error = attempted ? promotionDraftIssueFor(issues, "budget") : "";
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>Set your budget</Text>
        <Text style={styles.pageSubtitle}>This is the most you&apos;ll spend. You&apos;re never charged more.</Text>
        <View style={styles.segmentTrack}>
          {(["total", "daily"] as const).map((type) => {
            const active = draft.budgetType === type;
            return (
              <Pressable
                key={type}
                style={[styles.segment, active && styles.segmentActive]}
                onPress={() => {
                  updatePromotionDraft({ budgetType: type });
                  void persistPromotionDraft();
                }}
                accessibilityRole="button"
                accessibilityState={{ selected: active }}
              >
                <Text style={[styles.segmentText, active && styles.segmentTextActive]}>
                  {type === "total" ? "Total budget" : "Daily budget"}
                </Text>
              </Pressable>
            );
          })}
        </View>
        <View style={[styles.amountField, Boolean(error) && styles.amountFieldError]}>
          <Text style={styles.amountPrefix}>$</Text>
          <TextInput
            style={styles.amountInput}
            value={draft.budgetAmount}
            onChangeText={(next) => {
              updatePromotionDraft({ budgetAmount: next.replace(/[^0-9.]/g, "") });
            }}
            onBlur={() => void persistPromotionDraft()}
            placeholder="0.00"
            placeholderTextColor={adsLight.text.muted}
            keyboardType="decimal-pad"
            maxLength={9}
          />
        </View>
        <Text style={styles.helperNote}>
          Between {formatUsd(PROMOTION_MIN_BUDGET_CENTS)} and {formatUsd(PROMOTION_MAX_BUDGET_CENTS)}.
        </Text>
        {draft.budgetType === "daily" && budgetCents > 0 ? (
          <Text style={styles.helperNote}>
            About {formatUsd(estimatedTotalCents)} over {draft.durationDays} day{draft.durationDays === 1 ? "" : "s"}.
          </Text>
        ) : null}
        {error ? <Text style={styles.inlineError}>{error}</Text> : null}
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 5 — Duration
   * -------------------------------------------------------------- */
  function renderDuration() {
    const durationError = attempted ? promotionDraftIssueFor(issues, "duration") : "";
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>How long should it run?</Text>
        <Text style={styles.pageSubtitle}>Your promotion delivers evenly across this window.</Text>
        <View style={styles.stepperRow}>
          <Pressable
            style={[styles.stepperButton, draft.durationDays <= PROMOTION_MIN_DURATION_DAYS && styles.stepperDisabled]}
            disabled={draft.durationDays <= PROMOTION_MIN_DURATION_DAYS}
            onPress={() => {
              updatePromotionDraft({ durationDays: Math.max(PROMOTION_MIN_DURATION_DAYS, draft.durationDays - 1) });
              void persistPromotionDraft();
            }}
            accessibilityRole="button"
            accessibilityLabel="Fewer days"
          >
            <Ionicons name="remove" size={22} color={adsLight.text.primary} />
          </Pressable>
          <View style={styles.stepperValueBlock}>
            <Text style={styles.stepperValue}>{draft.durationDays}</Text>
            <Text style={styles.stepperUnit}>day{draft.durationDays === 1 ? "" : "s"}</Text>
          </View>
          <Pressable
            style={[styles.stepperButton, draft.durationDays >= PROMOTION_MAX_DURATION_DAYS && styles.stepperDisabled]}
            disabled={draft.durationDays >= PROMOTION_MAX_DURATION_DAYS}
            onPress={() => {
              updatePromotionDraft({ durationDays: Math.min(PROMOTION_MAX_DURATION_DAYS, draft.durationDays + 1) });
              void persistPromotionDraft();
            }}
            accessibilityRole="button"
            accessibilityLabel="More days"
          >
            <Ionicons name="add" size={22} color={adsLight.text.primary} />
          </Pressable>
        </View>
        <Text style={styles.helperNote}>Between {PROMOTION_MIN_DURATION_DAYS} and {PROMOTION_MAX_DURATION_DAYS} days.</Text>
        {durationError ? <Text style={styles.inlineError}>{durationError}</Text> : null}
      </View>
    );
  }

  /* -------------------------------------------------------------- *
   * Step 6 — Review
   * -------------------------------------------------------------- */
  function renderReview() {
    const c = draft.content;
    const billingReady = eligibility ? eligibility.eligible : true;
    return (
      <View style={styles.stack}>
        <Text style={styles.pageTitle}>Review your promotion</Text>
        <Text style={styles.pageSubtitle}>Submitting sends it for review before it starts delivering.</Text>

        {c ? (
          <View style={styles.reviewCard}>
            <View style={styles.reviewRow}>
              <Text style={styles.reviewLabel}>Content</Text>
              <Text style={styles.reviewValue} numberOfLines={1}>
                {CONTENT_TYPE_LABEL[c.contentType]} · {c.title || "Untitled"}
              </Text>
            </View>
            <View style={styles.reviewRow}>
              <Text style={styles.reviewLabel}>Goal</Text>
              <Text style={styles.reviewValue}>{draft.goal ? promotionGoalLabel(draft.goal) : "—"}</Text>
            </View>
            <View style={styles.reviewRow}>
              <Text style={styles.reviewLabel}>Audience</Text>
              <Text style={styles.reviewValue}>Automatic</Text>
            </View>
            <View style={styles.reviewRow}>
              <Text style={styles.reviewLabel}>Budget</Text>
              <Text style={styles.reviewValue}>
                {budgetCents > 0
                  ? `${formatUsd(budgetCents)} ${draft.budgetType === "daily" ? "per day" : "total"}`
                  : "—"}
              </Text>
            </View>
            <View style={styles.reviewRow}>
              <Text style={styles.reviewLabel}>Duration</Text>
              <Text style={styles.reviewValue}>
                {draft.durationDays} day{draft.durationDays === 1 ? "" : "s"}
              </Text>
            </View>
            {draft.budgetType === "daily" && budgetCents > 0 ? (
              <View style={styles.reviewRow}>
                <Text style={styles.reviewLabel}>Max spend</Text>
                <Text style={styles.reviewValue}>About {formatUsd(estimatedTotalCents)}</Text>
              </View>
            ) : null}
          </View>
        ) : null}

        {eligibility && eligibility.forecastingMessage ? (
          <Text style={styles.helperNote}>{eligibility.forecastingMessage}</Text>
        ) : null}
        {!billingReady && eligibility?.reason ? <Text style={styles.inlineError}>{eligibility.reason}</Text> : null}

        <Text style={styles.helperNote}>
          Spend comes from your shared Ad Wallet. You won&apos;t be charged more than your budget.
        </Text>
      </View>
    );
  }
}

/* ------------------------------------------------------------------ *
 * Primary CTA — violet Post-ads identity.
 * ------------------------------------------------------------------ */
function PrimaryButton({
  label,
  onPress,
  disabled,
  busy
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityState={{ disabled: Boolean(disabled) }}
      accessibilityLabel={label}
      style={[styles.primaryWrap, disabled && styles.primaryDisabled]}
    >
      <LinearGradient
        colors={[adsLight.post.from, adsLight.post.to]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.primaryFill}
      >
        {busy ? <ActivityIndicator color={adsLight.post.onViolet} /> : <Text style={styles.primaryText}>{label}</Text>}
      </LinearGradient>
    </Pressable>
  );
}

export default PromoteContentWizardScreen;

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: adsLight.bg.page },
  centerFill: { alignItems: "center", justifyContent: "center", padding: 24 },

  bar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 8,
    paddingTop: 8,
    paddingBottom: 6
  },
  barButton: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  stepIndicator: { alignItems: "center" },
  stepCount: { fontSize: 11, fontWeight: "700", color: adsLight.text.muted, letterSpacing: 0.3 },
  stepName: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary },

  progressTrack: { flexDirection: "row", gap: 4, paddingHorizontal: 16, paddingBottom: 12 },
  progressSeg: { flex: 1, height: 4, borderRadius: 2, backgroundColor: adsLight.chart.trackEmpty },
  progressSegActive: { backgroundColor: adsLight.post.base },

  scroll: { flex: 1 },
  scrollContent: { paddingHorizontal: 16, paddingTop: 4 },
  stack: { gap: 14 },

  pageTitle: { fontSize: 22, fontWeight: "800", color: adsLight.text.primary },
  pageSubtitle: { fontSize: 14, lineHeight: 20, color: adsLight.text.muted, marginTop: -6 },

  contentCard: {
    flexDirection: "row",
    gap: 12,
    padding: 12,
    backgroundColor: adsLight.bg.postSurface,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.suggestion.border
  },
  contentThumb: { width: 68, height: 68, borderRadius: adsLight.radius.thumb, backgroundColor: adsLight.bg.skeleton },
  contentThumbFallback: { alignItems: "center", justifyContent: "center" },
  contentMeta: { flex: 1, justifyContent: "center", gap: 6 },
  typeBadge: {
    alignSelf: "flex-start",
    backgroundColor: adsLight.content.reelBg,
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: adsLight.radius.pill
  },
  typeBadgeText: { fontSize: 11, fontWeight: "700", color: adsLight.content.reelText },
  contentTitle: { fontSize: 15, fontWeight: "600", color: adsLight.text.primary },

  linkRow: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 4 },
  linkText: { fontSize: 14, fontWeight: "600", color: adsLight.text.link },

  emptyCard: {
    alignItems: "center",
    gap: 12,
    paddingVertical: 32,
    paddingHorizontal: 20,
    backgroundColor: adsLight.bg.postSurface,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.suggestion.border
  },
  emptyText: { fontSize: 14, color: adsLight.text.muted, textAlign: "center" },

  inlineLoading: { paddingVertical: 12, alignItems: "center" },

  optionGroup: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    borderRadius: adsLight.radius.card,
    backgroundColor: adsLight.bg.card,
    overflow: "hidden"
  },
  optionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
    paddingHorizontal: 14,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: adsLight.border.hairline
  },
  optionRowActive: { backgroundColor: adsLight.bg.postSurface },
  optionRowDisabled: { opacity: 0.55 },
  optionTextBlock: { flex: 1, gap: 3 },
  optionLabel: { fontSize: 15, fontWeight: "600", color: adsLight.text.primary },
  optionLabelDisabled: { color: adsLight.text.muted },
  optionCaption: { fontSize: 12, lineHeight: 17, color: adsLight.text.muted },

  radioOuter: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: adsLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center"
  },
  radioOuterActive: { borderColor: adsLight.post.base },
  radioInner: { width: 11, height: 11, borderRadius: 6, backgroundColor: adsLight.post.base },

  helperNote: { fontSize: 13, lineHeight: 18, color: adsLight.text.muted },
  inlineError: { fontSize: 13, lineHeight: 18, color: adsLight.status.error, fontWeight: "600" },

  segmentTrack: {
    flexDirection: "row",
    backgroundColor: adsLight.bg.strip,
    borderRadius: adsLight.radius.control,
    padding: 4,
    gap: 4
  },
  segment: { flex: 1, paddingVertical: 10, borderRadius: adsLight.radius.control - 2, alignItems: "center" },
  segmentActive: { backgroundColor: adsLight.bg.card },
  segmentText: { fontSize: 14, fontWeight: "600", color: adsLight.text.muted },
  segmentTextActive: { color: adsLight.text.primary },

  amountField: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.secondaryButton,
    paddingHorizontal: 16
  },
  amountFieldError: { borderColor: adsLight.status.error },
  amountPrefix: { fontSize: 24, fontWeight: "700", color: adsLight.text.primary, marginRight: 6 },
  amountInput: { flex: 1, fontSize: 24, fontWeight: "700", color: adsLight.text.primary, paddingVertical: 14 },

  stepperRow: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 28, paddingVertical: 8 },
  stepperButton: {
    width: 52,
    height: 52,
    borderRadius: 26,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.secondaryButton,
    backgroundColor: adsLight.bg.card,
    alignItems: "center",
    justifyContent: "center"
  },
  stepperDisabled: { opacity: 0.4 },
  stepperValueBlock: { alignItems: "center", minWidth: 90 },
  stepperValue: { fontSize: 40, fontWeight: "800", color: adsLight.text.primary },
  stepperUnit: { fontSize: 13, color: adsLight.text.muted, marginTop: -4 },

  reviewCard: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    paddingHorizontal: 14
  },
  reviewRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 16,
    paddingVertical: 13,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: adsLight.border.hairline
  },
  reviewLabel: { fontSize: 14, color: adsLight.text.muted },
  reviewValue: { flex: 1, textAlign: "right", fontSize: 14, fontWeight: "600", color: adsLight.text.primary },

  footer: {
    paddingHorizontal: 16,
    paddingTop: 10,
    backgroundColor: adsLight.bg.card,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: adsLight.border.hairline,
    gap: 8
  },
  footerError: { fontSize: 13, color: adsLight.status.error, fontWeight: "600", textAlign: "center" },

  primaryWrap: { borderRadius: adsLight.radius.control, overflow: "hidden" },
  primaryDisabled: { opacity: 0.5 },
  primaryFill: { paddingVertical: 16, alignItems: "center", justifyContent: "center" },
  primaryText: { fontSize: 16, fontWeight: "800", color: adsLight.post.onViolet },

  secondaryButton: {
    paddingVertical: 12,
    paddingHorizontal: 20,
    borderRadius: adsLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.post.base
  },
  secondaryText: { fontSize: 15, fontWeight: "700", color: adsLight.post.base },

  successBlock: { alignItems: "center", gap: 12, maxWidth: 420 },
  successBadge: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: adsLight.post.base,
    alignItems: "center",
    justifyContent: "center"
  },
  successTitle: { fontSize: 22, fontWeight: "800", color: adsLight.text.primary, textAlign: "center" },
  statusPill: {
    backgroundColor: adsLight.post.tint,
    paddingHorizontal: 12,
    paddingVertical: 4,
    borderRadius: adsLight.radius.pill
  },
  statusPillText: { fontSize: 13, fontWeight: "700", color: adsLight.post.base },
  successBody: { fontSize: 15, lineHeight: 21, color: adsLight.text.primary, textAlign: "center" },
  successHint: { fontSize: 13, lineHeight: 19, color: adsLight.text.muted, textAlign: "center" },
  successActions: { alignSelf: "stretch", gap: 10, marginTop: 8 }
});

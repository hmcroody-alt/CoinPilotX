/**
 * A promoted-post card (Post ads = violet).
 *
 * The whole Post-ads product is an unbacked preview today, so every card wears a
 * "Preview" tag and any figure on it is explicitly a sample, never presented as
 * a real spend. The content type — post, Reel or live replay — is shown as a
 * badge whose colour is paired with its word, so the type is never carried by
 * hue alone.
 *
 * While a promotion is delivering, a soft violet ring breathes behind the
 * thumbnail. It reads as "this is live" atmosphere; the phase pill's word is
 * what actually states the status, and the ring settles still under
 * reduce-motion or when the promotion is not promoting.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { adsLight } from "../../theme/adsLight";
import { useStorePress } from "../../theme/storeMotion";
import { useAdsPromotedRing } from "../../theme/adsMotion";
import type {
  CampaignTone,
  PromotedContentType,
  PromotionPhase
} from "../../api/adsDashboard";
import { AdsStatusPill } from "./AdsStatusPill";
import { BudgetPacingBar } from "./BudgetPacingBar";
import { PauseSwitch } from "./PauseSwitch";

/** One cell of the metric strip. Values arrive formatted, and are never a dash. */
export type PromotedPostMetric = {
  key: string;
  label: string;
  value: string;
};

export type PromotedPostCardProps = {
  contentType: PromotedContentType;
  title: string;
  phase: PromotionPhase;
  phaseLabel: string;
  phaseTone: CampaignTone;
  /** Preview-only reach, already formatted, e.g. "12.4k reached". Optional. */
  reachLabel?: string | null;
  /** Preview-only spend, already formatted. Optional. Always sample data. */
  spendLabel?: string | null;
  /**
   * The strip. Only metrics that have a source belong in it — a cell is a claim
   * that the thing above the label was measured, so a metric the product does
   * not collect is described in `metricsNote` instead of given a cell with a
   * placeholder in it. Values are never a dash; see `absentValueText`.
   */
  metrics?: PromotedPostMetric[];
  /**
   * One line naming what the strip does not measure. Rendered only alongside a
   * strip, because on its own it is a disclaimer about numbers that aren't on
   * the card. Mirrors `metricsNote` on `CampaignCard`.
   */
  metricsNote?: string | null;
  /** Violet pacing bar. Null when the promotion has no budget to pace against. */
  pacing?: { spentLabel: string; budgetLabel: string; fraction: number; hot: boolean } | null;
  /** Shown only when the promotion can actually be paused or resumed. */
  showSwitch?: boolean;
  promoting?: boolean;
  onTogglePromotion?: (next: boolean) => void;
  toggleBusy?: boolean;
  switchDisabled?: boolean;
  /** Why the switch is inert. Required whenever `switchDisabled` is true. */
  switchReason?: string | null;
  /** Rejection reason when phase is rejected. */
  rejectionReason?: string | null;
  /**
   * Edit-and-resubmit for a rejected promotion. A rejection that only says
   * "rejected" leaves the person with nowhere to go, so the reason and this
   * link always travel together.
   */
  onEdit?: () => void;
  onPress: () => void;
  reducedMotion: boolean;
};

const CONTENT_COPY: Record<PromotedContentType, { label: string; bg: string; text: string }> = {
  post: { label: "Post", bg: adsLight.content.postBg, text: adsLight.content.postText },
  reel: { label: "Reel", bg: adsLight.content.reelBg, text: adsLight.content.reelText },
  live: { label: "Live replay", bg: adsLight.content.liveBg, text: adsLight.content.liveText }
};

export function PromotedPostCard({
  contentType,
  title,
  phase,
  phaseLabel,
  phaseTone,
  reachLabel,
  spendLabel,
  metrics = [],
  metricsNote = null,
  pacing = null,
  showSwitch = false,
  promoting = false,
  onTogglePromotion,
  toggleBusy = false,
  switchDisabled = false,
  switchReason = null,
  rejectionReason,
  onEdit,
  onPress,
  reducedMotion
}: PromotedPostCardProps) {
  const press = useStorePress(reducedMotion, 0.99);
  const ring = useAdsPromotedRing(reducedMotion, phase === "promoting");
  const badge = CONTENT_COPY[contentType];

  return (
    <Animated.View style={press.style}>
      <Pressable
        style={styles.card}
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLabel={`${badge.label}: ${title}. ${phaseLabel}. Preview.`}
      >
        <View style={styles.thumbWrap}>
          <Animated.View
            pointerEvents="none"
            accessibilityElementsHidden
            importantForAccessibility="no"
            style={[
              styles.ring,
              {
                opacity: ring.interpolate({ inputRange: [0, 1], outputRange: [0.25, 0.7] }),
                transform: [{ scale: ring.interpolate({ inputRange: [0, 1], outputRange: [1, 1.06] }) }]
              }
            ]}
          />
          <View style={styles.thumb}>
            <Text style={styles.thumbGlyph}>
              {contentType === "reel" ? "▶" : contentType === "live" ? "◉" : "▦"}
            </Text>
          </View>
        </View>

        <View style={styles.body}>
          <View style={styles.topRow}>
            <View style={[styles.typeBadge, { backgroundColor: badge.bg }]}>
              <Text style={[styles.typeText, { color: badge.text }]}>{badge.label}</Text>
            </View>
            <View style={styles.previewBadge}>
              <Text style={styles.previewText}>Preview</Text>
            </View>
          </View>

          <Text style={styles.title} numberOfLines={2}>
            {title}
          </Text>

          <View style={styles.metaRow}>
            <AdsStatusPill label={phaseLabel} tone={phaseTone} reducedMotion={reducedMotion} />
            {reachLabel ? <Text style={styles.meta}>{reachLabel}</Text> : null}
            {spendLabel ? <Text style={[styles.meta, styles.money]}>{spendLabel}</Text> : null}
          </View>

          {metrics.length ? (
            <View style={styles.metrics}>
              {metrics.map((metric) => (
                <View key={metric.key} style={styles.metric}>
                  {/* No dash-specific accessibility branch any more. It used to
                      translate "—" into "not yet available" for a screen reader,
                      which meant sighted and unsighted readers were being told
                      different things — and the spoken version was the honest
                      one. Values now carry their own wording, so both readers
                      get the same sentence. */}
                  <Text
                    style={styles.metricValue}
                    numberOfLines={1}
                    accessibilityLabel={`${metric.label}, ${metric.value}`}
                  >
                    {metric.value}
                  </Text>
                  <Text style={styles.metricLabel} numberOfLines={1}>
                    {metric.label}
                  </Text>
                </View>
              ))}
            </View>
          ) : null}

          {/* Paired with the strip, like CampaignCard's. A note about what the
              strip omits is meaningless without the strip. */}
          {metrics.length && metricsNote ? (
            <Text style={styles.metricsNote}>{metricsNote}</Text>
          ) : null}

          {pacing ? (
            <BudgetPacingBar
              spentLabel={pacing.spentLabel}
              budgetLabel={pacing.budgetLabel}
              fraction={pacing.fraction}
              hot={pacing.hot}
              accent={adsLight.post.base}
              reducedMotion={reducedMotion}
            />
          ) : null}

          {showSwitch && onTogglePromotion ? (
            <View style={styles.switchWrap}>
              <PauseSwitch
                on={promoting}
                onToggle={onTogglePromotion}
                reducedMotion={reducedMotion}
                busy={toggleBusy}
                disabled={switchDisabled}
                label={promoting ? "Promoting" : "Paused"}
              />
              {switchReason ? <Text style={styles.switchReason}>{switchReason}</Text> : null}
            </View>
          ) : null}

          {phase === "rejected" && rejectionReason ? (
            <View style={styles.rejectWrap} accessibilityLiveRegion="polite">
              <Text style={styles.reject}>{rejectionReason}</Text>
              {onEdit ? (
                <Pressable
                  onPress={onEdit}
                  hitSlop={8}
                  accessibilityRole="button"
                  accessibilityLabel={`Edit and resubmit ${title}`}
                >
                  <Text style={styles.rejectAction}>Edit and resubmit ›</Text>
                </Pressable>
              ) : null}
            </View>
          ) : null}
        </View>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    gap: 12,
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: adsLight.space.card
  },
  thumbWrap: { width: adsLight.size.thumb, height: adsLight.size.thumb, alignItems: "center", justifyContent: "center" },
  ring: {
    position: "absolute",
    width: adsLight.size.thumb + 6,
    height: adsLight.size.thumb + 6,
    borderRadius: (adsLight.size.thumb + 6) / 2,
    borderWidth: 2,
    borderColor: adsLight.post.base
  },
  thumb: {
    width: adsLight.size.thumb,
    height: adsLight.size.thumb,
    borderRadius: adsLight.radius.thumb,
    backgroundColor: adsLight.post.tint,
    alignItems: "center",
    justifyContent: "center"
  },
  thumbGlyph: { fontSize: 22, color: adsLight.post.base },
  body: { flex: 1, gap: 6 },
  topRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  typeBadge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: adsLight.radius.pill },
  typeText: { fontSize: 10, fontWeight: "800" },
  previewBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.bg.skeleton,
    marginLeft: "auto"
  },
  previewText: { fontSize: 10, fontWeight: "800", color: adsLight.text.muted },
  title: { fontSize: 14, fontWeight: "700", color: adsLight.text.primary, lineHeight: 19 },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 12, flexWrap: "wrap" },
  meta: { fontSize: 12, color: adsLight.text.muted, fontWeight: "600" },
  money: { color: adsLight.money.budget, fontWeight: "800" },
  metrics: { flexDirection: "row", flexWrap: "wrap", rowGap: 8, marginTop: 2 },
  // `flexBasis: "50%"` now the strip is two cells rather than four. At 25% two
  // cells would sit in the left half of the card with dead space beside them.
  metric: { minWidth: 70, flexGrow: 1, flexBasis: "50%", gap: 1 },
  // No `numberOfLines` — §37 forbids clipping the line that says the reporting
  // has a hole in it.
  metricsNote: { fontSize: 10, color: adsLight.text.muted, lineHeight: 14, marginTop: -2 },
  metricValue: { fontSize: 13, fontWeight: "800", color: adsLight.text.primary },
  metricLabel: { fontSize: 10, color: adsLight.text.muted },
  switchWrap: { gap: 4, marginTop: 2 },
  switchReason: { fontSize: 11, color: adsLight.text.muted, lineHeight: 15 },
  rejectWrap: { gap: 4 },
  reject: { fontSize: 12, color: adsLight.status.error, lineHeight: 16 },
  rejectAction: { fontSize: 12, fontWeight: "800", color: adsLight.post.base }
});

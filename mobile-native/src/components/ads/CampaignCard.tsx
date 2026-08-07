/**
 * A Marketplace-ads campaign card.
 *
 * It answers, top to bottom: what is this campaign, is it delivering, how is it
 * pacing against budget, and what can I do about it. Everything money-related is
 * gold, the phase is stated in words next to its dot, and the only control that
 * changes delivery is a real switch.
 *
 * The pill is followed by `phaseDetail`, one line saying why the pill reads what
 * it does. A campaign can be `status='active'` and still reach nobody — the
 * selector requires seven more conditions — so "Not delivering" without the
 * reason is a status the reader can do nothing with. See `api/adsDelivery.ts`.
 *
 * `blocked_verification` is an overlay, not a phase: when the account can't
 * transact, the card keeps showing the campaign but overlays a strip that names
 * the reason and deep-links to verification, and the pause switch is disabled
 * because toggling it would only produce a backend rejection.
 *
 * The card is dumb: names, budget figures and objective all arrive formatted.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { adsLight } from "../../theme/adsLight";
import { useStorePress } from "../../theme/storeMotion";
import type { CampaignPhase, CampaignTone } from "../../api/adsDashboard";
import type { DeliveryState } from "../../api/adsDelivery";
import { AdsStatusPill } from "./AdsStatusPill";
import { BudgetPacingBar } from "./BudgetPacingBar";
import { PauseSwitch } from "./PauseSwitch";

export type CampaignCardAction = {
  key: string;
  label: string;
  onPress: () => void;
};

export type CampaignCardBudget = {
  spentLabel: string;
  budgetLabel: string;
  fraction: number;
  hot: boolean;
};

/** One cell of the four-metric strip. Values arrive formatted. */
export type CampaignCardMetric = {
  key: string;
  label: string;
  value: string;
};

export type CampaignCardProps = {
  name: string;
  /**
   * The campaign's number, already introduced by a word that says what it is.
   *
   * Present only when the name does not identify the campaign on its own — an
   * unnamed one. It renders beside the objective at the objective's weight, so
   * the name stays the largest text on the card and the number never competes
   * with it. `null` on a named campaign, which is the common case.
   */
  reference?: string | null;
  objectiveLabel: string;
  /**
   * Advisory only — the card renders `phaseLabel` and `phaseTone`, never this.
   * Widened to `DeliveryState` because the lifecycle phase and the delivery
   * state are different questions and callers now answer the second one.
   */
  phase: CampaignPhase | DeliveryState;
  phaseLabel: string;
  phaseTone: CampaignTone;
  /**
   * Why the pill reads what it reads. Required in spirit, optional in type only
   * so existing call sites compile: a pill with no explanation is the generic
   * status with no recovery action that §31 forbids.
   */
  phaseDetail?: string | null;
  /** Null when no budget is set, which the card states rather than drawing a bar. */
  budget: CampaignCardBudget | null;
  /** Spent / Impressions / Clicks / CPC. Empty array draws no strip. */
  metrics?: CampaignCardMetric[];
  /**
   * False when the campaign is not delivering, which relabels the strip as
   * historical. Spend is never presented as moving for a paused campaign.
   */
  metricsLive?: boolean;
  /**
   * One line about what the strip cannot measure — see `attributionNote` in
   * api/adsDelivery. Deliberately a sentence and not a fifth metric cell: the
   * thing being reported is the absence of a number, and a cell would have to
   * put a figure under the word "Conversions" to say so.
   */
  metricsNote?: string | null;
  /** Whether the delivery switch is shown. Drafts have nothing to pause. */
  showSwitch: boolean;
  /** Switch on = delivering. */
  delivering: boolean;
  onToggleDelivery: (next: boolean) => void;
  toggleBusy?: boolean;
  /**
   * Renders the switch inert. Always paired with `switchReason`: a switch that
   * silently no-ops is forbidden, so a disabled one must say why.
   */
  switchDisabled?: boolean;
  switchReason?: string | null;
  /** When set, the verification overlay is shown and the switch is disabled. */
  blockedVerification?: boolean;
  onVerify?: () => void;
  /** Secondary actions (duplicate, submit, archive…), already filtered to legal. */
  actions: CampaignCardAction[];
  onPress: () => void;
  reducedMotion: boolean;
};

export function CampaignCard({
  name,
  reference = null,
  objectiveLabel,
  phase,
  phaseLabel,
  phaseTone,
  phaseDetail = null,
  budget,
  metrics = [],
  metricsLive = true,
  metricsNote = null,
  showSwitch,
  delivering,
  onToggleDelivery,
  toggleBusy = false,
  switchDisabled = false,
  switchReason = null,
  blockedVerification = false,
  onVerify,
  actions,
  onPress,
  reducedMotion
}: CampaignCardProps) {
  const press = useStorePress(reducedMotion, 0.99);

  return (
    <Animated.View style={press.style}>
      <Pressable
        style={styles.card}
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLabel={
          phaseDetail
            ? `${name}, ${objectiveLabel}, ${phaseLabel}. ${phaseDetail}`
            : `${name}, ${objectiveLabel}, ${phaseLabel}`
        }
        accessibilityHint="Opens campaign details"
      >
        <View style={styles.headRow}>
          <View style={styles.headText}>
            <Text style={styles.name} numberOfLines={1}>
              {name}
            </Text>
            <Text style={styles.objective} numberOfLines={1}>
              {reference ? `${objectiveLabel} · ${reference}` : objectiveLabel}
            </Text>
          </View>
          <AdsStatusPill label={phaseLabel} tone={phaseTone} reducedMotion={reducedMotion} />
        </View>

        {phaseDetail ? <Text style={styles.phaseDetail}>{phaseDetail}</Text> : null}

        {metrics.length ? (
          <View style={styles.metrics}>
            {metrics.map((metric) => (
              <View key={metric.key} style={styles.metric}>
                <Text style={styles.metricValue} numberOfLines={1}>
                  {metric.value}
                </Text>
                <Text style={styles.metricLabel} numberOfLines={1}>
                  {metric.label}
                </Text>
              </View>
            ))}
          </View>
        ) : null}

        {metrics.length && !metricsLive ? (
          <Text style={styles.historical}>Totals to date — this campaign isn’t delivering.</Text>
        ) : null}

        {/* Paired with the strip on purpose — the note names what the strip
            leaves out, so without a strip it is a disclaimer about nothing. */}
        {metrics.length && metricsNote ? (
          <Text style={styles.metricsNote}>{metricsNote}</Text>
        ) : null}

        {budget ? (
          <BudgetPacingBar
            spentLabel={budget.spentLabel}
            budgetLabel={budget.budgetLabel}
            fraction={budget.fraction}
            hot={budget.hot}
            reducedMotion={reducedMotion}
          />
        ) : (
          <Text style={styles.noBudget}>No budget set</Text>
        )}

        {blockedVerification ? (
          <View style={styles.block} accessibilityLiveRegion="polite">
            <Ionicons name="shield-half-outline" size={16} color={adsLight.status.warning} />
            <Text style={styles.blockText}>
              This campaign can’t deliver until this ad account is verified.
            </Text>
            {/* No link when there is nothing to ask for. A request already in
                review can only be answered "already in review", and a control
                whose sole outcome is its own refusal is the active-looking
                unavailable control §37 forbids. */}
            {onVerify ? (
              <Pressable
                onPress={onVerify}
                hitSlop={8}
                accessibilityRole="button"
                accessibilityLabel="Request verification for this ad account"
              >
                <Text style={styles.blockAction}>Request ›</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        <View style={styles.footer}>
          {showSwitch ? (
            <View style={styles.switchWrap}>
              <PauseSwitch
                on={delivering}
                onToggle={onToggleDelivery}
                reducedMotion={reducedMotion}
                busy={toggleBusy}
                disabled={switchDisabled || blockedVerification}
                label={delivering ? "Delivering" : "Paused"}
              />
              {switchReason && !blockedVerification ? (
                <Text style={styles.switchReason}>{switchReason}</Text>
              ) : null}
            </View>
          ) : (
            <View />
          )}
          <View style={styles.actions}>
            {actions.map((action) => (
              <Pressable
                key={action.key}
                onPress={action.onPress}
                style={styles.actionBtn}
                accessibilityRole="button"
                accessibilityLabel={`${action.label}, ${name}`}
                hitSlop={6}
              >
                <Text style={styles.actionText}>{action.label}</Text>
              </Pressable>
            ))}
          </View>
        </View>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: adsLight.space.card,
    gap: 12
  },
  headRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  headText: { flex: 1, gap: 2 },
  name: { fontSize: 15, fontWeight: "800", color: adsLight.text.primary },
  objective: { fontSize: 12, color: adsLight.text.muted },
  // No numberOfLines: the reason a campaign isn't delivering is the one line on
  // this card that must never be clipped (§37).
  phaseDetail: { fontSize: 12, color: adsLight.text.muted, lineHeight: 17, marginTop: -6 },
  noBudget: { fontSize: 12, color: adsLight.text.muted, fontStyle: "italic" },
  // `flexWrap` is what lets the strip fall to 2x2 at large font sizes instead of
  // squeezing four columns until the numbers truncate.
  metrics: { flexDirection: "row", flexWrap: "wrap", rowGap: 10 },
  metric: { minWidth: 76, flexGrow: 1, flexBasis: "25%", gap: 2 },
  metricValue: { fontSize: 14, fontWeight: "800", color: adsLight.text.primary },
  metricLabel: { fontSize: 11, color: adsLight.text.muted },
  historical: { fontSize: 11, color: adsLight.text.muted, marginTop: -4 },
  // No `numberOfLines`. This says the reporting has a hole in it, which is the
  // other line on the card §37 forbids clipping.
  metricsNote: { fontSize: 11, color: adsLight.text.muted, lineHeight: 15, marginTop: -4 },
  switchWrap: { flexShrink: 1, gap: 4 },
  switchReason: { fontSize: 11, color: adsLight.text.muted, lineHeight: 15, maxWidth: 210 },
  block: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 10,
    borderRadius: adsLight.radius.control,
    backgroundColor: adsLight.bg.warning,
    borderWidth: 1,
    borderColor: adsLight.border.warning
  },
  blockText: { flex: 1, fontSize: 12, color: adsLight.text.primary, lineHeight: 16 },
  blockAction: { fontSize: 12, fontWeight: "800", color: adsLight.status.warning },
  footer: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  actions: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" },
  actionBtn: {
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 12,
    borderRadius: adsLight.radius.pill,
    borderWidth: 1,
    borderColor: adsLight.border.secondaryButton
  },
  actionText: { fontSize: 12, fontWeight: "700", color: adsLight.text.primary }
});

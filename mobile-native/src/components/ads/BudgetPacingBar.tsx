/**
 * The budget pacing bar on a campaign card.
 *
 * It shows how much of the period's budget has been spent, as a gold fill (gold
 * = money) that grows left-to-right once on data arrival. The important thing is
 * that the bar never stands alone: it always sits above a text line that states
 * the same fact in words — "$18 of $50 today" — so the information does not
 * depend on reading a fill length or seeing a colour.
 *
 * When pacing runs hot (spent fraction outruns the fraction of the day elapsed,
 * or simply nears the cap) the fill turns warning-orange and the text says
 * "pacing fast", because a bar that is merely a different shade of gold would
 * hide the one thing worth flagging.
 *
 * Both figures are passed in already formatted and as raw cents for the
 * fraction; this component computes no currency and presents no balance.
 */

import { Animated, StyleSheet, Text, View } from "react-native";
import { adsLight } from "../../theme/adsLight";
import { useAdsBudgetFill } from "../../theme/adsMotion";

export type BudgetPacingBarProps = {
  /** Already formatted, e.g. "$18.00". */
  spentLabel: string;
  /** Already formatted, e.g. "$50.00 per day". */
  budgetLabel: string;
  /** 0..1 spent fraction. Clamped for the fill; the label carries the truth. */
  fraction: number;
  /** True when spend is pacing ahead of plan — turns the fill and copy hot. */
  hot?: boolean;
  /**
   * Fill colour when not hot, so Post ads can run violet while Marketplace
   * keeps gold. Hot always wins: a bar about to exhaust its budget is a money
   * warning in both products and must not be recoloured into the brand hue.
   */
  accent?: string;
  reducedMotion: boolean;
};

export function BudgetPacingBar({
  spentLabel,
  budgetLabel,
  fraction,
  hot = false,
  accent,
  reducedMotion
}: BudgetPacingBarProps) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0));
  const fill = useAdsBudgetFill(reducedMotion, clamped);
  const color = hot ? adsLight.money.budgetHot : accent || adsLight.money.budget;
  const pct = Math.round(clamped * 100);

  const summary = `${spentLabel} of ${budgetLabel}, ${pct}% spent${hot ? ", pacing fast" : ""}`;

  return (
    <View style={styles.wrap} accessible accessibilityLabel={summary}>
      <View style={styles.textRow}>
        <Text style={styles.spent} numberOfLines={1}>
          {spentLabel}
        </Text>
        <Text style={styles.budget} numberOfLines={1}>
          of {budgetLabel}
        </Text>
        <Text style={[styles.pct, hot ? { color: adsLight.status.warning } : null]}>
          {hot ? `${pct}% · fast` : `${pct}%`}
        </Text>
      </View>
      <View
        style={styles.track}
        accessibilityElementsHidden
        importantForAccessibility="no"
      >
        <Animated.View
          style={[
            styles.fill,
            {
              backgroundColor: color,
              // Grows from the left edge to the spent fraction. Anchored left by
              // being the first child of a flex-start track, so `width` alone
              // moves the right edge and the left stays put.
              width: fill.interpolate({
                inputRange: [0, 1],
                outputRange: ["0%", `${clamped * 100}%`]
              })
            }
          ]}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 6 },
  textRow: { flexDirection: "row", alignItems: "baseline", gap: 6 },
  spent: { fontSize: 13, fontWeight: "800", color: adsLight.text.primary },
  budget: { fontSize: 12, color: adsLight.text.muted, flexShrink: 1 },
  pct: { fontSize: 12, fontWeight: "700", color: adsLight.text.muted, marginLeft: "auto" },
  track: {
    height: 6,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.chart.trackEmpty,
    overflow: "hidden"
  },
  fill: {
    height: "100%",
    borderRadius: adsLight.radius.pill,
    alignSelf: "flex-start"
  }
});

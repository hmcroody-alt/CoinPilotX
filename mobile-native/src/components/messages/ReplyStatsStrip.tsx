/**
 * The reply-time strip: "⚡ Avg reply {time}" on the navy band, with a "Stats"
 * link. Mint accent matches the header family.
 *
 * Honesty rules baked in via `ReplyStat` (api/commerceInbox):
 *   • No reply history → `avgLabel` absent → this renders nothing (the seller who
 *     has never replied is not shown a fabricated stat).
 *   • The "keeps your fast-responder badge" incentive framing appears ONLY when a
 *     real badge rule sources it (`showIncentive`). No badge system was found in
 *     the app, so by default the stat stands alone with no invented ranking claim.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { messagesLight } from "../../theme/messagesLight";
import { ReplyStat } from "../../api/commerceInbox";

export function ReplyStatsStrip({
  stat,
  onPressStats
}: {
  stat: ReplyStat;
  onPressStats?: () => void;
}) {
  if (!stat.avgLabel) return null;

  return (
    <View style={styles.strip} accessibilityRole="summary">
      <View style={styles.left}>
        <Text style={styles.bolt} accessibilityElementsHidden importantForAccessibility="no">
          ⚡
        </Text>
        <Text style={styles.stat} accessibilityLabel={`Average reply time ${stat.avgLabel}`}>
          Avg reply <Text style={styles.accent}>{stat.avgLabel}</Text>
        </Text>
        {stat.showIncentive && stat.incentiveThreshold ? (
          <Text style={styles.incentive} numberOfLines={1}>
            · under {stat.incentiveThreshold} keeps your fast-responder badge
          </Text>
        ) : null}
      </View>
      {onPressStats ? (
        <Pressable onPress={onPressStats} hitSlop={8} accessibilityRole="button" accessibilityLabel="Reply stats">
          <Text style={styles.link}>Stats</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  strip: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    paddingHorizontal: messagesLight.space.card,
    paddingVertical: 8,
    backgroundColor: messagesLight.replyStrip.bg
  },
  left: { flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 },
  bolt: { fontSize: 13 },
  stat: { fontSize: 13, color: messagesLight.replyStrip.text, fontWeight: "600" },
  accent: { color: messagesLight.replyStrip.accent, fontWeight: "800" },
  incentive: { fontSize: 12, color: messagesLight.replyStrip.muted, flexShrink: 1 },
  link: { fontSize: 13, color: messagesLight.replyStrip.accent, fontWeight: "700" }
});

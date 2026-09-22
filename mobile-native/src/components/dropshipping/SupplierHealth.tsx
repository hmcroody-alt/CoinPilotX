/**
 * The shared way a supplier's operating state is drawn.
 *
 * Both the hub and the suppliers screen answer "is my supplier working, and
 * what does that mean for my orders". Before this they answered it in their own
 * markup off their own data, and drifted: one showed a sandbox note, the other
 * showed a green dot, and the merchant read whichever screen they happened to
 * open. Sharing the *rendering* is the second half of sharing the endpoint —
 * one fact, one sentence, wherever it appears.
 *
 * Nothing here decides health. Every component takes values that came off
 * `/supplier-status` and puts them on screen; the copy comes from
 * `supplierStatusCopy`, which has no rendering, and this file has no rules.
 */

import { StyleSheet, Text, View } from "react-native";
import type { StoreSupplierStatus } from "../../api/dropshipping";
import {
  REAL_FULFILMENT_OFF_EXPLANATION,
  SANDBOX_EXPLANATION,
  operatingMode,
  type HealthRow
} from "../../screens/dropshipping/supplierStatusCopy";
import { storeLight } from "../../theme/storeLight";

const TONE_COLOR: Record<HealthRow["tone"], string> = {
  ok: storeLight.status.success,
  warn: storeLight.status.warning,
  muted: storeLight.text.muted
};

/**
 * Who is connected, in which environment, and whether real orders can leave.
 *
 * Always rendered, including when every answer is the dull one. A banner that
 * only appears in sandbox teaches the merchant to read its absence as
 * production — and absence is also what a failed request looks like, so the one
 * state they must not mistake for "live and shipping" would be drawn exactly
 * like it.
 *
 * The explanations below it are two, not one, because they are two switches and
 * can disagree: a production connection with the platform switch closed is not
 * in sandbox and must not be described as though it were.
 */
export function OperatingModeBanner({
  status,
  testID
}: {
  status: StoreSupplierStatus;
  testID?: string;
}) {
  const mode = operatingMode(status);
  const limited = mode.sandbox || !mode.realOrders;
  return (
    <View
      style={[styles.banner, limited ? styles.bannerLimited : styles.bannerLive]}
      testID={testID}
      accessible
      accessibilityRole="summary"
      accessibilityLabel={mode.line.replace(/ · /g, ". ")}
    >
      <Text style={[styles.bannerLine, limited ? styles.bannerLineLimited : null]}>{mode.line}</Text>
      {mode.sandbox ? <Text style={styles.bannerBody}>{SANDBOX_EXPLANATION}</Text> : null}
      {/* Only when production is the environment. In sandbox the switch above
          already explains why nothing ships, and saying it twice reads as two
          separate faults. */}
      {!mode.sandbox && !mode.realOrders ? (
        <Text style={styles.bannerBody}>{REAL_FULFILMENT_OFF_EXPLANATION}</Text>
      ) : null}
    </View>
  );
}

/** The health summary, one server-sent fact per line. */
export function HealthRowList({ rows, testID }: { rows: HealthRow[]; testID?: string }) {
  if (rows.length === 0) return null;
  return (
    <View style={styles.rows} testID={testID}>
      {rows.map((row) => (
        <View key={row.key} style={styles.healthRow} accessible accessibilityRole="text">
          <Text style={styles.healthLabel}>{row.label}</Text>
          <Text style={[styles.healthValue, { color: TONE_COLOR[row.tone] }]} numberOfLines={2}>
            {row.value}
          </Text>
        </View>
      ))}
    </View>
  );
}

/**
 * The attention marker.
 *
 * A dot *and* a word. Colour alone cannot carry a verdict — a merchant who
 * cannot distinguish the green from the amber would have no signal at all — and
 * the word is also what a screen reader announces, since a bare coloured circle
 * announces nothing.
 */
export function AttentionBadge({
  needsAttention,
  label,
  testID
}: {
  needsAttention: boolean;
  label?: string;
  testID?: string;
}) {
  const text = label ?? (needsAttention ? "Needs attention" : "Working");
  return (
    <View style={styles.badge} testID={testID} accessible accessibilityLabel={text}>
      <View
        style={[
          styles.dot,
          { backgroundColor: needsAttention ? storeLight.status.warning : storeLight.status.success }
        ]}
      />
      <Text style={[styles.badgeText, needsAttention ? styles.badgeTextWarning : null]}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    padding: storeLight.space.card,
    gap: 6,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth
  },
  bannerLimited: {
    backgroundColor: storeLight.bg.warning,
    borderColor: storeLight.border.warning
  },
  bannerLive: {
    backgroundColor: storeLight.bg.card,
    borderColor: storeLight.border.hairline
  },
  bannerLine: { fontSize: 13, fontWeight: "800", color: storeLight.text.primary },
  bannerLineLimited: { color: storeLight.status.warning },
  bannerBody: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 },
  rows: { gap: 4, marginTop: 6 },
  healthRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  healthLabel: { flex: 1, fontSize: 12, color: storeLight.text.muted },
  // Capped rather than given a fixed width: the value is the answer, so it gets
  // the room, and a long one wraps inside the row instead of pushing the label
  // off the card.
  healthValue: { flex: 1.4, fontSize: 12, fontWeight: "600", textAlign: "right" },
  badge: { flexDirection: "row", alignItems: "center", gap: 6 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  badgeText: { fontSize: 11, fontWeight: "700", color: storeLight.status.success },
  badgeTextWarning: { color: storeLight.status.warning }
});

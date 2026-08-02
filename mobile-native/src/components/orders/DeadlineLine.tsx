/**
 * The ship-by pressure line on a seller order: "Ship by Fri · 2 days left", going
 * amber as it approaches and burnt-orange once overdue.
 *
 * The live seller-orders payload carries no fulfillment SLA, so this deadline is
 * a declared MOCK-DATA gap. The component therefore takes an explicit optional
 * `deadline` and a `preview` flag: when no deadline is supplied it renders
 * nothing (never a fabricated one), and when `preview` is set it tags the line so
 * the pressure is visibly provisional rather than presented as a real commitment.
 */

import { StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";

export function DeadlineLine({
  deadline,
  now = Date.now(),
  preview = false
}: {
  deadline?: string;
  now?: number;
  preview?: boolean;
}) {
  if (!deadline) return null;
  const target = Date.parse(deadline);
  if (Number.isNaN(target)) return null;

  const msLeft = target - now;
  const overdue = msLeft < 0;
  const color = overdue ? ordersLight.deadline.overdue : ordersLight.deadline.text;
  const bg = ordersLight.deadline.soft;

  const label = overdue ? `Overdue by ${humanize(-msLeft)}` : `Ship by · ${humanize(msLeft)} left`;

  return (
    <View style={[styles.row, { backgroundColor: bg }]} accessibilityLabel={label}>
      <View style={[styles.dot, { backgroundColor: color }]} />
      <Text style={[styles.text, { color }]}>{label}</Text>
      {preview ? <Text style={styles.preview}>Preview</Text> : null}
    </View>
  );
}

function humanize(ms: number): string {
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${Math.max(mins, 0)} min`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} hr`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 7,
    borderRadius: ordersLight.radius.control,
    paddingHorizontal: 9,
    paddingVertical: 6,
    alignSelf: "flex-start"
  },
  dot: { width: 7, height: 7, borderRadius: 4 },
  text: { fontSize: 12, fontWeight: "800" },
  preview: {
    fontSize: 10,
    fontWeight: "800",
    color: ordersLight.text.muted,
    textTransform: "uppercase",
    letterSpacing: 0.4,
    marginLeft: 2
  }
});

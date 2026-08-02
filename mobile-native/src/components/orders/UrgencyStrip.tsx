/**
 * The attention band that sits on the navy order header. It summarises the ONE
 * thing the person most needs to know at a glance:
 *
 *   • seller → warm peach (`urgency.seller`): how many orders need action / are
 *     overdue. This is the fulfillment pressure line.
 *   • buyer  → cool mint (`urgency.buyer`): that an order is moving / arriving.
 *
 * The colour distinguishes the two perspectives at a glance, but the count and
 * the words carry the meaning; the strip reads the same to a screen reader. It
 * renders nothing when there is nothing to say, rather than an empty band.
 */

import { StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";
import { OrderPerspective } from "../../api/ordersDashboard";

export function UrgencyStrip({
  perspective,
  count,
  label
}: {
  perspective: OrderPerspective;
  count: number;
  label: string;
}) {
  if (!count || count < 1) return null;
  const accent = perspective === "seller" ? ordersLight.urgency.seller : ordersLight.urgency.buyer;
  const text = label.replace("{n}", String(count));

  return (
    <View style={styles.strip} accessibilityLabel={text}>
      <View style={[styles.pip, { backgroundColor: accent }]} />
      <Text style={[styles.text, { color: accent }]}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  strip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: ordersLight.radius.control,
    backgroundColor: "rgba(255,255,255,0.06)"
  },
  pip: {
    width: 8,
    height: 8,
    borderRadius: 4
  },
  text: {
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 0.2
  }
});

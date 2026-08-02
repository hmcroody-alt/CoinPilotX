/**
 * The single-word state of an order, coloured by the surface's semantic rule:
 *
 *   • green  → arrival        (delivered, complete, handed off)
 *   • blue   → in transit     (shipped)
 *   • violet → local pickup    (pickup scheduled)
 *   • red    → issue           (cancelled, refunded, needs review)
 *   • neutral → resting        (paid, pending)
 *
 * The colour is a reinforcement; the word is the signal. The pill carries an
 * accessibility label so a screen reader reads the state, not just "text".
 */

import { StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";

const COPY: Record<string, string> = {
  pending: "Pending",
  paid: "Paid",
  processing: "Processing",
  shipped: "Shipped",
  delivered: "Delivered",
  complete: "Complete",
  pickup_scheduled: "Pickup ready",
  handed_off: "Picked up",
  cancelled: "Cancelled",
  refunded: "Refunded",
  failed: "Needs review"
};

function colorFor(status: string): string {
  switch (status) {
    case "delivered":
    case "complete":
    case "handed_off":
      return ordersLight.status.success;
    case "shipped":
      return ordersLight.transit.base;
    case "pickup_scheduled":
      return ordersLight.pickup.status;
    case "cancelled":
    case "refunded":
    case "failed":
      return ordersLight.status.error;
    default:
      return ordersLight.status.neutral;
  }
}

export function OrdersStatusPill({ status }: { status: string }) {
  const key = String(status || "pending").toLowerCase();
  const color = colorFor(key);
  const label = COPY[key] || key;
  return (
    <View style={[styles.pill, { borderColor: color }]} accessibilityLabel={`Status: ${label}`}>
      <Text style={[styles.text, { color }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    borderRadius: ordersLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 9,
    paddingVertical: 4,
    alignSelf: "flex-start"
  },
  text: { fontSize: 11, fontWeight: "800", textTransform: "uppercase", letterSpacing: 0.3 }
});

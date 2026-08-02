/**
 * The non-content states of the Orders list — loading, empty and offline/error —
 * kept in one place so seller and buyer render them identically. The offline
 * state is honest: it says the list may be stale rather than pretending the last
 * cached read is live.
 */

import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";
import { OrderPerspective } from "../../api/ordersDashboard";

export function OrdersLoading() {
  return (
    <View style={styles.center} accessibilityLabel="Loading orders">
      <ActivityIndicator color={ordersLight.text.muted} />
      <Text style={styles.dim}>Loading orders…</Text>
    </View>
  );
}

export function OrdersEmpty({ perspective }: { perspective: OrderPerspective }) {
  const title = perspective === "seller" ? "No orders yet" : "No orders yet";
  const body =
    perspective === "seller"
      ? "Orders buyers place with you will show up here, newest first."
      : "Things you buy on PulseSoc will show up here so you can track and re-order them.";
  return (
    <View style={styles.center} accessibilityLabel={`${title}. ${body}`}>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.body}>{body}</Text>
    </View>
  );
}

export function OrdersOffline({ message }: { message?: string }) {
  return (
    <View style={styles.banner} accessibilityLabel={message || "Showing saved orders. Pull to refresh."}>
      <Text style={styles.bannerText}>
        {message || "Showing saved orders — this list may be out of date. Pull to refresh."}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: {
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 48,
    paddingHorizontal: 24
  },
  dim: {
    fontSize: 13,
    color: ordersLight.text.muted
  },
  title: {
    fontSize: 17,
    fontWeight: "800",
    color: ordersLight.text.primary
  },
  body: {
    fontSize: 13,
    lineHeight: 19,
    color: ordersLight.text.muted,
    textAlign: "center"
  },
  banner: {
    backgroundColor: ordersLight.bg.warning,
    borderColor: ordersLight.border.warning,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: ordersLight.radius.control,
    paddingHorizontal: 12,
    paddingVertical: 9,
    marginHorizontal: ordersLight.space.gutter
  },
  bannerText: {
    fontSize: 12,
    color: ordersLight.text.primary
  }
});

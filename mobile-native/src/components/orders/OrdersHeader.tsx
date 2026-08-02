/**
 * The navy header for the Orders surface. It carries the one dark band and the
 * three things true before anything below is read: how to get back, which
 * perspective you are looking at (the fulfillment queue you sell, or the orders
 * you bought), and the single urgency line for that perspective.
 *
 * The perspective toggle lives here — not in the scroll view — because it governs
 * everything beneath it and must not scroll away. Both perspectives render the
 * same order model; the toggle only swaps which end you read it from.
 */

import { type ReactNode } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { ordersLight } from "../../theme/ordersLight";
import { OrderPerspective } from "../../api/ordersDashboard";
import { UrgencyStrip } from "./UrgencyStrip";

export function OrdersHeader({
  title,
  perspective,
  onChangePerspective,
  onBack,
  urgencyCount,
  urgencyLabel,
  below
}: {
  title: string;
  perspective: OrderPerspective;
  onChangePerspective: (next: OrderPerspective) => void;
  onBack: () => void;
  urgencyCount: number;
  urgencyLabel: string;
  below?: ReactNode;
}) {
  const insets = useSafeAreaInsets();

  return (
    <LinearGradient
      colors={[ordersLight.bg.headerFrom, ordersLight.bg.headerTo]}
      style={[styles.header, { paddingTop: insets.top + 8 }]}
    >
      <View style={styles.topRow}>
        <Pressable
          onPress={onBack}
          style={styles.iconButton}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={6}
        >
          <Ionicons name="chevron-back" size={24} color={ordersLight.text.onDark} />
        </Pressable>
        <Text style={styles.title} numberOfLines={1} accessibilityRole="header">
          {title}
        </Text>
        <View style={styles.iconButton} />
      </View>

      <PerspectiveToggle perspective={perspective} onChange={onChangePerspective} />

      <UrgencyStrip perspective={perspective} count={urgencyCount} label={urgencyLabel} />

      {below}
    </LinearGradient>
  );
}

function PerspectiveToggle({
  perspective,
  onChange
}: {
  perspective: OrderPerspective;
  onChange: (next: OrderPerspective) => void;
}) {
  return (
    <View style={styles.toggle} accessibilityRole="tablist">
      {(["seller", "buyer"] as OrderPerspective[]).map((p) => {
        const active = perspective === p;
        const label = p === "seller" ? "Selling" : "Buying";
        return (
          <Pressable
            key={p}
            style={[styles.toggleCell, active ? styles.toggleCellActive : null]}
            onPress={() => onChange(p)}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            accessibilityLabel={label}
          >
            <Text style={[styles.toggleText, active ? styles.toggleTextActive : null]}>{label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: ordersLight.space.card,
    paddingBottom: 12,
    gap: 12,
    overflow: "hidden"
  },
  topRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  iconButton: {
    minWidth: ordersLight.size.tapTarget,
    minHeight: ordersLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  title: {
    flex: 1,
    fontSize: 20,
    fontWeight: "700",
    color: ordersLight.text.onDark,
    textAlign: "center"
  },
  toggle: {
    flexDirection: "row",
    backgroundColor: "rgba(255,255,255,0.10)",
    borderRadius: ordersLight.radius.control,
    padding: 3
  },
  toggleCell: {
    flex: 1,
    minHeight: 38,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: ordersLight.radius.control - 2
  },
  toggleCellActive: {
    backgroundColor: "#FFFFFF"
  },
  toggleText: {
    fontSize: 14,
    fontWeight: "800",
    color: ordersLight.text.onDarkMuted
  },
  toggleTextActive: {
    color: ordersLight.text.primary
  }
});

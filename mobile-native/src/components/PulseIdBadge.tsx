import { Ionicons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";
import { colors } from "../theme/colors";

export function PulseIdBadge({ pulseId, compact = false }: { pulseId?: string; compact?: boolean }) {
  const value = String(pulseId || "").trim().toUpperCase();
  if (!value) return null;
  return (
    <View style={[styles.badge, compact && styles.compact]} accessibilityLabel={`Pulse ID ${value}`}>
      <Ionicons name="pulse" size={compact ? 11 : 13} color={colors.accent} />
      <Text style={[styles.text, compact && styles.compactText]}>Pulse ID • {value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: { alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 6, marginTop: 7, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 14, borderWidth: 1, borderColor: "rgba(70,226,210,0.35)", backgroundColor: "rgba(20,66,72,0.34)" },
  compact: { marginTop: 4, paddingHorizontal: 7, paddingVertical: 4, borderRadius: 11 },
  text: { color: "#BDFBF2", fontSize: 12, fontWeight: "800", letterSpacing: 0.35 },
  compactText: { fontSize: 10 }
});

import { Ionicons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";
import { colors } from "../../theme/colors";

export function AuthStatusFooter({ biometricProtected }: { biometricProtected: boolean }) {
  const items = [
    ...(biometricProtected ? [{ icon: "shield-checkmark-outline" as const, label: "Face ID protected" }] : []),
    { icon: "server-outline" as const, label: "Server verified" },
    { icon: "key-outline" as const, label: "Encrypted credential storage" }
  ];

  return (
    <View style={styles.root} accessibilityRole="text" accessibilityLabel={items.map((item) => item.label).join(", ")}>
      {items.map((item, index) => (
        <View key={item.label} style={styles.item}>
          <Ionicons name={item.icon} size={13} color={colors.accent} />
          <Text style={styles.label}>{item.label}</Text>
          {index < items.length - 1 ? <Text style={styles.dot}>•</Text> : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  item: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6
  },
  label: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "700"
  },
  dot: {
    color: colors.border,
    marginLeft: 4
  }
});

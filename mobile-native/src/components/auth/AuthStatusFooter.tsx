import { Ionicons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";
import { colors } from "../../theme/colors";

export function AuthStatusFooter({ biometricProtected: _biometricProtected }: { biometricProtected: boolean }) {
  const items = ["Secure", "Private", "Protected"];

  return (
    <View style={styles.root} accessibilityRole="text" accessibilityLabel={items.join(", ")}>
      <Ionicons name="shield-checkmark-outline" size={14} color={colors.accentStrong} />
      <Text style={styles.label}>{items.join("  •  ")}</Text>
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
  label: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 0.4
  }
});

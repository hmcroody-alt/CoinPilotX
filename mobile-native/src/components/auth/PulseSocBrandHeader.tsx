import { Ionicons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";

export function PulseSocBrandHeader() {
  return (
    <View style={styles.root} accessible accessibilityRole="header" accessibilityLabel="PulseSoc, Native Access. Your network is ready.">
      <View style={styles.mark}>
        <Ionicons name="pulse" size={30} color={colors.background} />
      </View>
      <Text style={styles.wordmark} maxFontSizeMultiplier={1.6}>
        Pulse<Text style={styles.wordmarkAccent}>Soc</Text>
      </Text>
      <Text style={styles.eyebrow} maxFontSizeMultiplier={1.8}>
        Native Access
      </Text>
      <Text style={styles.tagline} maxFontSizeMultiplier={1.8}>
        Your network is ready.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    alignItems: "center",
    gap: logiNexus.spacing.sm
  },
  mark: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.circular,
    height: 60,
    justifyContent: "center",
    marginBottom: logiNexus.spacing.xs,
    shadowColor: colors.accentStrong,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.55,
    shadowRadius: 16,
    width: 60
  },
  wordmark: {
    color: colors.text,
    fontSize: 34,
    fontWeight: "900",
    letterSpacing: 0.2
  },
  wordmarkAccent: {
    color: colors.accentStrong
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 3,
    textTransform: "uppercase"
  },
  tagline: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "600"
  }
});

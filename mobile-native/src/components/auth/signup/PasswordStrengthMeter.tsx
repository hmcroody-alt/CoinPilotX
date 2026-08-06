import { Ionicons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";
import { colors } from "../../../theme/colors";
import { logiNexus } from "../../../theme/logiNexus";
import { PasswordStrength } from "../../../auth/signupValidation";
import { createThemedStyles } from "../../../theme/themedStyles";

const SEGMENTS = 4;

function strengthColor(score: number): string {
  if (score >= 4) return colors.accent;
  if (score === 3) return colors.safety;
  if (score === 2) return colors.warning;
  return colors.danger;
}

/**
 * Honest, local password feedback: a 4-segment strength bar plus the real
 * server requirements shown up front (8+ chars, a number, a symbol) as
 * check chips. Requirements are advisory except the 8-char minimum, which is
 * what actually gates submission. Reserves its own vertical space so the layout
 * never jumps as the meter appears/updates.
 */
export function PasswordStrengthMeter({ strength }: { strength: PasswordStrength }) {
  const color = strengthColor(strength.score);
  const showLabel = strength.score > 0;

  return (
    <View style={styles.root} accessibilityRole="text" accessibilityLabel={showLabel ? `Password strength: ${strength.label}` : "Enter a password"}>
      <View style={styles.barRow}>
        <View style={styles.bars}>
          {Array.from({ length: SEGMENTS }).map((_, index) => (
            <View
              key={index}
              style={[styles.bar, { backgroundColor: index < strength.score ? color : colors.border }]}
            />
          ))}
        </View>
        {showLabel ? <Text style={[styles.label, { color }]}>{strength.label} password</Text> : null}
      </View>

      <View style={styles.chips}>
        {strength.requirements.map((req) => (
          <View key={req.key} style={[styles.chip, req.met && styles.chipMet]}>
            <Ionicons
              name={req.met ? "checkmark-circle" : "ellipse-outline"}
              size={13}
              color={req.met ? colors.accent : colors.muted}
            />
            <Text style={[styles.chipText, req.met && styles.chipTextMet]} maxFontSizeMultiplier={1.3}>
              {req.label}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  root: {
    gap: logiNexus.spacing.sm,
    minHeight: 58
  },
  barRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: logiNexus.spacing.md,
    justifyContent: "space-between"
  },
  bars: {
    flexDirection: "row",
    flex: 1,
    gap: 6
  },
  bar: {
    borderRadius: 999,
    flex: 1,
    height: 4
  },
  label: {
    fontSize: 12,
    fontWeight: "900"
  },
  chips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6
  },
  chip: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexDirection: "row",
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 5
  },
  chipMet: {
    borderColor: colors.accent
  },
  chipText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  },
  chipTextMet: {
    color: colors.text
  }
}));

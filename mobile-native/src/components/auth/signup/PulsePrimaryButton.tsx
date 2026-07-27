import { Ionicons } from "@expo/vector-icons";
import { useRef } from "react";
import { AccessibilityState, ActivityIndicator, Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { colors } from "../../../theme/colors";
import { logiNexus } from "../../../theme/logiNexus";

/**
 * Primary call-to-action for the auth flow: the PulseSoc cyan→green gradient,
 * matching the login screen's palette. Provides immediate spring press
 * feedback, an inline busy state, and a genuinely disabled state that is
 * communicated by more than color (reduced opacity, a lock glyph, flattened to
 * a neutral surface, plus accessibilityState) so it is not color-alone.
 */
export function PulsePrimaryButton({
  label,
  onPress,
  disabled,
  busy,
  testID,
  accessibilityLabel,
  accessibilityHint,
  iconName
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  busy?: boolean;
  testID?: string;
  accessibilityLabel?: string;
  accessibilityHint?: string;
  iconName?: keyof typeof Ionicons.glyphMap;
}) {
  const scale = useRef(new Animated.Value(1)).current;
  const inactive = Boolean(disabled || busy);

  const spring = (toValue: number) =>
    Animated.spring(scale, { toValue, useNativeDriver: true, speed: 40, bounciness: 6 }).start();

  const accessibilityState: AccessibilityState = { disabled: Boolean(disabled), busy: Boolean(busy) };

  return (
    <Animated.View style={{ transform: [{ scale }] }}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel || label}
        accessibilityHint={accessibilityHint}
        accessibilityState={accessibilityState}
        disabled={inactive}
        testID={testID}
        onPressIn={() => !inactive && spring(0.97)}
        onPressOut={() => spring(1)}
        onPress={onPress}
      >
        {disabled ? (
          <View style={[styles.button, styles.disabled]}>
            <Ionicons name="lock-closed" size={16} color={colors.disabled} style={styles.icon} />
            <Text style={[styles.text, styles.disabledText]}>{label}</Text>
          </View>
        ) : (
          <LinearGradient
            colors={[colors.accentStrong, colors.accent]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 0 }}
            style={styles.button}
          >
            {busy ? (
              <ActivityIndicator color={colors.background} />
            ) : (
              <>
                <Text style={styles.text}>{label}</Text>
                <Ionicons name={iconName || "arrow-forward"} size={18} color={colors.background} style={styles.trailingIcon} />
              </>
            )}
          </LinearGradient>
        )}
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  button: {
    alignItems: "center",
    borderRadius: logiNexus.radius.medium,
    flexDirection: "row",
    justifyContent: "center",
    minHeight: 54,
    paddingHorizontal: logiNexus.spacing.lg
  },
  disabled: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1
  },
  text: {
    ...logiNexus.typography.button,
    color: colors.background,
    fontSize: 16
  },
  disabledText: {
    color: colors.disabled
  },
  icon: {
    marginRight: 8
  },
  trailingIcon: {
    marginLeft: 8
  }
});

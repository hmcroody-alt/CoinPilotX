import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useEffect, useRef } from "react";
import { ActivityIndicator, Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";
import { BiometricKind } from "../../session/biometricAuth";

export type BiometricButtonState = "idle" | "loading" | "success" | "failed";

const LABELS: Record<BiometricKind, string> = {
  faceId: "Continue with Face ID",
  touchId: "Continue with Touch ID",
  iris: "Continue with biometrics",
  none: "Continue with biometrics"
};

const ICONS: Record<BiometricKind, keyof typeof MaterialCommunityIcons.glyphMap> = {
  faceId: "face-recognition",
  touchId: "fingerprint",
  iris: "eye-outline",
  none: "shield-check-outline"
};

export function BiometricLoginButton({
  kind,
  state,
  onPress
}: {
  kind: BiometricKind;
  state: BiometricButtonState;
  onPress: () => void;
}) {
  const reducedMotion = useLogiNexusReducedMotion();
  const glow = useRef(new Animated.Value(0.4)).current;

  useEffect(() => {
    if (reducedMotion || state !== "idle") return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(glow, { toValue: 1, duration: 1600, useNativeDriver: true }),
        Animated.timing(glow, { toValue: 0.4, duration: 1600, useNativeDriver: true })
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [glow, reducedMotion, state]);

  const disabled = state === "loading";
  const backgroundColor = state === "success" ? colors.accent : state === "failed" ? colors.danger : colors.accent;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={LABELS[kind]}
      accessibilityState={{ disabled, busy: state === "loading" }}
      disabled={disabled}
      testID="biometric-login-button"
      onPress={onPress}
      style={({ pressed }) => [styles.button, { backgroundColor, opacity: pressed ? 0.85 : 1 }]}
    >
      <Animated.View style={[styles.iconWrap, { opacity: reducedMotion ? 1 : glow }]}>
        {state === "loading" ? (
          <ActivityIndicator color={colors.background} />
        ) : (
          <MaterialCommunityIcons name={ICONS[kind]} size={22} color={colors.background} />
        )}
      </Animated.View>
      <Text style={styles.label} maxFontSizeMultiplier={1.6}>
        {state === "loading" ? "Verifying…" : state === "success" ? "Verified" : state === "failed" ? "Try again" : LABELS[kind]}
      </Text>
    </Pressable>
  );
}

export function BiometricUnavailableHint({ message }: { message: string }) {
  return (
    <View style={styles.hint} accessibilityRole="text">
      <Text style={styles.hintText}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  button: {
    alignItems: "center",
    borderRadius: logiNexus.radius.medium,
    flexDirection: "row",
    gap: 10,
    justifyContent: "center",
    minHeight: 56,
    paddingHorizontal: logiNexus.spacing.lg
  },
  iconWrap: {
    alignItems: "center",
    justifyContent: "center"
  },
  label: {
    ...logiNexus.typography.button,
    fontSize: 15,
    color: colors.background
  },
  hint: {
    paddingHorizontal: 4
  },
  hintText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
    textAlign: "center"
  }
});

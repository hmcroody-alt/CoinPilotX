import * as Haptics from "expo-haptics";
import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { GalacticAtmosphere } from "../components/GalacticAtmosphere";
import { EngineerAccessModal } from "../components/engineer/EngineerAccessModal";
import { emitEngineerAccessDiagnostic } from "../security/engineerAccessDiagnostics";
import { useAuth } from "../session/auth";

type Props = {
  onReturn: () => void;
  /**
   * Called once the server has issued an engineer grant. The host route
   * re-resolves access and mounts the originally requested screen in place, so
   * this screen never navigates anywhere itself.
   */
  onEngineerAccessGranted?: () => void;
};

export function GalacticConstructionScreen({ onReturn, onEngineerAccessGranted }: Props) {
  const { authState } = useAuth();
  const userId = Number(authState.user?.user_id || 0);
  const [challengeOpen, setChallengeOpen] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const [unlocked, setUnlocked] = useState(false);
  const pulse = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    let active = true;
    AccessibilityInfo.isReduceMotionEnabled().then((enabled) => active && setReduceMotion(Boolean(enabled)));
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", (enabled) =>
      setReduceMotion(Boolean(enabled))
    );
    return () => { active = false; subscription?.remove?.(); };
  }, []);

  /**
   * A slow, low-amplitude glow — a sign of life on an otherwise dormant control,
   * not an attention-grab. It must stay quieter than Return, so the cycle is
   * long and the opacity delta small. Stopped entirely for Reduce Motion.
   */
  useEffect(() => {
    if (reduceMotion) { pulse.setValue(0); return; }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 2200, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 2200, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.delay(2600)
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [reduceMotion, pulse]);

  function openChallenge() {
    emitEngineerAccessDiagnostic({ stage: "button_tapped" });
    // Subtle warning haptic: this control leads somewhere restricted.
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => undefined);
    setChallengeOpen(true);
  }

  function handleGranted() {
    setChallengeOpen(false);
    // Brief unlocked state, then straight through. Access is never delayed for
    // the sake of the animation — the host re-resolves on the same tick.
    setUnlocked(true);
    onEngineerAccessGranted?.();
  }

  const glowOpacity = pulse.interpolate({ inputRange: [0, 1], outputRange: [0.16, 0.42] });

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.space} accessibilityRole="summary">
        <GalacticAtmosphere variant="business" testID="construction-galactic-atmosphere" />
        <Text style={styles.eyebrow}>PULSESOC GALACTIC CONSTRUCTION</Text>
        <Text style={styles.title}>THIS PART OF THE PULSESOC GALAXY IS STILL BEING BUILT</Text>
        <Text style={styles.body}>Our engineers are assembling the next generation of Business and Marketplace systems.</Text>
        <Text style={styles.body}>This sector will open once construction has been completed. Thank you for being part of the journey.</Text>
        <View style={styles.progressTrack} accessibilityLabel="Construction progress: infrastructure, security layer and business engine complete">
          <View style={styles.progress} />
        </View>
        <Text style={styles.progressLabel}>FOUNDATION SYSTEMS ONLINE</Text>
        <View style={styles.systems}>
          <Text style={styles.complete}>✓ Infrastructure     ✓ Security Layer</Text>
          <Text style={styles.active}>● Business Engine     • Marketplace     • Commerce</Text>
        </View>

        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Return"
          onPress={onReturn}
          testID="construction-return"
          style={({ pressed }) => [styles.button, pressed && styles.buttonPressed]}
        >
          <Text style={styles.buttonText}>Return</Text>
        </Pressable>

        <View style={styles.engineerWrap}>
          <Animated.View pointerEvents="none" style={[styles.engineerGlow, { opacity: reduceMotion ? 0.16 : glowOpacity }]} />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Engineer Access"
            accessibilityHint="Opens a secure passcode challenge for authorized engineers"
            onPress={openChallenge}
            testID="construction-engineer-access"
            style={({ pressed }) => [styles.engineerButton, pressed && styles.buttonPressed]}
          >
            <Text style={styles.engineerIcon} accessibilityElementsHidden importantForAccessibility="no">
              {unlocked ? "🔓" : "🔒"}
            </Text>
            <Text style={styles.engineerText}>Engineer Access</Text>
          </Pressable>
        </View>
        <Text style={styles.helper}>Authorized engineers only</Text>
      </View>

      <EngineerAccessModal
        visible={challengeOpen}
        userId={userId}
        onCancel={() => setChallengeOpen(false)}
        onGranted={handleGranted}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#030716" }, space: { flex: 1, alignItems: "center", justifyContent: "center", paddingHorizontal: 28, overflow: "hidden" },
  eyebrow: { color: "#57D9FF", fontSize: 11, fontWeight: "800", letterSpacing: 2.1, textAlign: "center", marginBottom: 14 },
  title: { color: "#FFFFFF", fontSize: 24, lineHeight: 30, fontWeight: "900", textAlign: "center", maxWidth: 360 },
  body: { color: "#AEBBD2", fontSize: 15, lineHeight: 22, textAlign: "center", marginTop: 12, maxWidth: 370 },
  progressTrack: { width: "100%", maxWidth: 340, height: 7, borderRadius: 7, backgroundColor: "#16213C", marginTop: 28, overflow: "hidden" }, progress: { width: "64%", height: "100%", borderRadius: 7, backgroundColor: "#42D7F5" }, progressLabel: { color: "#6FE5FF", fontSize: 10, letterSpacing: 1.7, fontWeight: "800", marginTop: 9 },
  systems: { marginTop: 18, alignItems: "center" }, complete: { color: "#65E5BB", fontSize: 12, lineHeight: 20 }, active: { color: "#9BA9C4", fontSize: 12, lineHeight: 20, textAlign: "center" },
  button: { marginTop: 30, minWidth: 220, minHeight: 50, borderRadius: 25, alignItems: "center", justifyContent: "center", backgroundColor: "#EDF8FF", shadowColor: "#4ADFFF", shadowOpacity: 0.35, shadowRadius: 16 }, buttonPressed: { opacity: 0.8, transform: [{ scale: 0.98 }] }, buttonText: { color: "#071225", fontSize: 16, fontWeight: "900" },
  // Matches the Return button's width but stays visually secondary: outlined
  // rather than filled, cooler accent, no solid background.
  engineerWrap: { marginTop: 14, minWidth: 220, alignItems: "stretch", justifyContent: "center" },
  engineerGlow: { position: "absolute", left: -5, right: -5, top: -5, bottom: -5, borderRadius: 30, backgroundColor: "#7A5CFF" },
  engineerButton: { minWidth: 220, minHeight: 48, borderRadius: 25, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8, borderWidth: 1.5, borderColor: "#8E7BFF", backgroundColor: "rgba(18,20,48,0.72)" },
  engineerIcon: { fontSize: 15 },
  engineerText: { color: "#BFC6FF", fontSize: 15, fontWeight: "800" },
  helper: { color: "#7C88A8", fontSize: 11.5, textAlign: "center", marginTop: 10 }
});

import { Ionicons } from "@expo/vector-icons";
import * as Haptics from "expo-haptics";
import { useEffect, useRef, useState } from "react";
import { Animated, Easing, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { createThemedStyles } from "../../theme/themedStyles";
import { useTheme } from "../../theme/ThemeContext";
import type { MotionMode } from "./motionStateMachine";
import { updateMotionSettings, useMotionSettings } from "./motionSettings";

/**
 * Spatial Motion onboarding (mission §§18–20).
 *
 * The one and only path into tilt: motion never activates silently. The flow
 * explains what tilt does, states the privacy contract (all sensor data is
 * processed locally, never stored or transmitted), lets the user pick a mode —
 * with Swipe Only as an always-available choice — and queues calibration for
 * the natural holding angle. Completing OR skipping marks `onboarded`; only an
 * explicit non-swipe-only choice ever turns sensors on.
 *
 * Also serves as the replayable tutorial from Settings → Accessibility →
 * Spatial Motion ("Replay tutorial").
 *
 * Every screen names Reels. Motion applies to Reels and nowhere else — Home
 * Feed motion was tested and then withdrawn — so the copy must not leave the
 * reader expecting tilt to do something on the Feed and wondering what broke.
 */

type Step = "intro" | "privacy" | "mode" | "calibrate";

const STEP_ORDER: Step[] = ["intro", "privacy", "mode", "calibrate"];

export function MotionOnboarding({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const insets = useSafeAreaInsets();
  const { reduceMotion } = useTheme();
  const settings = useMotionSettings();
  const [step, setStep] = useState<Step>("intro");
  const [chosenMode, setChosenMode] = useState<MotionMode>(settings.mode);

  // Restart from the first step each time the tutorial is opened, and start
  // the mode choice from whatever the user currently has.
  useEffect(() => {
    if (visible) {
      setStep("intro");
      setChosenMode(settings.mode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  // A small looping tilt demo — pure transform/opacity, disabled under Reduce
  // Motion (the motion layer itself is disabled there too).
  const demo = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!visible || reduceMotion) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(demo, { toValue: 1, duration: 1100, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(demo, { toValue: -1, duration: 1100, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(demo, { toValue: 0, duration: 900, easing: Easing.inOut(Easing.quad), useNativeDriver: true })
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [demo, reduceMotion, visible]);

  const stepIndex = STEP_ORDER.indexOf(step);

  const finish = (mode: MotionMode) => {
    Haptics.selectionAsync().catch(() => undefined);
    // Choosing tilt/parallax drops any stale baseline so calibration re-runs
    // against the user's current natural holding angle on first use.
    updateMotionSettings({
      mode,
      onboarded: true,
      ...(mode === "swipe-only" ? {} : { neutralBaselineRad: null })
    }).catch(() => undefined);
    onClose();
  };

  const skip = () => {
    // Skipping is a real answer: onboarded, sensors stay off.
    updateMotionSettings({ mode: "swipe-only", onboarded: true }).catch(() => undefined);
    onClose();
  };

  return (
    <Modal animationType={reduceMotion ? "none" : "slide"} transparent visible={visible} onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={[styles.sheet, { paddingBottom: Math.max(insets.bottom, 16) }]} testID="motion-onboarding">
          <View style={styles.dots}>
            {STEP_ORDER.map((name, index) => (
              <View key={name} style={[styles.dot, index === stepIndex && styles.dotActive]} />
            ))}
          </View>

          <ScrollView contentContainerStyle={styles.body} showsVerticalScrollIndicator={false}>
            {step === "intro" ? (
              <>
                <Text style={styles.title}>Move through Reels with a tilt</Text>
                <Animated.View
                  style={[
                    styles.demoCard,
                    reduceMotion
                      ? null
                      : {
                          transform: [
                            { translateX: demo.interpolate({ inputRange: [-1, 1], outputRange: [26, -26] }) },
                            { rotate: demo.interpolate({ inputRange: [-1, 1], outputRange: ["4deg", "-4deg"] }) }
                          ]
                        }
                  ]}
                >
                  <Ionicons name="phone-portrait-outline" size={34} color={colors.accent} />
                </Animated.View>
                <Text style={styles.paragraph}>
                  In Reels, a slight tilt previews the next reel with a touch of depth. Hold a firmer tilt briefly and
                  it moves, with a small haptic tick. Swiping always works exactly as before — tilt is an extra, never a
                  requirement, and it does nothing anywhere else in PulseSoc.
                </Text>
                <Text style={styles.paragraph}>
                  Touch always wins: the moment your finger is on the screen, tilt stands down. It also pauses while you
                  type, when menus or the create console are open, and when the phone is lying flat.
                </Text>
              </>
            ) : null}

            {step === "privacy" ? (
              <>
                <Text style={styles.title}>Private by design</Text>
                <View style={styles.demoCard}>
                  <Ionicons name="lock-closed-outline" size={34} color={colors.accent} />
                </View>
                <Text style={styles.paragraph}>
                  Motion data is processed entirely on this device, in memory, and only while Reels is open. The
                  sensor is not running anywhere else. Raw readings are never stored and never leave your phone.
                </Text>
                <Text style={styles.paragraph}>
                  Only your preferences are saved: the mode you pick, sensitivity, and your calibrated holding angle.
                </Text>
              </>
            ) : null}

            {step === "mode" ? (
              <>
                <Text style={styles.title}>Choose how you navigate</Text>
                <ModeOption
                  title="Swipe only"
                  subtitle="Motion sensors stay off. Everything works by touch."
                  icon="hand-left-outline"
                  selected={chosenMode === "swipe-only"}
                  testID="motion-onboarding-mode-swipe-only"
                  onPress={() => setChosenMode("swipe-only")}
                />
                <ModeOption
                  title="Swipe + Parallax"
                  subtitle="Tilt adds a subtle depth preview. Reels never change from tilt."
                  icon="layers-outline"
                  selected={chosenMode === "parallax"}
                  testID="motion-onboarding-mode-parallax"
                  onPress={() => setChosenMode("parallax")}
                />
                <ModeOption
                  title="Swipe + Tilt"
                  subtitle="A sustained tilt moves to the next reel, with a haptic tick. Swipe still always works."
                  icon="sync-outline"
                  selected={chosenMode === "tilt"}
                  testID="motion-onboarding-mode-tilt"
                  onPress={() => setChosenMode("tilt")}
                />
              </>
            ) : null}

            {step === "calibrate" ? (
              <>
                <Text style={styles.title}>{chosenMode === "swipe-only" ? "All set" : "Calibration"}</Text>
                <View style={styles.demoCard}>
                  <Ionicons
                    name={chosenMode === "swipe-only" ? "checkmark-circle-outline" : "compass-outline"}
                    size={34}
                    color={colors.accent}
                  />
                </View>
                {chosenMode === "swipe-only" ? (
                  <Text style={styles.paragraph}>
                    Sensors stay off. You can turn tilt or parallax on any time in Settings → Accessibility → Spatial
                    Motion.
                  </Text>
                ) : (
                  <>
                    <Text style={styles.paragraph}>
                      Hold your phone the way you naturally do. The first moments in Reels capture that angle as your
                      neutral position, so "no tilt" means your comfortable grip — not perfectly upright.
                    </Text>
                    <Text style={styles.paragraph}>
                      You can recalibrate or change sensitivity any time in Settings → Accessibility → Spatial Motion.
                    </Text>
                  </>
                )}
              </>
            ) : null}
          </ScrollView>

          <View style={styles.footer}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Skip Spatial Motion setup and keep swipe only"
              style={({ pressed }) => [styles.secondaryButton, pressed && styles.pressed]}
              testID="motion-onboarding-skip"
              onPress={skip}
            >
              <Text style={styles.secondaryButtonText}>Keep swipe only</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={step === "calibrate" ? "Finish Spatial Motion setup" : "Next"}
              style={({ pressed }) => [styles.primaryButton, pressed && styles.pressed]}
              testID="motion-onboarding-next"
              onPress={() => {
                Haptics.selectionAsync().catch(() => undefined);
                if (step === "calibrate") {
                  finish(chosenMode);
                  return;
                }
                setStep(STEP_ORDER[stepIndex + 1]);
              }}
            >
              <Text style={styles.primaryButtonText}>{step === "calibrate" ? "Done" : "Next"}</Text>
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

function ModeOption({
  title,
  subtitle,
  icon,
  selected,
  testID,
  onPress
}: {
  title: string;
  subtitle: string;
  icon: keyof typeof Ionicons.glyphMap;
  selected: boolean;
  testID: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${title}. ${subtitle}`}
      accessibilityState={{ selected }}
      style={({ pressed }) => [styles.modeOption, selected && styles.modeOptionSelected, pressed && styles.pressed]}
      testID={testID}
      onPress={() => {
        Haptics.selectionAsync().catch(() => undefined);
        onPress();
      }}
    >
      <View style={styles.modeIconShell}>
        <Ionicons name={icon} size={22} color={selected ? colors.accent : colors.text} />
      </View>
      <View style={styles.modeText}>
        <Text style={[styles.modeTitle, selected && styles.modeTitleSelected]}>{title}</Text>
        <Text style={styles.modeSubtitle}>{subtitle}</Text>
      </View>
      {selected ? <Ionicons name="checkmark" size={20} color={colors.accent} /> : null}
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  backdrop: {
    backgroundColor: "rgba(3, 9, 18, 0.96)",
    flex: 1,
    justifyContent: "flex-end"
  },
  body: {
    gap: 14,
    paddingHorizontal: logiNexus.spacing.md,
    paddingVertical: 18
  },
  demoCard: {
    alignItems: "center",
    alignSelf: "center",
    backgroundColor: "rgba(18, 26, 61, 0.44)",
    borderColor: "rgba(100, 160, 255, 0.6)",
    borderRadius: 24,
    borderWidth: 1,
    height: 84,
    justifyContent: "center",
    width: 84
  },
  dot: {
    backgroundColor: "rgba(255,255,255,0.18)",
    borderRadius: 3,
    height: 6,
    width: 6
  },
  dotActive: {
    backgroundColor: colors.accent,
    width: 18
  },
  dots: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    paddingTop: 14
  },
  footer: {
    flexDirection: "row",
    gap: 10,
    paddingHorizontal: logiNexus.spacing.md,
    paddingTop: 6
  },
  modeIconShell: {
    alignItems: "center",
    backgroundColor: "rgba(18, 26, 61, 0.44)",
    borderColor: "rgba(100, 160, 255, 0.6)",
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 42,
    justifyContent: "center",
    width: 42
  },
  modeOption: {
    alignItems: "center",
    backgroundColor: "rgba(7, 14, 32, 0.95)",
    borderColor: "rgba(77, 150, 255, 0.25)",
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    minHeight: 64,
    padding: 12
  },
  modeOptionSelected: {
    borderColor: colors.accent
  },
  modeSubtitle: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    fontSize: 12,
    lineHeight: 16
  },
  modeText: {
    flex: 1,
    gap: 2
  },
  modeTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800"
  },
  modeTitleSelected: {
    color: colors.accent
  },
  paragraph: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  pressed: {
    opacity: 0.72,
    transform: [{ scale: 0.98 }]
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 19,
    flex: 1,
    justifyContent: "center",
    minHeight: 48
  },
  primaryButtonText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "800"
  },
  secondaryButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: "rgba(255,255,255,0.18)",
    borderRadius: 19,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 48,
    paddingHorizontal: 12
  },
  secondaryButtonText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "700"
  },
  sheet: {
    backgroundColor: "rgba(7, 14, 32, 0.95)",
    borderColor: "rgba(77, 150, 255, 0.25)",
    borderTopLeftRadius: 28,
    borderTopRightRadius: 28,
    borderWidth: 1,
    maxHeight: "88%"
  },
  title: {
    ...logiNexus.typography.sectionTitle,
    color: colors.text,
    fontSize: 21,
    textAlign: "center"
  }
}))

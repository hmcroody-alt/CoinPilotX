import { useCallback, useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, AppState, Easing, StyleSheet, Text, View } from "react-native";
import { useIsFocused } from "@react-navigation/native";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";

/**
 * The PulseSoc header wordmark, at rest, and its rare "welcome" signature
 * moment. Isolated from Home so the ~10s scheduling loop and its Animated
 * values never cause Home (or any of its siblings in the header row) to
 * rerender — this component owns its own timer, focus/app-state awareness,
 * and animation lifecycle, and renders nothing that changes the header's
 * height or the horizontal space it occupies.
 *
 * Cadence is one-shot and additive, never a countdown: ~10s of quiet, then a
 * single ~1.9s animated burst, then quiet again. The stage envelope
 * constants below (ENTRANCE/UNDERLINE/HOLD/EXIT) exist so the schedule that
 * unmounts the particle layer and the total-cycle math stay derived from the
 * same numbers the Animated sequence actually uses, instead of duplicated
 * magic numbers drifting apart over time.
 */

const WELCOME_INTERVAL_MS = 10000;
const LETTERS = "PulseSoc".split("");
const ACCENT_START_INDEX = 5; // "Pulse" | "Soc"
const PARTICLE_COUNT = 3;
const PARTICLE_OFFSETS: Array<{ left: number; top: number }> = [
  { left: -30, top: -2 },
  { left: 0, top: 12 },
  { left: 30, top: -2 }
];

// Envelope durations (ms) for the full-motion sequence. Each is an upper
// bound on how long its stage's Animated.parallel actually takes to settle;
// the individual timings below are tuned to land at or under these.
const ENTRANCE_MS = 500;
const UNDERLINE_MS = 480;
const HOLD_MS = 500;
const EXIT_MS = 460;
const REDUCED_MOTION_IN_MS = 300;
const REDUCED_MOTION_HOLD_MS = 1200;
const REDUCED_MOTION_OUT_MS = 300;

type Phase = "idle" | "welcome";

export function LivingPulseSocWordmark() {
  const isFocused = useIsFocused();
  const [appActive, setAppActive] = useState(AppState.currentState === "active");
  const [reducedMotion, setReducedMotion] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [particlesVisible, setParticlesVisible] = useState(false);

  const staticOpacity = useRef(new Animated.Value(1)).current;
  const lettersOpacity = useRef(new Animated.Value(0)).current;
  const welcomeOpacity = useRef(new Animated.Value(0)).current;
  const welcomeTranslate = useRef(new Animated.Value(6)).current;
  const scale = useRef(new Animated.Value(1)).current;
  const letterOffsets = useRef(LETTERS.map(() => new Animated.Value(0))).current;
  const underlinePulse = useRef(new Animated.Value(0)).current;
  const particleValues = useRef(Array.from({ length: PARTICLE_COUNT }, () => new Animated.Value(0))).current;

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const particleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sequenceRef = useRef<Animated.CompositeAnimation | null>(null);
  const activeRef = useRef(false);
  const playRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((enabled) => mounted && setReducedMotion(Boolean(enabled)))
      .catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", (enabled) => setReducedMotion(Boolean(enabled)));
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (next) => setAppActive(next === "active"));
    return () => subscription.remove();
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (particleTimerRef.current) {
      clearTimeout(particleTimerRef.current);
      particleTimerRef.current = null;
    }
  }, []);

  const resetVisuals = useCallback(() => {
    staticOpacity.setValue(1);
    lettersOpacity.setValue(0);
    welcomeOpacity.setValue(0);
    welcomeTranslate.setValue(6);
    scale.setValue(1);
    letterOffsets.forEach((value) => value.setValue(0));
    underlinePulse.setValue(0);
    particleValues.forEach((value) => value.setValue(0));
  }, [letterOffsets, particleValues, lettersOpacity, scale, staticOpacity, underlinePulse, welcomeOpacity, welcomeTranslate]);

  const scheduleNext = useCallback(() => {
    clearTimer();
    timerRef.current = setTimeout(() => {
      if (activeRef.current) playRef.current();
    }, WELCOME_INTERVAL_MS);
  }, [clearTimer]);

  const stopSequence = useCallback(() => {
    sequenceRef.current?.stop();
    sequenceRef.current = null;
    setParticlesVisible(false);
    setPhase("idle");
    resetVisuals();
  }, [resetVisuals]);

  const play = useCallback(() => {
    setPhase("welcome");
    setParticlesVisible(true);

    if (reducedMotion) {
      const sequence = Animated.sequence([
        Animated.timing(welcomeOpacity, {
          toValue: 1,
          duration: REDUCED_MOTION_IN_MS,
          easing: Easing.out(Easing.quad),
          useNativeDriver: true
        }),
        Animated.delay(REDUCED_MOTION_HOLD_MS),
        Animated.timing(welcomeOpacity, {
          toValue: 0,
          duration: REDUCED_MOTION_OUT_MS,
          easing: Easing.in(Easing.quad),
          useNativeDriver: true
        })
      ]);
      sequenceRef.current = sequence;
      setParticlesVisible(false);
      sequence.start(({ finished }) => {
        setPhase("idle");
        resetVisuals();
        if (finished) scheduleNext();
      });
      return;
    }

    const entrance = Animated.parallel([
      Animated.timing(welcomeOpacity, { toValue: 1, duration: 250, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      Animated.timing(welcomeTranslate, { toValue: 0, duration: 250, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
      Animated.timing(staticOpacity, { toValue: 0, duration: 180, easing: Easing.out(Easing.quad), useNativeDriver: true }),
      Animated.timing(lettersOpacity, { toValue: 1, duration: 180, easing: Easing.out(Easing.quad), useNativeDriver: true }),
      Animated.sequence([
        Animated.spring(scale, { toValue: 1.05, speed: 22, bounciness: 8, useNativeDriver: true }),
        Animated.spring(scale, { toValue: 1, speed: 18, bounciness: 5, useNativeDriver: true })
      ]),
      Animated.stagger(
        25,
        letterOffsets.map((value) =>
          Animated.sequence([
            Animated.timing(value, { toValue: -3, duration: 130, easing: Easing.out(Easing.quad), useNativeDriver: true }),
            Animated.timing(value, { toValue: 0, duration: 170, easing: Easing.out(Easing.quad), useNativeDriver: true })
          ])
        )
      )
    ]);

    const underlineAndParticles = Animated.parallel([
      Animated.timing(underlinePulse, { toValue: 1, duration: UNDERLINE_MS, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      Animated.stagger(
        40,
        particleValues.map((value) =>
          Animated.sequence([
            Animated.timing(value, { toValue: 1, duration: 180, easing: Easing.out(Easing.quad), useNativeDriver: true }),
            Animated.timing(value, { toValue: 0, duration: 220, easing: Easing.in(Easing.quad), useNativeDriver: true })
          ])
        )
      )
    ]);

    const exit = Animated.parallel([
      Animated.timing(welcomeOpacity, { toValue: 0, duration: EXIT_MS, easing: Easing.in(Easing.cubic), useNativeDriver: true }),
      Animated.timing(welcomeTranslate, { toValue: 6, duration: EXIT_MS, easing: Easing.in(Easing.cubic), useNativeDriver: true }),
      Animated.timing(staticOpacity, { toValue: 1, duration: EXIT_MS, easing: Easing.in(Easing.quad), useNativeDriver: true }),
      Animated.timing(lettersOpacity, { toValue: 0, duration: EXIT_MS, easing: Easing.in(Easing.quad), useNativeDriver: true })
    ]);

    const sequence = Animated.sequence([entrance, underlineAndParticles, Animated.delay(HOLD_MS), exit]);
    sequenceRef.current = sequence;

    particleTimerRef.current = setTimeout(() => setParticlesVisible(false), ENTRANCE_MS + UNDERLINE_MS);

    sequence.start(({ finished }) => {
      setParticlesVisible(false);
      if (finished) {
        setPhase("idle");
        resetVisuals();
        scheduleNext();
      }
    });
  }, [
    letterOffsets,
    lettersOpacity,
    particleValues,
    reducedMotion,
    resetVisuals,
    scale,
    scheduleNext,
    staticOpacity,
    underlinePulse,
    welcomeOpacity,
    welcomeTranslate
  ]);

  useEffect(() => {
    playRef.current = play;
  }, [play]);

  const active = isFocused && appActive;

  useEffect(() => {
    activeRef.current = active;
    if (!active) {
      clearTimer();
      if (phase === "welcome") stopSequence();
      return;
    }
    if (!timerRef.current && phase === "idle") {
      scheduleNext();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  useEffect(
    () => () => {
      clearTimer();
      sequenceRef.current?.stop();
    },
    [clearTimer]
  );

  const underlineTranslateX = underlinePulse.interpolate({ inputRange: [0, 1], outputRange: [-58, 58] });
  const underlineGlowOpacity = underlinePulse.interpolate({ inputRange: [0, 0.15, 0.85, 1], outputRange: [0, 1, 1, 0] });

  return (
    <View style={styles.wrap} testID="pulsesoc-wordmark" accessibilityValue={{ text: phase }}>
      <Animated.Text
        style={[styles.welcomeLabel, { opacity: welcomeOpacity, transform: [{ translateY: welcomeTranslate }] }]}
        numberOfLines={1}
        pointerEvents="none"
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
      >
        Welcome to
      </Animated.Text>

      <View style={styles.brandRow}>
        <Animated.Text style={[styles.headerTitleHome, { opacity: staticOpacity }]} numberOfLines={1}>
          Pulse<Text style={styles.headerTitleHomeAccent}>Soc</Text>
        </Animated.Text>
        <Animated.View
          pointerEvents="none"
          style={[StyleSheet.absoluteFillObject, styles.lettersRow, { opacity: lettersOpacity, transform: [{ scale }] }]}
        >
          {LETTERS.map((letter, index) => (
            <Animated.Text
              key={`${letter}-${index}`}
              style={[
                styles.headerTitleHome,
                styles.letter,
                index >= ACCENT_START_INDEX ? styles.headerTitleHomeAccent : null,
                { transform: [{ translateY: letterOffsets[index] }] }
              ]}
            >
              {letter}
            </Animated.Text>
          ))}
        </Animated.View>
      </View>

      <View pointerEvents="none" style={styles.homeBrandSignal}>
        <View style={styles.homeBrandSignalPrimary} />
        <Text style={styles.homeBrandPulse}>⌁</Text>
        <View style={styles.homeBrandSignalSecondary} />
        <Animated.View
          style={[
            styles.underlineGlow,
            { opacity: underlineGlowOpacity, transform: [{ translateX: underlineTranslateX }] }
          ]}
        />
      </View>

      {particlesVisible ? (
        <View pointerEvents="none" testID="pulsesoc-wordmark-particles" style={styles.particleField}>
          {particleValues.map((value, index) => {
            const offset = PARTICLE_OFFSETS[index % PARTICLE_OFFSETS.length];
            const drift = value.interpolate({ inputRange: [0, 1], outputRange: [0, -8] });
            return (
              <Animated.View
                key={index}
                style={[
                  styles.particle,
                  { left: offset.left, top: offset.top, opacity: value, transform: [{ translateY: drift }] }
                ]}
              />
            );
          })}
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: "relative"
  },
  brandRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "center"
  },
  lettersRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "center"
  },
  letter: {
    flexShrink: 0
  },
  headerTitleHome: {
    ...logiNexus.typography.home.brand,
    color: colors.text,
    fontSize: 25,
    lineHeight: 31,
    textAlign: "center"
  },
  headerTitleHomeAccent: {
    color: colors.intelligence
  },
  welcomeLabel: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 2,
    lineHeight: 14,
    position: "absolute",
    textAlign: "center",
    top: -15,
    width: "100%"
  },
  homeBrandSignal: {
    alignItems: "center",
    flexDirection: "row",
    height: 10,
    justifyContent: "center",
    marginTop: 3,
    width: 120
  },
  homeBrandSignalPrimary: {
    backgroundColor: colors.accent,
    height: 1,
    width: 58
  },
  homeBrandSignalSecondary: {
    backgroundColor: colors.intelligence,
    height: 1,
    width: 58
  },
  homeBrandPulse: {
    color: colors.accent,
    fontSize: 21,
    fontWeight: "900",
    lineHeight: 16,
    marginHorizontal: -2,
    marginTop: -4
  },
  underlineGlow: {
    backgroundColor: colors.text,
    borderRadius: 4,
    height: 3,
    position: "absolute",
    width: 16
  },
  particleField: {
    alignItems: "center",
    height: 0,
    justifyContent: "center",
    position: "absolute",
    top: 8,
    width: "100%"
  },
  particle: {
    backgroundColor: colors.accent,
    borderRadius: 2,
    height: 4,
    position: "absolute",
    width: 4
  }
});

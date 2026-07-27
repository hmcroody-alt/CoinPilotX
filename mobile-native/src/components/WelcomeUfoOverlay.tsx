import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AccessibilityInfo,
  Animated,
  Easing,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import * as Haptics from "expo-haptics";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { dismissWelcome, fetchWelcomeState, WelcomeState } from "../api/welcome";
import { logiNexus } from "../theme/logiNexus";
import { colors } from "../theme/colors";

type ActiveWelcome = Extract<WelcomeState, { shouldShow: true }>;

type WelcomeUfoOverlayProps = {
  // Gate that turns true once the user is authenticated and Home is the active
  // surface. The overlay only fetches state while this is true so the impression
  // is not spent behind a splash screen or on a non-Home tab.
  active: boolean;
};

function resolveReducedMotion(welcome: ActiveWelcome | null, systemReducedMotion: boolean): boolean {
  const preference = welcome?.settings.reducedMotion;
  if (preference === "true") return true;
  if (preference === "false") return false;
  return systemReducedMotion;
}

export function WelcomeUfoOverlay({ active }: WelcomeUfoOverlayProps) {
  const insets = useSafeAreaInsets();
  const [welcome, setWelcome] = useState<ActiveWelcome | null>(null);
  const [systemReducedMotion, setSystemReducedMotion] = useState(false);
  const requestedRef = useRef(false);

  const backdrop = useRef(new Animated.Value(0)).current;
  const card = useRef(new Animated.Value(0)).current;
  const float = useRef(new Animated.Value(0)).current;
  const floatLoop = useRef<Animated.CompositeAnimation | null>(null);

  const reducedMotion = resolveReducedMotion(welcome, systemReducedMotion);

  useEffect(() => {
    let mounted = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((enabled) => mounted && setSystemReducedMotion(Boolean(enabled)))
      .catch(() => undefined);
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", (enabled) =>
      setSystemReducedMotion(Boolean(enabled))
    );
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);

  // Fetch once per authenticated Home session. The GET records the impression
  // server-side, so we guard with requestedRef to avoid double-claiming.
  useEffect(() => {
    if (!active || requestedRef.current) return;
    requestedRef.current = true;
    let mounted = true;
    fetchWelcomeState()
      .then((state) => {
        if (!mounted || !state.shouldShow) return;
        setWelcome(state);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, [active]);

  const visible = Boolean(welcome);

  useEffect(() => {
    if (!welcome) return;
    if (welcome.settings.welcomeHaptics) {
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
    }
    AccessibilityInfo.announceForAccessibility?.(`${welcome.title}. ${welcome.body}`);

    if (reducedMotion) {
      backdrop.setValue(1);
      card.setValue(1);
      float.setValue(0.5);
      return;
    }

    backdrop.setValue(0);
    card.setValue(0);
    Animated.parallel([
      Animated.timing(backdrop, { toValue: 1, duration: 220, useNativeDriver: true }),
      Animated.spring(card, { toValue: 1, friction: 7, tension: 70, useNativeDriver: true })
    ]).start();

    floatLoop.current = Animated.loop(
      Animated.sequence([
        Animated.timing(float, {
          toValue: 1,
          duration: 2400,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true
        }),
        Animated.timing(float, {
          toValue: 0,
          duration: 2400,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true
        })
      ])
    );
    floatLoop.current.start();

    return () => {
      floatLoop.current?.stop();
      floatLoop.current = null;
    };
  }, [welcome, reducedMotion, backdrop, card, float]);

  const close = useCallback(() => {
    const current = welcome;
    if (!current) return;
    if (current.settings.welcomeHaptics) {
      Haptics.selectionAsync().catch(() => undefined);
    }
    dismissWelcome(current.welcomeType, current.eventId).catch(() => undefined);
    floatLoop.current?.stop();

    if (reducedMotion) {
      setWelcome(null);
      return;
    }
    Animated.parallel([
      Animated.timing(backdrop, { toValue: 0, duration: 180, useNativeDriver: true }),
      Animated.timing(card, { toValue: 0, duration: 180, useNativeDriver: true })
    ]).start(() => setWelcome(null));
  }, [welcome, reducedMotion, backdrop, card]);

  const floatTranslate = float.interpolate({ inputRange: [0, 1], outputRange: [8, -8] });
  const cardTranslate = card.interpolate({ inputRange: [0, 1], outputRange: [40, 0] });
  const cardScale = card.interpolate({ inputRange: [0, 1], outputRange: [0.92, 1] });

  const gradientColors = useMemo(
    () => ["#0a1830", "#101f3a", "#1a1440"] as const,
    []
  );

  if (!visible || !welcome) return null;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="none"
      onRequestClose={close}
      statusBarTranslucent
    >
      <Animated.View style={[styles.backdrop, { opacity: backdrop }]}>
        <Pressable
          style={StyleSheet.absoluteFill}
          accessibilityRole="button"
          accessibilityLabel="Dismiss welcome"
          onPress={close}
        />
        <Animated.View
          style={[
            styles.card,
            {
              marginBottom: insets.bottom,
              opacity: card,
              transform: [{ translateY: cardTranslate }, { scale: cardScale }]
            }
          ]}
          accessibilityViewIsModal
          accessibilityLiveRegion="polite"
        >
          <LinearGradient colors={gradientColors} style={StyleSheet.absoluteFill} />
          <Animated.View style={[styles.craftWrap, { transform: [{ translateY: floatTranslate }] }]}>
            <View style={styles.craftGlow} />
            <Text style={styles.craft} accessibilityElementsHidden importantForAccessibility="no">
              🛸
            </Text>
          </Animated.View>
          <Text style={styles.title} accessibilityRole="header">
            {welcome.title}
          </Text>
          <Text style={styles.body}>{welcome.body}</Text>
          {welcome.subtext ? <Text style={styles.subtext}>{welcome.subtext}</Text> : null}
          <Pressable
            style={({ pressed }) => [styles.cta, pressed && styles.ctaPressed]}
            accessibilityRole="button"
            accessibilityLabel={welcome.cta}
            onPress={close}
          >
            <Text style={styles.ctaLabel}>{welcome.cta}</Text>
          </Pressable>
        </Animated.View>
      </Animated.View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(3, 7, 18, 0.82)",
    justifyContent: "flex-end",
    alignItems: "center",
    paddingHorizontal: logiNexus.spacing.xl
  },
  card: {
    width: "100%",
    maxWidth: 460,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    borderColor: colors.accent,
    paddingHorizontal: logiNexus.spacing.xxl,
    paddingTop: logiNexus.spacing.giant,
    paddingBottom: logiNexus.spacing.xxl,
    overflow: "hidden",
    alignItems: "center"
  },
  craftWrap: {
    alignItems: "center",
    justifyContent: "center",
    marginBottom: logiNexus.spacing.lg
  },
  craftGlow: {
    position: "absolute",
    width: 96,
    height: 96,
    borderRadius: 48,
    backgroundColor: "rgba(50, 230, 179, 0.22)"
  },
  craft: {
    fontSize: 60,
    lineHeight: 68,
    textAlign: "center"
  },
  title: {
    ...logiNexus.typography.title,
    color: colors.text,
    textAlign: "center",
    marginBottom: logiNexus.spacing.sm
  },
  body: {
    ...logiNexus.typography.body,
    color: colors.text,
    textAlign: "center",
    marginBottom: logiNexus.spacing.xs
  },
  subtext: {
    ...logiNexus.typography.body,
    color: colors.muted,
    textAlign: "center",
    marginBottom: logiNexus.spacing.xl
  },
  cta: {
    alignSelf: "stretch",
    marginTop: logiNexus.spacing.md,
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.capsule,
    paddingVertical: logiNexus.spacing.lg,
    alignItems: "center"
  },
  ctaPressed: {
    opacity: 0.85
  },
  ctaLabel: {
    ...logiNexus.typography.button,
    color: colors.background
  }
});

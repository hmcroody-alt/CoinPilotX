import { useEffect, useRef, useState } from "react";
import { Animated, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { createLogiNexusAmbientPulse, useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";
import { createThemedStyles } from "../../theme/themedStyles";

/**
 * A locked hub card for a module that is not launched yet.
 *
 * The module stays VISIBLE — same grid slot, same icon, same label as a live
 * tile — but reads as "preparing", not broken: a gentle floating drift with a
 * breathing accent glow, staggered per card so neighbours never move in
 * lock-step, and a press-scale response so the card clearly received the tap.
 * The tap opens the Coming Soon message; it never navigates, so there is no
 * dead button and no back-door into an unfinished surface.
 *
 * Reduce Motion removes every decorative movement (no drift, no pulse, no
 * press spring) while keeping the premium locked appearance — static glow
 * border and badge — so the card still reads as intentionally locked.
 *
 * All copy comes from the i18n catalogs; readiness itself is decided by
 * `core/launchReadiness.ts`, never here.
 */

const DRIFT_TRAVEL = -4;
const DRIFT_DURATION_MS = 2600;
const STAGGER_MS = 340;

type Props = {
  icon: string;
  label: string;
  blurb: string;
  /** Position among the locked cards — drives the stagger offset. */
  index: number;
  testID?: string;
};

export function ComingSoonCard({ icon, label, blurb, index, testID }: Props) {
  const { t } = useTranslation();
  const reducedMotion = useLogiNexusReducedMotion();
  const [open, setOpen] = useState(false);
  const drift = useRef(new Animated.Value(0)).current;
  const pressScale = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (reducedMotion) {
      drift.setValue(0);
      return;
    }
    let pulse: Animated.CompositeAnimation | null = null;
    // Staggered start: each card joins the drift on its own beat so the grid
    // shimmers instead of bouncing as one block.
    const timer = setTimeout(() => {
      pulse = createLogiNexusAmbientPulse(drift, { duration: DRIFT_DURATION_MS });
      pulse.start();
    }, (index % 6) * STAGGER_MS);
    return () => {
      clearTimeout(timer);
      pulse?.stop();
      drift.setValue(0);
    };
  }, [reducedMotion, drift, index]);

  const translateY = drift.interpolate({ inputRange: [0, 1], outputRange: [0, DRIFT_TRAVEL] });
  const glowOpacity = reducedMotion
    ? 0.55
    : drift.interpolate({ inputRange: [0, 1], outputRange: [0.3, 0.85] });

  function pressTo(value: number) {
    if (reducedMotion) return;
    Animated.spring(pressScale, { toValue: value, useNativeDriver: true, speed: 30, bounciness: 6 }).start();
  }

  return (
    <>
      <Animated.View
        style={[
          styles.card,
          { transform: [{ translateY }, { scale: pressScale }] }
        ]}
      >
        <Animated.View pointerEvents="none" style={[styles.glow, { opacity: glowOpacity }]} />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${label}. ${blurb}`}
          accessibilityHint={t("commerce:launch.comingSoon.hint")}
          testID={testID}
          onPressIn={() => pressTo(0.96)}
          onPressOut={() => pressTo(1)}
          onPress={() => setOpen(true)}
          style={styles.cardBody}
        >
          <View style={styles.cardHeader}>
            <Ionicons name={icon as never} size={20} color={colors.accent} />
            <View style={styles.badge}>
              <Text style={styles.badgeText}>{t("commerce:launch.comingSoon.title")}</Text>
            </View>
          </View>
          <Text style={styles.label}>{label}</Text>
          <Text style={styles.blurb} numberOfLines={2}>
            {blurb}
          </Text>
        </Pressable>
      </Animated.View>

      <Modal animationType="fade" onRequestClose={() => setOpen(false)} transparent visible={open}>
        <View style={styles.backdrop}>
          <View style={styles.sheet}>
            <Text style={styles.sheetBadge}>{t("commerce:launch.comingSoon.title")}</Text>
            <Text style={styles.sheetTitle}>{label}</Text>
            <Text style={styles.sheetBody}>{t("commerce:launch.comingSoon.body")}</Text>
            <Pressable
              accessibilityRole="button"
              testID={testID ? `${testID}-got-it` : undefined}
              onPress={() => setOpen(false)}
              style={styles.gotIt}
            >
              <Text style={styles.gotItText}>{t("commerce:launch.comingSoon.gotIt")}</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = createThemedStyles(() => ({
  backdrop: {
    alignItems: "center",
    backgroundColor: "rgba(0, 0, 0, 0.6)",
    flex: 1,
    justifyContent: "center",
    padding: 28
  },
  badge: {
    backgroundColor: colors.surface,
    borderColor: colors.accent,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 8,
    paddingVertical: 3
  },
  badgeText: {
    color: colors.accent,
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 1,
    textTransform: "uppercase"
  },
  blurb: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  card: {
    flexBasis: "47%",
    flexGrow: 1
  },
  cardBody: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 6,
    padding: 12
  },
  cardHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  glow: {
    borderColor: colors.accent,
    borderRadius: 12,
    borderWidth: 1.5,
    bottom: -2,
    left: -2,
    position: "absolute",
    right: -2,
    top: -2
  },
  gotIt: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 10,
    paddingVertical: 12
  },
  gotItText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "700"
  },
  label: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "700"
  },
  sheet: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.accent,
    borderRadius: 16,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 12,
    maxWidth: 420,
    padding: 24,
    width: "100%"
  },
  sheetBadge: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 2,
    textTransform: "uppercase"
  },
  sheetBody: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 22
  },
  sheetTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "800"
  }
}));

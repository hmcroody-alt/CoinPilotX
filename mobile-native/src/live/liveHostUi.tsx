import { ReactNode, useEffect, useRef } from "react";
import {
  Animated,
  Easing,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import * as Haptics from "expo-haptics";
import { colors } from "../theme/colors";

/**
 * Shared glass primitives for the immersive Live Host studio.
 *
 * Everything here is Views + expo-linear-gradient + Ionicons only (no new native
 * deps) so it drops into the existing binary. Controls read as translucent
 * "broadcast glass" that float over the live camera rather than a settings page.
 */

export type GlassTone = "default" | "accent" | "danger" | "intelligence" | "creator";

function toneColor(tone: GlassTone): string {
  if (tone === "accent") return colors.accent;
  if (tone === "danger") return colors.danger;
  if (tone === "intelligence") return colors.intelligence;
  if (tone === "creator") return colors.creator;
  return colors.text;
}

/**
 * Circular glass control. Used in both the bottom control tray and the right
 * action rail. `active` lights the ring in the tone color; `solid` fills it
 * (used for the End button). A small badge renders unread/queued counts.
 */
export function GlassCircleButton({
  icon,
  label,
  onPress,
  active = false,
  solid = false,
  tone = "default",
  disabled = false,
  size = 52,
  badge,
  haptics = true
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label?: string;
  onPress?: () => void;
  active?: boolean;
  solid?: boolean;
  tone?: GlassTone;
  disabled?: boolean;
  size?: number;
  badge?: number;
  haptics?: boolean;
}) {
  const ring = toneColor(tone);
  const press = () => {
    if (disabled) return;
    if (haptics) Haptics.selectionAsync().catch(() => undefined);
    onPress?.();
  };
  return (
    <View style={styles.circleWrap}>
      <Pressable
        onPress={press}
        disabled={disabled}
        accessibilityRole="button"
        accessibilityLabel={label || String(icon)}
        accessibilityState={{ selected: active, disabled }}
        style={({ pressed }) => [
          styles.circle,
          {
            width: size,
            height: size,
            borderRadius: size / 2,
            borderColor: active || solid ? ring : "rgba(255,255,255,0.16)",
            backgroundColor: solid ? ring : "rgba(6,14,24,0.55)"
          },
          pressed && !disabled && styles.circlePressed,
          disabled && styles.circleDisabled
        ]}
      >
        <Ionicons
          name={icon}
          size={Math.round(size * 0.42)}
          color={solid ? colors.background : active ? ring : colors.text}
        />
        {badge && badge > 0 ? (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{badge > 99 ? "99+" : badge}</Text>
          </View>
        ) : null}
      </Pressable>
      {label ? (
        <Text style={[styles.circleLabel, (active || solid) && { color: ring }]} numberOfLines={1}>
          {label}
        </Text>
      ) : null}
    </View>
  );
}

/**
 * A chip-styled pill (LIVE badge host, music track, viewer count, connection).
 * Kept generic so the top bar can compose several without bespoke styles.
 */
export function GlassPill({
  children,
  tone = "default",
  style,
  onPress
}: {
  children: ReactNode;
  tone?: GlassTone;
  style?: any;
  onPress?: () => void;
}) {
  const body = (
    <View style={[styles.pill, tone !== "default" && { borderColor: toneColor(tone) }, style]}>{children}</View>
  );
  if (!onPress) return body;
  return (
    <Pressable onPress={onPress} accessibilityRole="button">
      {body}
    </Pressable>
  );
}

/**
 * Slide-up glass bottom sheet. Backdrop dismiss, drag handle, title row, and a
 * scrollable body. Advanced live tools (guests, comments, music, share, more)
 * all reveal through this so the stage stays uncluttered.
 */
export function LiveBottomSheet({
  visible,
  onClose,
  title,
  subtitle,
  accent = colors.accent,
  children,
  maxHeightRatio = 0.72
}: {
  visible: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  accent?: string;
  children: ReactNode;
  maxHeightRatio?: number;
}) {
  const insets = useSafeAreaInsets();
  const anim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      anim.setValue(0);
      Animated.timing(anim, {
        toValue: 1,
        duration: 240,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true
      }).start();
    }
  }, [visible, anim]);

  const translateY = anim.interpolate({ inputRange: [0, 1], outputRange: [420, 0] });

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose} statusBarTranslucent>
      <KeyboardAvoidingView
        style={styles.sheetRoot}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <Pressable style={StyleSheet.absoluteFill} accessibilityLabel="Dismiss" onPress={onClose} />
        <Animated.View
          style={[
            styles.sheet,
            { maxHeight: `${Math.round(maxHeightRatio * 100)}%`, paddingBottom: insets.bottom + 16, transform: [{ translateY }] }
          ]}
        >
          <LinearGradient
            colors={["rgba(13,25,40,0.98)", "rgba(6,13,23,0.99)"]}
            style={StyleSheet.absoluteFill}
          />
          <View style={[styles.sheetHandle, { backgroundColor: accent }]} />
          <View style={styles.sheetHeader}>
            <View style={{ flex: 1 }}>
              <Text style={styles.sheetTitle}>{title}</Text>
              {subtitle ? <Text style={styles.sheetSubtitle}>{subtitle}</Text> : null}
            </View>
            <Pressable onPress={onClose} accessibilityRole="button" accessibilityLabel="Close" style={styles.sheetClose}>
              <Ionicons name="close" size={20} color={colors.text} />
            </Pressable>
          </View>
          <ScrollView
            style={styles.sheetScroll}
            contentContainerStyle={styles.sheetScrollContent}
            showsVerticalScrollIndicator={false}
            keyboardDismissMode="interactive"
            keyboardShouldPersistTaps="handled"
          >
            {children}
          </ScrollView>
        </Animated.View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

/** A single tappable tool row inside the "More" sheet grid. */
export function ToolTile({
  icon,
  label,
  hint,
  tone = "default",
  onPress
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  hint?: string;
  tone?: GlassTone;
  onPress?: () => void;
}) {
  const color = toneColor(tone === "default" ? "creator" : tone);
  return (
    <Pressable style={({ pressed }) => [styles.tool, pressed && styles.toolPressed]} onPress={onPress} accessibilityRole="button" accessibilityLabel={label}>
      <View style={[styles.toolIcon, { borderColor: color }]}>
        <Ionicons name={icon} size={22} color={color} />
      </View>
      <Text style={styles.toolLabel} numberOfLines={1}>
        {label}
      </Text>
      {hint ? (
        <Text style={styles.toolHint} numberOfLines={1}>
          {hint}
        </Text>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  circleWrap: {
    alignItems: "center",
    gap: 5
  },
  circle: {
    alignItems: "center",
    borderWidth: 1,
    justifyContent: "center"
  },
  circlePressed: {
    opacity: 0.7,
    transform: [{ scale: 0.94 }]
  },
  circleDisabled: {
    opacity: 0.4
  },
  circleLabel: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "700"
  },
  badge: {
    alignItems: "center",
    backgroundColor: colors.danger,
    borderRadius: 999,
    justifyContent: "center",
    minWidth: 18,
    paddingHorizontal: 4,
    height: 18,
    position: "absolute",
    right: -3,
    top: -3
  },
  badgeText: {
    color: "#fff",
    fontSize: 10,
    fontWeight: "900"
  },
  pill: {
    alignItems: "center",
    backgroundColor: "rgba(6,14,24,0.55)",
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 6
  },
  sheetRoot: {
    backgroundColor: "rgba(2,5,12,0.55)",
    flex: 1,
    justifyContent: "flex-end"
  },
  sheet: {
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    borderColor: "rgba(121,210,255,0.14)",
    borderWidth: 1,
    overflow: "hidden",
    paddingHorizontal: 20,
    paddingTop: 10,
    width: "100%"
  },
  sheetHandle: {
    alignSelf: "center",
    borderRadius: 999,
    height: 4,
    marginBottom: 12,
    opacity: 0.7,
    width: 42
  },
  sheetHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    marginBottom: 12
  },
  sheetTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  sheetSubtitle: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "600",
    marginTop: 2
  },
  sheetClose: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.08)",
    borderRadius: 999,
    height: 34,
    justifyContent: "center",
    width: 34
  },
  sheetScroll: {
    flexGrow: 0
  },
  sheetScrollContent: {
    gap: 10,
    paddingBottom: 8
  },
  tool: {
    alignItems: "center",
    gap: 6,
    paddingVertical: 12,
    width: "25%"
  },
  toolPressed: {
    opacity: 0.6
  },
  toolIcon: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.05)",
    borderRadius: 16,
    borderWidth: 1,
    height: 54,
    justifyContent: "center",
    width: 54
  },
  toolLabel: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800",
    textAlign: "center"
  },
  toolHint: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "600",
    textAlign: "center"
  }
});
